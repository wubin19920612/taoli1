"""No external sockets: even an accidental production request fails the suite."""
import base64
import hashlib
import hmac
import json
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from tools.fill_sim.core import Book, Market, Spec, dec
from tools.crossex_exec.security import Credentials, load_env
from tools.crossex_exec.transport import APIError, Transport, gate_sign, bitget_sign
from tools.crossex_exec.adapters import CrossEx, Bitget
from tools.crossex_exec.storage import Store
from tools.crossex_exec.policy import Instrument, review, exit_reason, risk_decision
from tools.crossex_exec.execution import Executor


def setUpModule():
    original = socket.socket.connect
    resolve = socket.getaddrinfo
    def local_only(sock, address):
        if address[0] not in ('127.0.0.1', '::1'):
            raise AssertionError('external_network_forbidden')
        return original(sock, address)
    def local_dns(host, *args, **kwargs):
        if host not in ('127.0.0.1','::1','localhost'):
            raise AssertionError('external_dns_forbidden')
        return resolve(host,*args,**kwargs)
    global network_guard, dns_guard
    network_guard = patch.object(socket.socket, 'connect', local_only)
    dns_guard=patch.object(socket,'getaddrinfo',local_dns)
    network_guard.start()
    dns_guard.start()


def tearDownModule():
    dns_guard.stop()
    network_guard.stop()


def instrument(ex='binance'):
    m = Market(ex, 'future', 'BTCUSDT')
    return Instrument(m, Spec('BTC', 'USDT', '1', '.001', '.001', '5'),
                      'BTCUSDT' if ex == 'bitget' else 'BINANCE_FUTURE_BTC_USDT', '.01')


def intent(cid='ce-test', ex='binance', side='buy', reduce=False, quantity='1'):
    return dict(cid=cid, instrument=instrument(ex).dump(), side=side,
                quantity=quantity, price='100.00', reduce_only=reduce, position_side='long',
                pair_id='p1', created_at=time.time(), estimate='100')


class FakeVenue:
    """Models asynchronous ACK, terminal IOC and delayed/ambiguous responses."""
    def __init__(self, venue):
        self.venue, self.orders, self.calls = venue, {}, []
        self.fill = '1'
        self.timeout_once = self.reject = self.not_found = False
        self.never_terminal = False
        self.reject_reduce = False
        self.barrier = None
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def do_DELETE(self):
                self.handle_request()

            def handle_request(self):
                path = urlsplit(self.path)
                body = self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode()
                data = json.loads(body) if body else {}
                owner.calls.append((self.command, path.path, data, dict(self.headers)))
                # Independent verification of exact bytes, path, timestamp and query.
                if owner.venue == 'gate':
                    pre = '\n'.join([self.command, path.path, path.query,
                                     hashlib.sha512(body.encode()).hexdigest(), self.headers['Timestamp']])
                    expected = hmac.new(b'test-secret', pre.encode(), hashlib.sha512).hexdigest()
                    valid = self.headers['SIGN'] == expected
                else:
                    pre = self.headers['ACCESS-TIMESTAMP'] + self.command + self.path + body
                    expected = base64.b64encode(hmac.new(b'test-secret', pre.encode(), hashlib.sha256).digest()).decode()
                    valid = self.headers['ACCESS-SIGN'] == expected and self.headers['ACCESS-PASSPHRASE'] == 'test-pass'
                if not valid:
                    return self.reply(401, {'label': 'INVALID_SIGNATURE', 'code': '40009'})
                if self.command == 'POST' and (path.path.endswith('/orders') or path.path.endswith('/place-order')):
                    cid = data.get('text', data.get('clientOid'))
                    if cid in owner.orders:
                        return self.reply(400, {'label': 'TRADE_ORDER_DUPLICATE_ERROR', 'code': '45034'})
                    if owner.reject:
                        return self.reply(400, {'label': 'TRADE_INVALID_ORDER_QTY', 'code': '40017',
                                               'message': 'test-secret test-pass test-key'})
                    reduce = data.get('reduce_only')=='true' or data.get('reduceOnly')=='YES' or data.get('tradeSide')=='close'
                    if reduce and owner.reject_reduce:
                        return self.reply(400, {'label':'TRADE_INVALID_ORDER_QTY','code':'40017'})
                    fill = min(dec(owner.fill),dec(data.get('qty',data.get('size'))))
                    owner.orders[cid] = dict(id=str(len(owner.orders)+1), polls=0, data=data, fill=str(fill))
                    if owner.barrier and not reduce:
                        owner.barrier.wait(timeout=2)
                    if owner.timeout_once:
                        owner.timeout_once = False
                        return self.reply(503, {'message': 'test-secret test-pass test-key'})
                    return self.reply(200, {'order_id': owner.orders[cid]['id'], 'text': cid} if owner.venue == 'gate'
                                      else {'orderId': owner.orders[cid]['id'], 'clientOid': cid})
                if 'history_trades' in path.path or path.path.endswith('/fills'):
                    rows = []
                    for cid, order in owner.orders.items():
                        if owner.venue == 'gate':
                            rows.append(dict(transaction_id='t'+order['id'], order_id=order['id'], text=cid,
                                             symbol=order['data']['symbol'], qty=order['fill'], price='100',
                                             fee='.02', fee_coin='USDT', create_time='1700000000000'))
                        else:
                            rows.append(dict(tradeId='t'+order['id'], orderId=order['id'], symbol='BTCUSDT',
                                             baseVolume=order['fill'], price='100', feeDetail=[{'feeCoin':'USDT','totalFee':'-.02'}],
                                             cTime='1700000000000'))
                    return self.reply(200, rows if owner.venue == 'gate' else {'fillList':rows, 'endId':''})
                query = parse_qs(path.query)
                identity = path.path.rsplit('/', 1)[-1] if owner.venue == 'gate' else query.get('orderId', query.get('clientOid', ['']))[0]
                found = next(((cid, o) for cid, o in owner.orders.items() if identity in (cid, o['id'])), None)
                if found is None or owner.not_found:
                    return self.reply(404, {'label':'TRADE_ORDER_NOT_FOUND_ERROR', 'code':'43001'})
                cid, order = found
                order['polls'] += 1
                terminal = order['polls'] > 1 and not owner.never_terminal
                if owner.venue == 'gate':
                    result = dict(order_id=order['id'], text=cid, symbol=order['data']['symbol'],
                                  state='CANCELLED' if terminal else 'OPEN', executed_qty=order['fill'] if terminal else '0')
                else:
                    result = dict(orderId=order['id'], clientOid=cid, symbol='BTCUSDT',
                                  state='canceled' if terminal else 'live', baseVolume=order['fill'] if terminal else '0')
                self.reply(200, result)

            def reply(self, status, data):
                if owner.venue == 'bitget' and status == 200:
                    data = {'code':'00000', 'data':data}
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                    pass  # The deadline tests intentionally abandon responses.
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class SignatureTests(unittest.TestCase):
    def test_gate_official_get_vector(self):
        self.assertEqual(gate_sign('secret', 'GET', '/api/v4/futures/orders',
                                  'contract=BTC_USD&status=finished&limit=50', '', '1541993715'),
                         '55f84ea195d6fe57ce62464daaa7c3c02fa9d1dde954e4c898289c9a2407a3d6fb3faf24deff16790d726b66ac9f74526668b13bd01029199cc4fcc522418b8a')

    def test_gate_official_post_vector(self):
        body = '{"contract":"BTC_USD","type":"limit","size":100,"price":6800,"time_in_force":"gtc"}'
        self.assertEqual(gate_sign('secret', 'POST', '/api/v4/futures/orders', '', body, '1541993715'),
                         'eae42da914a590ddf727473aff25fc87d50b64783941061f47a3fdb92742541fc4c2c14017581b4199a1418d54471c269c03a38d788d802e2c306c37636389f0')

    def test_bitget_document_preimage(self):
        # Official preimage, independently chosen synthetic secret; docs give no output digest.
        pre = '16273667805456GET/api/mix/v2/market/depth?limit=20&symbol=BTCUSDT'
        expected = base64.b64encode(hmac.new(b'secret', pre.encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(bitget_sign('secret', 'GET', '/api/mix/v2/market/depth',
                                    'limit=20&symbol=BTCUSDT', '', '16273667805456'), expected)


class PolicyTests(unittest.TestCase):
    def test_review_common_step_funding_and_limits(self):
        a, b = instrument(), instrument('bitget')
        a.spec = Spec('BTC','USDT','1','.003','.003','5')
        b.spec = Spec('BTC','USDT','1','.002','.002','5')
        books = [Book([['99','10']], [['100','10']], 1000), Book([['102','10']], [['103','10']], 1000)]
        funding = [dict(rate='.0001', next=5000, interval=28800, captured_at=1000, history_ok=True, max_abs_24h='.001')]*2
        result = review([a,b], books, ['.0002','.0006'], funding, ['5000000']*2, 1000)
        self.assertEqual(dec(result['quantity']) % dec('.006'), 0)
        self.assertEqual(result['limits'], ['100.03','101.96'])
        for field, value in [('next', 1100), ('max_abs_24h', '.006'), ('history_ok', False)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                review([a,b], books, ['.0002','.0006'], [dict(funding[0], **{field:value}), funding[1]], ['5000000']*2, 1000)

    def test_close_conditions(self):
        base = dict(age=100, spread=90, entry_spread=100, pnl=0, notional=100, next_funding=2000, stale_age=0)
        for updates, expected in [({'spread':50},'converged'), ({'spread':150},'adverse_spread'),
                                  ({'pnl':-1},'loss_limit'), ({'age':7200},'max_age'),
                                  ({'next_funding':299},'funding_exit'), ({'stale_age':60},'stale_exit')]:
            with self.subTest(expected=expected):
                self.assertEqual(exit_reason(**dict(base, **updates)), expected)
        self.assertIsNone(exit_reason(**base))

    def test_all_breakers(self):
        base = dict(daily=0, total=0, margins=[4,4], single_failures=0, rejected=0, attempted=10, reconciled=True)
        for updates, expected in [({'daily':-20},'STOP'), ({'total':-100},'HALTED'),
                                  ({'margins':[4,2.9]},'STOP'), ({'margins':[1.9,4]},'FLATTEN'),
                                  ({'single_failures':3},'STOP'), ({'rejected':4},'STOP'),
                                  ({'reconciled':False},'STOP'), ({'margins':[None,4]},'STOP')]:
            with self.subTest(updates=updates):
                self.assertEqual(risk_decision(**dict(base, **updates)), expected)
        self.assertEqual(risk_decision(**base), 'RUN')


class HTTPExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'exec.db')
        self.gate, self.bg = FakeVenue('gate'), FakeVenue('bitget')
        cred = Credentials('test-key','test-secret','test-pass')
        self.cross = CrossEx(Transport('gate', cred, base=self.gate.url, live=True, local_test=True))
        self.bitget = Bitget(Transport('bitget', cred, base=self.bg.url, live=True, local_test=True), 'one_way_mode')
        self.exec = Executor(self.store, {'crossex':self.cross,'bitget':self.bitget}, live=True, poll_interval=.001, confirm_timeout=3)

    def tearDown(self):
        self.gate.close()
        self.bg.close()
        self.store.close()
        self.tmp.cleanup()

    def test_ack_terminal_and_fills_both_venues(self):
        for ex in ('binance', 'bitget'):
            with self.subTest(ex=ex):
                result = self.exec.execute(intent('ce-'+ex, ex))
                self.assertEqual(result['state'], 'TERMINAL')
                self.assertEqual(result['filled'], '1')
                self.assertEqual(dec(result['fee']), dec('.02'))
                self.assertEqual(result['vwap'], '100')
                self.assertTrue(result['oid'])
                self.assertEqual([r['kind'] for r in self.store.events() if r['cid']==result['cid']],
                                 ['INTENT','SENT','ACK','TERMINAL','FILLS'])

    def test_timeout_queries_before_any_possible_resend(self):
        self.gate.timeout_once = True
        result = self.exec.execute(intent())
        self.assertEqual(result['state'], 'TERMINAL')
        self.assertEqual(sum(c[0]=='POST' for c in self.gate.calls), 1)
        self.assertEqual(self.gate.calls[1][0], 'GET')

    def test_not_found_after_timeout_is_not_absence_proof(self):
        self.gate.timeout_once = self.gate.not_found = True
        result = self.exec.execute(intent())
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(sum(c[0]=='POST' for c in self.gate.calls), 1)

    def test_duplicate_local_id_never_resends(self):
        self.exec.execute(intent())
        count = len(self.gate.calls)
        self.exec.execute(intent())
        self.assertEqual(len(self.gate.calls), count)

    def test_duplicate_server_rejection_is_queried(self):
        for ex,adapter,fake in [('binance',self.cross,self.gate),('bitget',self.bitget,self.bg)]:
            with self.subTest(exchange=ex):
                i = intent('ce-duplicate-'+ex,ex)
                adapter.place(i)
                self.assertEqual(self.exec.execute(i)['state'], 'TERMINAL')
                self.assertEqual(sum(c[0]=='POST' for c in fake.calls), 2)

    def test_partial_fill(self):
        self.bg.fill = '.4'
        result = self.exec.execute(intent(ex='bitget'))
        self.assertEqual(dec(result['filled']), dec('.4'))

    def test_shadow_never_calls_place_or_cancel(self):
        self.exec.live = False
        with (patch.object(self.cross, 'place', side_effect=AssertionError('shadow_place')),
              patch.object(self.cross, 'cancel', side_effect=AssertionError('shadow_cancel'))):
            result = self.exec.execute(intent())
            self.exec.cancel(result)
        self.assertEqual(result['state'], 'SHADOW')
        self.assertFalse(self.gate.calls)

    def test_transport_also_blocks_shadow_writes(self):
        self.cross.http.live = False
        with self.assertRaises(APIError):
            self.cross.place(intent())
        with self.assertRaises(APIError):
            self.cross.cancel(intent())
        self.assertFalse(self.gate.calls)

    def test_bitget_hedge_close_uses_position_direction(self):
        self.bitget.mode = 'hedge_mode'
        self.bitget.place(intent(ex='bitget', side='sell', reduce=True))
        sent = self.bg.calls[-1][2]
        self.assertEqual((sent['side'],sent['tradeSide']), ('buy','close'))
        self.assertNotIn('reduceOnly',sent)

    def test_secrets_are_not_in_db_or_exceptions(self):
        self.gate.reject = True
        self.exec.execute(intent())
        dump = '\n'.join(self.store.db.iterdump())
        for secret in ('test-key', 'test-secret', 'test-pass', self.gate.calls[0][3]['SIGN']):
            self.assertNotIn(secret,dump)
        try:
            self.cross.place(intent('ce-other'))
        except APIError as error:
            self.assertNotIn('test-secret',repr(error))
            self.assertNotIn('test-pass',str(error))

    def test_sent_is_after_admission(self):
        def before(*args):
            self.assertEqual(self.store.order('ce-test')['state'], 'INTENT')
            raise APIError('rate_limited')
        with patch.object(self.cross.http, 'admit', side_effect=before):
            result = self.exec.execute(intent())
        self.assertEqual(result['state'], 'UNSENT')
        self.assertFalse(self.gate.calls)

    def test_nonterminal_deadline_is_bounded_and_remains_unknown(self):
        self.gate.never_terminal=True
        self.exec.confirm_timeout=.08
        start=time.monotonic()
        result=self.exec.execute(intent())
        self.assertEqual(result['state'],'UNKNOWN')
        self.assertLess(time.monotonic()-start,.4)

    def test_terminal_fill_backfill_retries_without_resending(self):
        with patch.object(self.cross,'fills',side_effect=APIError('unknown')):
            result=self.exec.execute(intent())
        self.assertTrue(result['accounting_pending'])
        self.exec.recover()
        self.assertFalse(self.store.order(result['cid'])['accounting_pending'])
        self.assertEqual(sum(c[0]=='POST' for c in self.gate.calls),1)

    def test_foreign_origins_and_plain_http_production_rejected(self):
        cred=Credentials('test-key','test-secret','test-pass')
        for url in ('http://api.gateio.ws','https://example.com','http://127.0.0.1:1234'):
            with self.subTest(url=url),self.assertRaises(ValueError):
                Transport('gate',cred,base=url)

    def test_log_redaction(self):
        import io
        from contextlib import redirect_stdout
        from tools.crossex_exec.__main__ import emit
        self.exec.execute(intent())
        signature=self.gate.calls[0][3]['SIGN']
        output=io.StringIO()
        with redirect_stdout(output):
            emit('test',message='test-key test-secret test-pass '+signature)
        for secret in ('test-key','test-secret','test-pass',signature):
            self.assertNotIn(secret,output.getvalue())


class PersistenceTests(unittest.TestCase):
    def test_halted_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'state.db'
            with Store(p) as s:
                s.set_meta('state', 'HALTED')
            with Store(p) as s:
                with self.assertRaisesRegex(ValueError,'HALTED'):
                    s.assert_startable()

    def test_env_permissions_and_no_process_env_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'secrets'
            p.write_text('CROSSEX_LIVE_ENABLED=0\nGATE_API_KEY=\n',encoding='utf8')
            # Windows has no POSIX mode 0600: emulate stat only for parsing test.
            import os
            st = p.stat()
            with patch('tools.crossex_exec.security.os.fstat', return_value=type('Stat',(),{'st_mode':0o100600,'st_uid':getattr(os,'getuid',lambda:0)()})()):
                self.assertEqual(load_env(p)['CROSSEX_LIVE_ENABLED'],'0')
            with patch('tools.crossex_exec.security.os.fstat', return_value=st):
                if st.st_mode & 0o777 != 0o600:
                    with self.assertRaises(ValueError):
                        load_env(p)
