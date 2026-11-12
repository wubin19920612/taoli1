"""Read-only HTTP contract; all sockets are guarded to stay on loopback."""
from contextlib import closing
import errno
import http.client
import json
import sqlite3
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.crossex_exec import api
from tools.crossex_exec.api_data import JournalView, readonly
from tools.crossex_exec.storage import Store
from tools.crossex_exec.security import REDACTOR
from tools.fill_sim.storage import Store as SimStore
from test_exec import instrument, setUpModule, tearDownModule


NOW = 1794384000.0
TOKEN = 'fictional-api-token-7bfaf9'
SUMMARY = {'generated_at', 'mode', 'halted', 'halt_reason', 'stop_file',
           'opening_paused_reason', 'caps', 'accounts', 'today', 'total', 'fees', 'last_error',
           'baseline_ready_pairs', 'structural_spread_pairs', 'cooldown_pairs', 'opening_skip_counts'}
POSITION = {'id', 'symbol', 'opened_at', 'held_seconds', 'legs', 'entry_spread_bps',
            'current_spread_bps', 'unrealized_usd', 'unrealized_bps', 'next_funding_at',
            'stop_distance_bps', 'max_hold_at', 'status', 'baseline_bps', 'excess_bps', 'funding_cost_bps',
            'open_book_skew_ms', 'close_book_skew_ms'}
TRADE = {'id', 'symbol', 'pair', 'opened_at', 'closed_at', 'held_seconds', 'legs',
         'entry_spread_bps', 'fees_usd', 'funding_usd', 'net_usd', 'net_bps', 'exit_reason',
         'sim', 'slippage_bps', 'leg_send_gap_ms', 'leg_fill_gap_ms', 'one_leg_incident',
         'baseline_bps', 'excess_bps', 'funding_cost_bps', 'open_book_skew_ms', 'close_book_skew_ms'}
STATS = {'days', 'n', 'histogram', 'by_symbol', 'by_pair', 'by_exit_reason',
         'by_spread_tier', 'vs_sim'}
# Additive round-eight fields; all original keys and value assertions remain.
SUMMARY |= {'config_version', 'effective_risk_limits'}
SUMMARY |= {'account_snapshot', 'equity_baseline_revision', 'liabilities', 'stop_locks'}
POSITION |= {'config_version', 'config_at_open', 'sizing', 'turnovers_usd'}
TRADE |= {'config_version', 'config_at_open', 'sizing', 'turnovers_usd'}


class APIFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'exec # journal.db'
        self.sim_path = Path(self.tmp.name) / 'sim.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.config = {'CROSSEX_EXEC_API_TOKEN': TOKEN, 'CROSSEX_EXEC_API_BIND': '127.0.0.1',
                       'FILL_SIM_DB': str(self.sim_path),
                       'CROSSEX_STOP_FILE': str(Path(self.tmp.name) / 'STOP')}
        self.view = JournalView(self.path, self.config, clock=lambda: NOW)

    def pair(self, identity='p1', state='CLOSED', closed=NOW-10, net='1.78'):
        p = dict(id=identity, trigger_id='shadow-'+identity, base='BTC', state=state,
                 opened_at=NOW-100, instruments=[instrument().dump(), instrument('bitget').dump()],
                 fill={'buy': {'notional': '100', 'vwap': '100'},
                       'sell': {'notional': '102', 'vwap': '102'}, 'gross_bps': 200},
                 funding=[{'next': NOW+4000}, {'next': NOW+5000}],
                 remaining=['0', '0'] if state == 'CLOSED' else ['1', '1'],
                 last_books=[{'bids': [['101', '5']], 'asks': [['102', '5']], 'received_at': NOW-1},
                             {'bids': [['100', '5']], 'asks': [['101', '5']], 'received_at': NOW-1}],
                 accounting_pending=False, funding_known=True, net_usd=net,
                 exit_reason='converged', single_failure=False)
        if state == 'CLOSED':
            p['closed_at'] = closed
        self.store.pair(p)
        for leg, price in enumerate(('100', '102')):
            self.store.save_order(dict(cid=identity+'-open-'+str(leg), pair_id=identity, leg=leg,
                reduce_only=False, side='buy' if leg == 0 else 'sell', state='TERMINAL',
                filled='1', vwap=price, fee='.05', accounting_pending=False,
                sent_ms=NOW*1000+leg*12, fill_ms=NOW*1000+leg*230))
            if state == 'CLOSED':
                self.store.save_order(dict(cid=identity+'-close-'+str(leg), pair_id=identity, leg=leg,
                    reduce_only=True, side='sell' if leg == 0 else 'buy', state='TERMINAL',
                    filled='1', vwap='101', fee='.06', accounting_pending=False))
        return p

    def sim(self, p, *, event=None, variant='measured', amount=100, net=180):
        with_context = SimStore(self.sim_path)
        try:
            event = event or p['trigger_id']
            row = dict(id=f'{event}:{variant}:{amount}', event_id=event, variant=variant,
                       amount=amount, opened_at=NOW-100, closed_at=NOW-10,
                       markets=[i['market'] for i in p['instruments']],
                       spread_tier='1–2%', fill=p['fill'], close_buy={'notional': '101'},
                       close_sell={'notional': '101'}, net_bps=net, funding_known=True)
            with with_context.transaction():
                with_context.position(row)
        finally:
            with_context.close()

    def get(self, endpoint, **query):
        return self.view.get(endpoint, query)


class DataTests(APIFixture, unittest.TestCase):
    def test_empty_contract_and_unknowns(self):
        s = self.get('summary')
        self.assertEqual(set(s), SUMMARY)
        self.assertEqual(set(s['caps']), {'leg_max_usd', 'total_max_usd', 'total_open_usd',
                                          'equity_drop_max_usd', 'max_pairs', 'open_pairs'})
        self.assertEqual(set(s['accounts']), {'crossex', 'bitget'})
        self.assertEqual(set(s['accounts']['crossex']),
                         {'equity_usd', 'baseline_usd', 'drop_usd', 'margin_multiple', 'ok', 'error'})
        self.assertIsNone(s['accounts']['crossex']['equity_usd'])
        self.assertIsNone(s['accounts']['crossex']['ok'])
        self.assertEqual(s['today'], dict(net_usd=0, fees_usd=0, funding_usd=0,
                                         trades=0, wins=0, daily_loss_limit_usd=20))
        self.assertEqual(s['total'], dict(net_usd=0, fees_usd=0, funding_usd=0,
                                         trades=0, wins=0, total_loss_limit_usd=100))
        self.assertEqual(self.get('positions'), [])
        self.assertEqual(self.get('trades'), [])
        stats = self.get('stats')
        self.assertEqual(set(stats), STATS)
        self.assertEqual(len(stats['histogram']), 16)
        self.assertIsNone(stats['vs_sim']['one_leg_rate'])

    def test_positions_use_fills_and_executable_books_not_intent_or_mark(self):
        p = self.pair(state='OPEN')
        row, = self.get('positions')
        self.assertEqual(set(row), POSITION)
        self.assertEqual(row['symbol'], 'BTCUSDT')
        self.assertEqual(row['status'], 'open')
        self.assertEqual(row['held_seconds'], 100)
        self.assertAlmostEqual(row['entry_spread_bps'], 200)
        self.assertAlmostEqual(row['current_spread_bps'], 0)
        self.assertEqual(row['unrealized_usd'], 2)
        self.assertEqual(row['unrealized_bps'], 200)
        self.assertAlmostEqual(row['stop_distance_bps'], 250)
        self.assertTrue(row['opened_at'].endswith('Z'))
        self.assertTrue(row['next_funding_at'].endswith('Z'))
        self.assertTrue(row['max_hold_at'].endswith('Z'))
        for leg in row['legs']:
            self.assertEqual(set(leg), {'exchange', 'route', 'side', 'qty', 'entry_price',
                                        'notional_usd', 'mark_price', 'raw_symbol', 'native_base',
                                        'quantity_multiplier', 'mapping_id', 'native_qty', 'native_entry_price'})
            self.assertIsNone(leg['mark_price'])
        p['last_books'][0]['received_at'] = NOW-61
        self.store.pair(p)
        row, = self.get('positions')
        self.assertIsNone(row['current_spread_bps'])
        self.assertIsNone(row['unrealized_usd'])

    def test_statuses_shadow_and_unknown_accounting(self):
        for state, status in [('OPENING', 'opening'), ('CLOSING', 'closing'), ('UNKNOWN', 'one_leg')]:
            self.pair(state=state)
            self.assertEqual(self.get('positions')[0]['status'], status)
        p = self.pair(state='OPEN')
        p['remaining'] = ['1', '.4']
        self.store.pair(p)
        self.assertEqual(self.get('positions')[0]['status'], 'one_leg')
        self.pair(state='SHADOW')
        self.assertEqual(self.get('positions'), [])
        self.assertEqual(self.get('trades'), [])
        p = self.pair()
        p.update(funding_known=False, accounting_pending=True, net_usd=None)
        self.store.pair(p)
        order = self.store.order('p1-open-0')
        order.update(accounting_pending=True, fee=None, vwap=None)
        self.store.save_order(order)
        row, = self.get('trades')
        for field in ('net_usd', 'net_bps', 'fees_usd', 'funding_usd'):
            self.assertIsNone(row[field])
        self.assertIsNone(self.get('summary')['total']['net_usd'])

    def test_trade_contract_actual_vwap_fees_gaps_and_sim_identity(self):
        p = self.pair()
        self.sim(p, event='different-shadow', net=999)
        self.sim(p, amount=300, net=777)
        self.sim(p, variant='plus_1s', net=888)
        self.assertIsNone(self.get('trades')[0]['sim'])
        self.sim(p)
        row, = self.get('trades')
        self.assertEqual(set(row), TRADE)
        self.assertEqual(row['pair'], 'binance|bitget')
        self.assertEqual(row['held_seconds'], 90)
        self.assertAlmostEqual(row['fees_usd'], .22)
        self.assertEqual(row['net_bps'], 178)
        self.assertEqual(row['funding_usd'], 0)
        self.assertEqual(row['sim'], dict(net_bps=180, gross_bps=200))
        self.assertEqual(row['slippage_bps'], -2)
        self.assertEqual(row['leg_send_gap_ms'], 12)
        self.assertEqual(row['leg_fill_gap_ms'], 230)
        for leg in row['legs']:
            self.assertEqual(set(leg), {'exchange', 'side', 'entry_vwap', 'exit_vwap', 'qty',
                                        'raw_symbol','native_base','quantity_multiplier','mapping_id',
                                        'native_qty','native_entry_price','native_exit_vwap'})
        self.assertEqual(row['legs'][0]['entry_vwap'], 100)
        self.assertEqual(row['legs'][0]['exit_vwap'], 101)

    def test_sim_archive_unavailable_and_market_mismatch(self):
        p = self.pair()
        self.sim(p)
        archive = self.sim_path.with_name('sim-2026-11-10.db')
        self.sim_path.rename(archive)
        self.assertIsNotNone(self.get('trades')[0]['sim'])
        with closing(sqlite3.connect(archive)) as conn, conn:
            row = json.loads(conn.execute('SELECT data FROM positions').fetchone()[0])
            row['markets'][0]['raw_symbol'] = 'OTHERUSDT'
            conn.execute('UPDATE positions SET data=?', (json.dumps(row),))
        self.assertIsNone(self.get('trades')[0]['sim'])
        self.config['FILL_SIM_DB'] = str(Path(self.tmp.name) / 'missing.db')
        view = JournalView(self.path, self.config, clock=lambda: NOW)
        self.assertIsNone(view.get('trades', {})[0]['sim'])
        self.assertFalse(Path(self.config['FILL_SIM_DB']).exists())

    def test_summary_utc_totals_caps_equity_margin_and_ambiguous_fees(self):
        self.pair()
        self.pair('old', closed=NOW-86400, net='-2')
        self.pair('active', state='OPEN')
        self.store.set_meta('live_mode', True)
        self.store.set_meta('account_equity_baseline', dict(crossex='1000', bitget='30000'))
        self.store.set_meta('account_equity_current', dict(crossex='990', bitget='30001'))
        self.store.set_meta('actual_fees', {'at': NOW, 'rates': {
            'binance|future|BTCUSDT': '.00048', 'bitget|future|BTCUSDT': '.0006'}})
        self.store.event('RECONCILE', {'margins': ['4', '1e30'], 'risk': 'RUN', 'matched': True})
        self.store.db.execute('UPDATE events SET at=?', (NOW,))
        s = self.get('summary')
        self.assertEqual(s['mode'], 'live')
        self.assertEqual(s['today']['trades'], 1)
        self.assertEqual(s['total']['trades'], 2)
        self.assertAlmostEqual(s['total']['net_usd'], -.22)
        self.assertEqual(s['accounts']['crossex']['drop_usd'], 10)
        self.assertTrue(s['accounts']['crossex']['ok'])
        self.assertEqual(s['accounts']['crossex']['margin_multiple'], 4)
        self.assertIsNone(s['accounts']['bitget']['margin_multiple'])
        self.assertEqual(s['caps']['open_pairs'], 1)
        self.assertEqual(s['caps']['total_open_usd'], 202)
        self.assertEqual(s['fees'], dict(crossex_taker=.00048, bitget_taker=.0006))
        self.store.set_meta('actual_fees', {'at': NOW, 'rates': {
            'binance|future|BTCUSDT': '.00048', 'okx|future|BTC-USDT-SWAP': '.0003'}})
        self.assertIsNone(self.get('summary')['fees']['crossex_taker'])
        Path(self.config['CROSSEX_STOP_FILE']).touch()
        self.assertEqual(self.get('summary')['opening_paused_reason'], 'STOP')
        self.store.set_meta('state', 'HALTING')
        self.store.set_meta('stop_reason', 'account_equity_drop')
        self.assertTrue(self.get('summary')['halted'])

    def test_pagination_stats_boundaries_and_exit_mapping(self):
        for i, (net, reason) in enumerate([('-3', 'adverse_spread'), ('-.25', 'loss_limit'),
                                           ('0', 'max_age'), ('3', 'funding_exit')]):
            p = self.pair('p'+str(i), closed=NOW-10+i, net=net)
            p['exit_reason'] = reason
            self.store.pair(p)
        self.pair('outside-window', closed=NOW-8*86400)
        page = self.get('trades', limit='2')
        self.assertEqual([r['id'] for r in page], ['p3', 'p2'])
        self.assertEqual([r['id'] for r in self.get('trades', before=page[-1]['closed_at'], limit='2')],
                         ['p1', 'p0'])
        self.assertEqual(page[0]['exit_reason'], 'pre_funding')
        self.assertEqual(page[1]['exit_reason'], 'max_hold')
        s = self.get('stats', days='7')
        self.assertEqual(s['n'], 4)
        self.assertEqual(s['histogram'][0], dict(lo=-200, hi=-175, count=1))
        self.assertEqual(s['histogram'][-1]['count'], 1)
        self.assertEqual(s['histogram'][7]['count'], 1)
        self.assertEqual(s['histogram'][8]['count'], 1)
        self.assertEqual(s['by_symbol'][0]['key'], 'BTCUSDT')
        self.assertEqual(set(s['by_symbol'][0]), {'key', 'n', 'net_usd', 'mean_bps', 'win_rate'})
        self.assertEqual(set(s['vs_sim']), {'n', 'slippage_bps_p50', 'slippage_bps_p90',
                         'leg_fill_gap_ms_p50', 'leg_fill_gap_ms_p90', 'one_leg_rate'})
        self.assertEqual(s['vs_sim']['n'], 0)

    def test_sim_statistics_percentiles_and_original_event_tier(self):
        for index, net in enumerate(('1.7', '1.78', '1.82')):
            p = self.pair('s'+str(index), net=net)
            self.sim(p, net=180)
        s = self.get('stats')
        self.assertEqual(s['vs_sim']['n'], 3)
        self.assertAlmostEqual(s['vs_sim']['slippage_bps_p50'], -2)
        self.assertAlmostEqual(s['vs_sim']['slippage_bps_p90'], -8.4)
        self.assertEqual(s['vs_sim']['leg_fill_gap_ms_p50'], 230)
        self.assertEqual(s['vs_sim']['leg_fill_gap_ms_p90'], 230)
        self.assertEqual(s['vs_sim']['one_leg_rate'], 0)
        self.assertEqual([r['key'] for r in s['by_spread_tier']], ['1–2%'])
        self.assertEqual(s['by_spread_tier'][0]['n'], 3)

    def test_sim_corruption_duplicate_and_open_result_are_null(self):
        p = self.pair()
        self.sim(p)
        with closing(sqlite3.connect(self.sim_path)) as conn, conn:
            conn.execute('UPDATE positions SET closed_at=NULL')
        self.assertIsNone(self.get('trades')[0]['sim'])
        self.sim(p)
        archive = self.sim_path.with_name('sim-2026-11-10.db')
        with closing(sqlite3.connect(self.sim_path)) as source, closing(sqlite3.connect(archive)) as target:
            source.backup(target)
        self.assertIsNone(self.get('trades')[0]['sim'])
        with closing(sqlite3.connect(self.sim_path)) as conn, conn:
            conn.execute("UPDATE positions SET data='not json'")
        view = JournalView(self.path, dict(self.config, FILL_SIM_DB=str(Path(self.tmp.name)/'broken.db')))
        Path(view.sim_path).write_bytes(b'invalid sqlite file')
        self.assertIsNone(view.get('trades', {})[0]['sim'])

    def test_missing_order_and_position_values_are_explicit_nulls(self):
        self.store.pair(dict(id='opening', state='OPENING'))
        row, = self.get('positions')
        self.assertEqual(set(row), POSITION)
        self.assertEqual(row['status'], 'opening')
        for field in ('symbol', 'opened_at', 'held_seconds', 'entry_spread_bps', 'next_funding_at'):
            self.assertIsNone(row[field])
        self.assertEqual(len(row['legs']), 2)
        self.store.pair(dict(id='opening', state='CLOSED', closed_at=NOW))
        row, = self.get('trades')
        self.assertEqual(set(row), TRADE)
        self.assertIsNone(row['sim'])
        self.assertIsNone(row['net_usd'])
        self.assertIsNone(row['legs'][0]['qty'])

    def test_stale_account_is_not_healthy_and_expired_fees_are_null(self):
        self.store.set_meta('account_equity_current', dict(crossex='1000', bitget='30000'))
        self.store.event('RECONCILE', dict(margins=['4', '4'], risk='RUN'))
        self.store.db.execute('UPDATE events SET at=?', (NOW-61,))
        self.store.set_meta('actual_fees', {'at': NOW-3601, 'rates': {'bitget|future|BTCUSDT': '.0006'}})
        row = self.get('summary')
        self.assertFalse(row['accounts']['crossex']['ok'])
        self.assertEqual(row['accounts']['crossex']['error'], 'account_snapshot_stale')
        self.assertIsNone(row['accounts']['crossex']['margin_multiple'])
        self.assertIsNone(row['fees']['bitget_taker'])

    def test_database_writes_and_attach_are_denied_even_after_pragma(self):
        self.pair()
        self.sim(self.store.pairs()[0])
        for path in (self.path, self.sim_path):
            with readonly(path) as conn:
                self.assertTrue(conn.execute('SELECT count(*) FROM positions' if path == self.sim_path
                                             else 'SELECT count(*) FROM pairs').fetchone())
                for sql in ('CREATE TABLE forbidden(x)', 'PRAGMA query_only=OFF',
                            'DELETE FROM meta', "ATTACH DATABASE ':memory:' AS writable"):
                    with self.subTest(path=path, sql=sql), self.assertRaises(sqlite3.DatabaseError):
                        conn.execute(sql)
        missing = Path(self.tmp.name) / 'missing.db'
        with self.assertRaises(sqlite3.OperationalError):
            with readonly(missing):
                pass
        self.assertFalse(missing.exists())

    def test_ro_snapshot_does_not_lock_writer_or_share_store_lock(self):
        self.pair()
        finished = threading.Event()
        with readonly(self.path) as reader:
            reader.execute('BEGIN')
            reader.execute('SELECT * FROM pairs').fetchall()
            def write():
                self.store.set_meta('writer-progress', True)
                finished.set()
            writer = threading.Thread(target=write)
            writer.start()
            self.assertTrue(finished.wait(1), 'WAL reader blocked execution writer')
            writer.join(1)
        with self.store.lock:
            with patch.object(self.store, 'all_orders', side_effect=AssertionError('shared writer')):
                self.assertEqual(len(self.get('trades')), 1)


class HTTPTests(APIFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.logs = []
        self.real_server = api._Server

    def start(self):
        # Production is fixed at 8795. Test only the transport on an ephemeral port.
        def local_server(address, *args, **kwargs):
            return self.real_server(('127.0.0.1', 0), *args, **kwargs)
        with patch.object(api, '_Server', side_effect=local_server):
            service = api.start_api(self.config, self.path, logger=lambda kind, **kw: self.logs.append((kind, kw)))
            self.assertTrue(service.ready.wait(2))
        self.addCleanup(service.close)
        self.assertEqual(len(service.addresses), 1)
        return service

    def request(self, service, path='/api/v1/summary', token=TOKEN, method='GET', headers=None):
        conn = http.client.HTTPConnection(*service.addresses[0], timeout=3)
        try:
            conn.request(method, path, headers=headers if headers is not None else
                         ({'X-Api-Token': token} if token is not None else {}))
            response = conn.getresponse()
            return response.status, response.read().decode(), dict(response.getheaders())
        finally:
            conn.close()

    def test_http_contract_auth_and_get_only(self):
        self.pair()
        service = self.start()
        for endpoint, schema in [('summary', SUMMARY), ('positions', None), ('trades', TRADE), ('stats', STATS)]:
            status, body, headers = self.request(service, '/api/v1/'+endpoint)
            self.assertEqual(status, 200)
            value = json.loads(body)
            if schema:
                self.assertEqual(set(value[0] if endpoint == 'trades' else value), schema)
            self.assertEqual(headers['Cache-Control'], 'no-store')
        for token in (None, '', 'wrong', TOKEN+'x'):
            self.assertEqual(self.request(service, token=token)[0], 401)
        with patch.object(api.hmac, 'compare_digest', wraps=api.hmac.compare_digest) as compare:
            self.assertEqual(self.request(service)[0], 200)
            compare.assert_called()
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'TRACE', 'WEIRD'):
            self.assertEqual(self.request(service, method=method)[0], 405)
            self.assertEqual(self.request(service, method=method, token=None)[0], 401)
        self.assertEqual(len(self.store.pairs()), 1)

    def test_errors_and_all_responses_never_reflect_secrets(self):
        secret = 'fictional-sensitive-secret-92a'
        self.config['CROSSEX_API_SECRET'] = secret
        self.config['BITGET_API_PASSPHRASE'] = 'fictional-passphrase-83'
        self.config['CROSSEX_API_KEY'] = 'fictional-crossex-key-41'
        self.config['BITGET_API_KEY'] = 'fictional-bitget-key-52'
        self.config['BITGET_API_SECRET'] = 'fictional-bitget-secret-63'
        signature = 'fictional-hmac-signature-74'
        REDACTOR.signature(signature)
        self.pair(identity=signature, state='OPEN')
        p = self.pair(identity=secret)
        p.update(secret=secret, signature='signature-do-not-output', headers={'key': secret})
        self.store.pair(p)
        self.store.set_meta('stop_reason', secret)
        self.store.event('ALERT', {'reason': secret, 'signature': 'signature-do-not-output'})
        service = self.start()
        for endpoint in ('summary', 'positions', 'trades', 'stats', secret):
            status, body, headers = self.request(service, '/api/v1/'+endpoint)
            for value in (secret, TOKEN, signature, 'fictional-passphrase-83', 'signature-do-not-output',
                          'fictional-crossex-key-41', 'fictional-bitget-key-52', 'fictional-bitget-secret-63'):
                self.assertNotIn(value, body+json.dumps(headers))
        with patch.object(JournalView, 'get', side_effect=RuntimeError(secret)):
            self.assertEqual(self.request(service)[0], 503)
            self.assertNotIn(secret, self.request(service)[1])
        self.assertNotIn(secret, json.dumps(self.logs))
        self.assertEqual(self.request(service)[0], 200)

    def test_read_failure_does_not_change_engine_tick_or_halt_state(self):
        from tools.crossex_exec.engine import Engine
        from test_engine import FakeAccounts, FakeMarket
        market = FakeMarket()
        accounts = FakeAccounts(Mock(), Mock(), market)
        engine = Engine(self.store, market, accounts,config={'CROSSEX_RISK_SCOPE':'dedicated_account'}, stop_file=self.config['CROSSEX_STOP_FILE'],
                        clock=lambda: NOW)
        engine.limits.initialize(accounts.snapshot())
        service = self.start()
        with patch.object(JournalView, 'get', side_effect=RuntimeError('fictional-request-crash')):
            for _ in range(3):
                self.assertEqual(self.request(service)[0], 503)
                engine.tick()
        self.assertEqual(engine.last_reconcile, NOW)
        self.assertEqual(engine.risk, 'RUN')
        self.assertIsNone(self.store.meta('state'))
        self.assertTrue(any(e['kind'] == 'RECONCILE' for e in self.store.events()))
        self.assertEqual(self.request(service)[0], 200)

    def test_duplicate_headers_and_parser_errors_do_not_echo_input(self):
        service = self.start()
        conn = http.client.HTTPConnection(*service.addresses[0], timeout=3)
        try:
            conn.putrequest('GET', '/api/v1/summary')
            conn.putheader('X-Api-Token', TOKEN)
            conn.putheader('X-Api-Token', TOKEN)
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 401)
            self.assertNotIn(TOKEN, response.read().decode())
        finally:
            conn.close()
        conn = http.client.HTTPConnection(*service.addresses[0], timeout=3)
        try:
            conn.connect()
            conn.sock.sendall(b'GET /fictional-parser-secret HTTP/wrong\r\n\r\n')
            data = conn.sock.recv(4096)
            self.assertNotIn(b'fictional-parser-secret', data)
        finally:
            conn.close()

    def test_busy_reader_returns_503_and_missing_exec_db_is_not_created(self):
        service = self.start()
        with service.read_slot:
            self.assertEqual(self.request(service)[0], 503)
        missing = Path(self.tmp.name) / 'missing-exec.db'
        service.view.path = missing
        status, body, _ = self.request(service)
        self.assertEqual(status, 503)
        self.assertNotIn(str(missing), body)
        self.assertFalse(missing.exists())

    def test_listener_start_never_performs_reverse_dns(self):
        with patch.object(socket, 'getfqdn', side_effect=AssertionError('reverse DNS forbidden')) as dns:
            service = self.start()
            self.assertEqual(self.request(service)[0], 200)
            dns.assert_not_called()

    def test_query_validation_and_capped_limit(self):
        for i in range(502):
            self.store.pair(dict(id=str(i), base='BTC', state='CLOSED', closed_at=NOW-i))
        service = self.start()
        status, body, _ = self.request(service, '/api/v1/trades?limit=9999')
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)), 500)
        for query in ('trades?limit=0', 'trades?limit=-1', 'trades?limit=bad',
                      'trades?before=2026-11-11', 'trades?before=%27+OR+1=1',
                      'stats?days=0', 'stats?days=abc', 'stats?days=1&days=2', 'summary?unknown=x'):
            self.assertEqual(self.request(service, '/api/v1/'+query)[0], 400)


class LifecycleTests(unittest.TestCase):
    def test_missing_token_never_binds_or_reads_database(self):
        with patch.object(api, '_Server') as server, patch.object(api, 'JournalView') as view:
            for config in ({}, {'CROSSEX_EXEC_API_TOKEN': ''}, {'CROSSEX_EXEC_API_TOKEN': '  '}):
                self.assertIsNone(api.start_api(config, 'does-not-exist.db'))
            server.assert_not_called()
            view.assert_not_called()

    def test_bind_allowlist_fixed_port_and_bridge_fallback(self):
        self.assertEqual(api.parse_bind(None), [('127.0.0.1', 8795), ('172.18.0.1', 8795)])
        self.assertEqual(api.parse_bind('127.0.0.1:8795'), [('127.0.0.1', 8795)])
        for address in ('0.0.0.0', '::', 'localhost', '8.8.8.8', '127.0.0.1:80', ''):
            with self.assertRaises(ValueError):
                api.parse_bind(address)
        loopback = Mock()
        loopback.server_address = ('127.0.0.1', 8795)
        loopback.handle_request.side_effect = SystemExit('private-crash')
        logs = []
        def bind(address, *args):
            if address[0] == '172.18.0.1':
                raise OSError(errno.EADDRNOTAVAIL, 'private-bind-error')
            return loopback
        with patch.object(api, '_Server', side_effect=bind):
            service = api.start_api({'CROSSEX_EXEC_API_TOKEN': TOKEN}, 'unused.db',
                                    logger=lambda kind, **kw: logs.append((kind, kw)))
            self.assertTrue(service.ready.wait(2))
            for thread in service.threads:
                thread.join(2)
            service.close()
        self.assertEqual(service.addresses, [('127.0.0.1', 8795)])
        self.assertIn('api_bind_unavailable', [r[0] for r in logs])
        self.assertNotIn('private-', json.dumps(logs))

    def test_invalid_config_and_startup_failure_are_contained(self):
        with patch.object(api, '_Server') as server:
            self.assertIsNone(api.start_api({'CROSSEX_EXEC_API_TOKEN': TOKEN,
                                            'CROSSEX_EXEC_API_BIND': '0.0.0.0'}, 'unused.db'))
            server.assert_not_called()
        with patch.object(api.APIService, 'start', side_effect=RuntimeError('private-thread-start')):
            self.assertIsNone(api.start_api({'CROSSEX_EXEC_API_TOKEN': TOKEN}, 'unused.db',
                                           logger=Mock(side_effect=RuntimeError('logger failed'))))

    def test_main_loop_survives_api_bind_failure(self):
        from tools.crossex_exec import __main__ as cli
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'exec.db'
            config = {'CROSSEX_DB': str(path), 'CROSSEX_EXEC_API_TOKEN': TOKEN,
                      'CROSSEX_EXEC_API_BIND': '127.0.0.1'}
            fake_fcntl = types.SimpleNamespace(flock=lambda *a: None, LOCK_EX=1, LOCK_NB=2)
            engine = Mock()
            def tick():
                with Store(path) as writer:
                    writer.set_meta('state', 'HALTED')
            engine.tick.side_effect = tick
            with (patch.dict(sys.modules, {'fcntl': fake_fcntl}),
                  patch.object(cli, 'load_env', return_value=config),
                  patch.object(cli, 'Engine', return_value=engine),
                  patch.object(cli, 'MarketData') as market,
                  patch.object(cli, 'Accounts') as accounts,
                  patch.object(cli, 'ShadowAPI') as shadow,
                  patch.object(cli.signal, 'signal'), patch.object(cli.time, 'sleep'),
                  patch.object(cli, 'emit'),
                  patch.object(api, '_Server', side_effect=RuntimeError('private-bind-error'))):
                accounts.return_value.fees.return_value = {}
                shadow.return_value.poll.return_value = []
                self.assertEqual(cli.main(['run', '--env-file', 'fictional']), 0)
                engine.tick.assert_called_once()
                market.return_value.close.assert_called_once()
