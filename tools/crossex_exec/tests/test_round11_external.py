"""External ownership is explicit; every test uses offline account evidence."""
import copy
import json
import http.client
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.fill_sim.core import dec, day
from tools.crossex_exec.storage import Store
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.management import WriteConflict
from test_exec import instrument, intent, setUpModule, tearDownModule
from test_engine import FakeMarket


SYMBOL = 'HYPERLIQUID_FUTURE_io:OAI_USDC'


def identity(symbol=SYMBOL):
    exchange, business, rest = symbol.split('_', 2)
    raw, quote = rest.rsplit('_', 1)
    return dict(route='crossex', exchange_type=exchange, business=business,
                symbol=symbol, quote=quote, dex=raw.split(':')[0] if ':' in raw else '')


def rule(symbol=SYMBOL):
    i = identity(symbol)
    return dict(symbol=symbol, exchange_type=i['exchange_type'], business_type=i['business'],
                tag=i['dex'], state='live', support_cross='true', delist_time='0',
                lot_size='.001', min_size='.001', min_notional='1', max_limit_size='10000', tick_size='.01')


def position(symbol=SYMBOL, qty='1', upnl='0', side='LONG'):
    return dict(symbol=symbol, position_qty=qty, upnl=upnl, position_side=side,
                margin_mode='CROSS', mark_price='100', position_id='external-position')


class ExternalFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'exec.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.now = 1794441600.0
        self.rows = []
        self.book = []
        self.trades = []
        self.bg_book = []
        self.open_orders = []
        self.margin = '4'
        self.equity = '1000'
        self.bitget_equity = '30000'
        self.liabilities = []
        self.activity_complete = True
        self.market = FakeMarket()
        self.cross = SimpleNamespace(open_orders=lambda:copy.deepcopy(self.open_orders))
        self.bg = SimpleNamespace(open_orders=lambda:[])
        self.accounts = SimpleNamespace(cross=self.cross, bitget=self.bg, snapshot=self.snapshot,
            position_snapshot=self.snapshot, activity=self.activity)
        self.engine = Engine(self.store,self.market,self.accounts,clock=lambda:self.now,
            live=True,stop_file=Path(self.tmp.name)/'STOP')
        self.addCleanup(self.engine.close)
        self.catalog()
        self.engine.limits.initialize(self.snapshot())

    def catalog(self, symbols=(SYMBOL, 'HYPERLIQUID_FUTURE_xyz:OAI_USDC', 'BINANCE_FUTURE_BTC_USDT')):
        self.store.set_meta('external_catalog',dict(at=self.now,rules=[rule(s) for s in symbols]))

    def snapshot(self):
        from tools.crossex_exec.accounts import remote_positions
        rows=copy.deepcopy(self.rows)
        positions=remote_positions(rows,[])
        floating={k:str(dec(r['upnl'])) for r in rows for k in remote_positions([r],[])}
        result=dict(positions=positions,upnl=str(sum(map(dec,floating.values()),dec(0))),upnl_by_position=floating,
            margins=[self.margin,'4'],equity_by_venue=dict(crossex=self.equity,bitget=self.bitget_equity),
            equity=str(dec(self.equity)+dec(self.bitget_equity)),notionals={k:'100' for k in positions},
            liabilities=copy.deepcopy(self.liabilities),raw_positions=dict(crossex=rows,bitget=[]),
            raw_accounts=dict(crossex={'assets':[dict(exchange_type='BINANCE',coin='USDT',
                balance=str(dec(self.equity)-sum(map(dec,floating.values()),dec(0))))]},
                bitget={'assets':[dict(coin='USDT',balance=self.bitget_equity)]}))
        if not getattr(self,'independent_funding',False):
            from funding_fixtures import enrich
            enrich(self,result,self.book,self.bg_book,self.trades)
        return result

    def activity(self, start_ms, end_ms):
        def window(rows):
            return copy.deepcopy([r for r in rows if start_ms<=int(r.get('create_time',r.get('ts',0)))<=end_ms])
        from funding_fixtures import histories
        return dict(from_ms=start_ms,to_ms=end_ms,complete=self.activity_complete,
            crossex_book=window(self.book),crossex_trades=window(self.trades),bitget_book=window(self.bg_book),
            **histories(self,start_ms,end_ms))

    def registry(self):
        from tools.crossex_exec.external import ExternalRegistry
        return ExternalRegistry(self.store,clock=lambda:self.now)

    def change(self, registered=True, symbol=SYMBOL, expected=None):
        return self.engine.external_admin(identity=identity(symbol),registered=registered,
            expected_status=self.registry().status() if expected is None else expected,
            reason='user_external_ownership',source='cli')

    def anchor(self):
        return self.engine.rebaseline(confirm=True,reason='verified_external_epoch',source='cli')


class RegistryTests(ExternalFixture, unittest.TestCase):
    def test_exact_identity_both_directions_alias_and_permanent_ban_after_cancel_restart(self):
        result=self.change()
        self.assertTrue(result['record']['registered'])
        for side in ('long','short'):
            row=intent(side= 'buy' if side=='long' else 'sell')
            row['instrument']['symbol']=SYMBOL
            row['instrument']['market']['exchange']='hyperliquid'
            row['instrument']['canonical_base']='OPENAI'
            row['position_side']=side
            for reduce in (False,True):
                row['reduce_only']=reduce
                with self.assertRaisesRegex(ValueError,'external_contract_forbidden'):
                    self.engine.check_order(row)
        self.change(False)
        with Store(self.path) as reopened:
            from tools.crossex_exec.external import ExternalRegistry
            self.assertTrue(ExternalRegistry(reopened).forbidden('crossex',SYMBOL))
            self.assertFalse(ExternalRegistry(reopened).forbidden('crossex','HYPERLIQUID_FUTURE_xyz:OAI_USDC'))
        self.assertEqual(len([e for e in self.store.events() if e['kind']=='EXTERNAL_CHANGED']),2)

    def test_identity_missing_or_dex_disagrees_is_never_normalized(self):
        for field in identity():
            bad=identity(); bad.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.engine.external_admin(identity=bad,registered=True,expected_status=self.registry().status(),reason='x',source='cli')
        bad=identity(); bad['dex']='xyz'
        with self.assertRaises(ValueError):
            self.engine.external_admin(identity=bad,registered=True,expected_status=self.registry().status(),reason='x',source='cli')

    def test_orphan_unknown_intent_terminal_history_pending_and_closed_pair_refused(self):
        symbol='BINANCE_FUTURE_BTC_USDT'
        for state in ('INTENT','SENT','ACK','UNKNOWN','TERMINAL','REJECTED'):
            with self.subTest(state=state):
                row=dict(intent(),state=state,filled='0',accounting_pending=state=='TERMINAL')
                self.store.save_order(row)
                with self.assertRaisesRegex(ValueError,'program_ownership'):
                    self.change(symbol=symbol)
                self.store.db.execute('DELETE FROM orders')
        self.store.pair(dict(id='closed',state='CLOSED',instruments=[instrument().dump()],remaining=['0']))
        with self.assertRaisesRegex(ValueError,'program_ownership'):
            self.change(symbol=symbol)

    def test_unregistration_requires_fresh_remote_flat_and_no_remote_orders(self):
        self.change()
        self.rows=[position()]
        with self.assertRaisesRegex(ValueError,'external_remote_position'):
            self.change(False)
        self.rows=[]
        self.open_orders=[dict(symbol=SYMBOL)]
        with self.assertRaisesRegex(ValueError,'external_remote_order'):
            self.change(False)
        self.open_orders=[]
        original=self.accounts.snapshot
        def delayed():
            value=original(); self.now+=11; return value
        self.accounts.snapshot=delayed
        with self.assertRaisesRegex(ValueError,'account_snapshot_stale'):
            self.change(False)
        self.assertTrue(next(iter(self.registry().records().values()))['registered'])

    def test_optimistic_conflict_and_success_audit_failures_are_atomic(self):
        old=self.registry().status()
        self.change()
        with self.assertRaises(WriteConflict):
            self.change(False,expected=old)
        self.assertTrue(any(e['kind']=='EXTERNAL_CONFLICT' for e in self.store.events()))
        before=self.registry().records()
        with patch.object(self.store,'event',side_effect=sqlite3.OperationalError('audit')):
            with self.assertRaises(sqlite3.OperationalError):
                self.change(False)
            with self.assertRaises(sqlite3.OperationalError):
                self.change(False,expected=old)
        self.assertEqual(before,self.registry().records())

    def test_catalog_change_and_raw_missing_stop_without_losing_snapshot(self):
        self.change()
        self.rows=[position(upnl='-3')]
        self.anchor()
        original=self.snapshot()
        changed=self.store.meta('external_catalog')
        changed['rules'][0]['tick_size']='.1'
        self.store.set_meta('external_catalog',changed)
        self.engine.reconcile()
        self.assertFalse(self.engine.can_open())
        self.assertIn('external_identity_unverifiable',self.store.meta('stop_locks'))
        self.assertEqual(self.store.meta('account_risk_snapshot')['raw_positions'],original['raw_positions'])

    def test_program_flat_halting_reports_external_and_never_places(self):
        self.change(); self.rows=[position()]
        self.store.set_meta('state','HALTING')
        self.engine.flatten_all('test_halt')
        self.assertEqual(self.store.meta('state'),'HALTED')
        status=self.store.meta('flatten_status')
        self.assertTrue(status['program_flat'])
        self.assertFalse(status['account_flat'])
        self.assertIn('crossex|'+SYMBOL+'|long',status['external_positions'])
        self.assertEqual(self.store.all_orders(),[])

    def test_standalone_executor_and_shutdown_cannot_bypass_registry(self):
        self.change(symbol='BINANCE_FUTURE_BTC_USDT')
        from tools.crossex_exec.execution import Executor
        adapter=SimpleNamespace(place=lambda *a,**k:self.fail('forbidden order sent'))
        executor=Executor(self.store,{'crossex':adapter},live=True)
        with self.assertRaisesRegex(ValueError,'external_contract_forbidden'):
            executor.execute(intent())
        self.rows=[position('BINANCE_FUTURE_BTC_USDT')]
        self.engine.flatten_all('shutdown')
        self.assertTrue(self.store.meta('flatten_status')['program_flat'])
        self.assertFalse(self.store.meta('flatten_status')['account_flat'])

    def test_local_pending_prevents_program_flat_report_even_when_remote_empty(self):
        self.change()
        row=dict(intent(),state='UNKNOWN',filled='0',accounting_pending=True)
        self.store.save_order(row)
        self.store.set_meta('state','HALTING')
        with patch.object(self.engine.executor,'recover'):
            self.engine.flatten_all('pending')
        self.assertFalse(self.store.meta('flatten_status')['program_flat'])
        self.assertEqual(self.store.meta('state'),'HALTING')

    def test_rebaseline_and_release_only_external_audit_and_hard_prerequisites(self):
        self.change(); self.rows=[position(upnl='-9')]
        audit=self.anchor()
        self.assertTrue(audit['external_snapshot']['external_positions'])
        self.assertEqual(self.store.meta('account_equity_baseline')['crossex'],'1000')
        self.store.set_meta('state','HALTED'); self.store.set_meta('halt_reason','account_equity_drop')
        audit=self.engine.rebaseline(confirm='account_equity_drop',reason='x',source='cli',release_halt=True)
        self.assertEqual(self.store.meta('state'),'RUNNING')
        self.assertTrue(audit['external_snapshot']['external_positions'])
        for field,value,reason in (('margin','2.9','margin_open_limit'),('liabilities',[{'liability':'1'}],'account_liability')):
            old=getattr(self,field); setattr(self,field,value)
            with self.assertRaisesRegex(ValueError,reason): self.anchor()
            setattr(self,field,old)


class AccountingTests(ExternalFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.symbol='BINANCE_FUTURE_BTC_USDT'
        self.change(symbol=self.symbol)
        self.rows=[position(self.symbol,upnl='10')]
        self.anchor()

    def reconcile(self):
        self.now+=1
        self.engine.reconcile()
        return self.store.meta('account_loss_current')

    def test_profit_loss_both_sides_excluded_from_daily_but_total_preserved(self):
        for upnl,equity in (('40','1030'),('-30','960')):
            with self.subTest(upnl=upnl):
                self.rows=[position(self.symbol,upnl=upnl)]
                self.equity=equity
                loss=self.reconcile()
                self.assertEqual(dec(loss['daily']),0)
                # R14: external total PnL is observation only, like daily PnL.
                self.assertEqual(dec(loss['total']),0)
                self.assertFalse(self.store.meta('position_mismatch'))
                self.assertFalse(self.store.meta('program_projection')['positions'])

    def test_missing_complete_history_unknown_type_and_non_usdt_never_zero(self):
        self.activity_complete=False
        self.reconcile()
        self.assertIn('external_income_unverifiable',self.store.meta('stop_locks'))
        self.assertFalse(self.engine.can_open())
        self.activity_complete=True
        self.book=[dict(id='x',statement_type='OTHER',change='1',coin='USDT',symbol=self.symbol,
                        exchange_type='BINANCE',create_time=str(int(self.now*1000)))]
        self.reconcile()
        self.assertIn('external_income_unverifiable',self.store.meta('stop_locks'))

    def test_equity_and_margin_hard_limits_include_external_catastrophe(self):
        # R14: preserve the halt/never-touch-external assertions with an actual
        # independently proved PROGRAM loss, not the user's external loss.
        self.store.save_order(dict(intent('own',ex='bitget'),state='TERMINAL',filled='0',
            fee='0',leg=1,accounting_pending=False))
        self.bg_book=[dict(id='program-loss',category='USDT-FUTURES',symbol='BTCUSDT',coin='USDT',
            type='CONTRACT_MAIN_SETTLE_FEE_USER_OUT',amount='-150',fee='0',ts=str(int((self.now+.5)*1000)))]
        self.bitget_equity='29850'
        self.rows=[position(self.symbol,upnl='-250')]; self.equity='740'
        self.reconcile()
        self.assertEqual(self.store.meta('state'),'HALTED')
        self.assertFalse(self.store.meta('flatten_status')['account_flat'])
        self.assertFalse(any(o['reduce_only'] for o in self.store.all_orders()))

    def add_trade(self,qty,side,rpnl='0',fee='.1',position_side='LONG'):
        oid='trade-'+str(len(self.trades))
        at=str(int((self.now+.5)*1000))
        self.trades.append(dict(transaction_id=oid,order_id=oid,symbol=self.symbol,exchange_type='BINANCE',
            business_type='FUTURE',fee_coin='USDT',side=side,position_mode='DUAL',position_side=position_side,
            qty=str(qty),price='100',rpnl=rpnl,fee=fee,create_time=at))
        for kind,value in (('TRANSACTION',rpnl),('TRADING_FEE',str(-dec(fee)))):
            self.book.append(dict(id=oid+kind,business_id=oid,statement_type=kind,change=value,
                coin='USDT',symbol=self.symbol,exchange_type='BINANCE',create_time=at))

    def test_increase_partial_close_full_close_and_funding_are_not_current_upnl_offset(self):
        self.add_trade('1','BUY')
        self.rows=[position(self.symbol,qty='2',upnl='12')]; self.equity='1001.9'
        self.assertEqual(dec(self.reconcile()['daily']),0)
        self.add_trade('1','SELL',rpnl='5')
        self.rows=[position(self.symbol,qty='1',upnl='7')]; self.equity='1001.8'
        self.assertEqual(dec(self.reconcile()['daily']),0)
        self.add_trade('1','SELL',rpnl='7')
        self.rows=[]; self.equity='1001.7'
        self.assertEqual(dec(self.reconcile()['daily']),0)
        self.book.append(dict(id='funding',business_id='pos:time',statement_type='FUNDING_FEE',change='-25',
            coin='USDT',symbol=self.symbol,exchange_type='BINANCE',create_time=str(int((self.now+.5)*1000))))
        self.equity='976.7'
        loss=self.reconcile()
        self.assertEqual(dec(loss['daily']),0)
        self.assertEqual(dec(self.store.meta('external_income')['external_contribution']),dec('-23.3'))
        self.assertIsNone(self.store.meta('daily_loss_day'))

    def test_missing_trade_missing_fee_duplicate_and_transfer_fail_closed(self):
        self.add_trade('1','BUY')
        self.rows=[position(self.symbol,qty='2',upnl='10')]
        self.trades=[]
        self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.rows=[position(self.symbol,upnl='10')]; self.book=[]
        for kind in ('TRANSFER_IN','TRANSFER_OUT','AUTO_REPAY','OTHER'):
            self.book=[dict(id='cash',statement_type=kind,change='-10',coin='USDT',symbol='',
                exchange_type='CROSSEX',create_time=str(int((self.now+.5)*1000)))]
            self.reconcile()
            self.assertFalse(self.store.meta('external_income')['verified'])

    def test_own_funding_loss_is_not_erased_by_external_profit(self):
        own=dict(intent(ex='bitget'),state='TERMINAL',filled='0',fee='0',leg=1,accounting_pending=False)
        self.store.save_order(own)
        self.bg_book=[dict(id='bg-funding',category='USDT-FUTURES',symbol='BTCUSDT',coin='USDT',
            type='CONTRACT_MAIN_SETTLE_FEE_USER_OUT',amount='-21',fee='0',ts=str(int((self.now+.5)*1000)))]
        self.rows=[position(self.symbol,upnl='60')]; self.equity='1050'; self.bitget_equity='29979'
        loss=self.reconcile()
        self.assertEqual(dec(loss['daily']),-21)
        self.assertIn('daily_loss',self.store.meta('stop_locks'))

    def test_utc_rollover_and_restart_keep_full_history_and_program_delta(self):
        self.now+=86398
        self.catalog()
        self.rows=[position(self.symbol,upnl='-5')]; self.equity='985'
        self.assertEqual(dec(self.reconcile()['daily']),0)
        self.add_trade('1','SELL',rpnl='-5')
        self.rows=[]; self.equity='984.9'
        self.now+=2
        self.assertEqual(dec(self.reconcile()['daily']),0)
        self.assertTrue(self.store.meta('external_income')['verified'])
        self.engine.close()
        self.engine=Engine(self.store,self.market,self.accounts,clock=lambda:self.now,live=True,
            stop_file=Path(self.tmp.name)/'STOP')
        self.addCleanup(self.engine.close)
        self.assertEqual(dec(self.reconcile()['daily']),0)
        self.assertTrue(self.registry().forbidden('crossex',self.symbol))

    def test_slow_history_and_settlement_during_reads_cannot_certify_stale_snapshot(self):
        original=self.accounts.activity
        def changed(start,end):
            value=original(start,end)
            self.rows=[position(self.symbol,qty='2',upnl='10')]
            return value
        self.accounts.activity=changed
        self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])

    def test_non_usdt_cancellation_of_different_currency_pnl_is_not_zero_proof(self):
        self.change(symbol=SYMBOL)
        self.rows=[position(self.symbol,upnl='-10'),position(SYMBOL,upnl='10')]
        self.anchor()
        self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])

    def test_program_trade_cash_must_match_immutable_fill_journal(self):
        own=dict(intent(ex='bitget'),state='TERMINAL',filled='0',fee='0',leg=1,accounting_pending=False)
        self.store.save_order(own)
        self.bg_book=[dict(id='unrecorded',category='USDT-FUTURES',symbol='BTCUSDT',coin='USDT',
            type='CLOSE_LONG',amount='-9',fee='-.1',ts=str(int((self.now+.5)*1000)))]
        self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])

    def test_rebaseline_offsets_verified_program_funding_without_erasing_loss_latch(self):
        own=dict(intent(ex='bitget'),state='TERMINAL',filled='0',fee='0',leg=1,accounting_pending=False)
        self.store.save_order(own)
        self.bg_book=[dict(id='bg-funding',category='USDT-FUTURES',symbol='BTCUSDT',coin='USDT',
            type='CONTRACT_MAIN_SETTLE_FEE_USER_OUT',amount='-21',fee='0',ts=str(int((self.now+.5)*1000)))]
        self.bitget_equity='29979'
        self.reconcile()
        self.anchor()
        self.reconcile()
        self.assertEqual(dec(self.store.meta('external_income')['program_funding']),0)
        self.assertIn('daily_loss',self.store.meta('stop_locks'))

    def test_new_account_refresh_revokes_prior_attribution_before_next_reconcile(self):
        self.reconcile()
        self.assertTrue(self.engine.can_open())

        self.now+=1
        self.rows=[position(self.symbol,upnl='15')]; self.equity='1005'
        self.engine.refresh_accounts()
        self.assertFalse(self.engine.can_open())
        self.reconcile()
        self.assertTrue(self.engine.can_open())

    def test_external_pending_order_blocks_income_certificate_and_keeps_raw_order(self):
        self.open_orders=[dict(symbol=self.symbol,order_id='astro-pending',qty='100')]
        self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertEqual(self.store.meta('external_failed_snapshot')['raw_open_orders']['crossex'],self.open_orders)

    def test_empty_history_with_unexplained_cash_change_is_not_complete_evidence(self):
        self.equity='999'
        self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])

    def test_same_millisecond_new_funding_is_not_discarded_at_epoch_boundary(self):
        self.book=[dict(id='boundary-funding',business_id='position:time',statement_type='FUNDING_FEE',
            change='-1',symbol=self.symbol,coin='USDT',exchange_type='BINANCE',create_time=str(int(self.now*1000)))]
        self.equity='999'
        self.assertEqual(dec(self.reconcile()['daily']),0)
        self.assertEqual(dec(self.store.meta('external_income')['external_contribution']),-1)

    def test_failed_registration_read_revokes_income_and_preserves_failed_raw_response(self):
        self.reconcile()
        self.assertTrue(self.engine.can_open())
        raw=dict(raw_positions=dict(crossex=[dict(symbol=SYMBOL,position_qty='1')]))
        failure=ValueError('invalid_response')
        failure.account_snapshot=raw
        with patch.object(self.accounts,'snapshot',side_effect=failure):
            with self.assertRaisesRegex(ValueError,'external_read_unavailable'):
                self.change(symbol=SYMBOL)
        self.assertFalse(self.engine.can_open())
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertEqual(self.store.meta('external_failed_snapshot'),raw)
        self.assertEqual(len(self.registry().records()),1)

    def test_failed_rebaseline_cannot_reuse_old_income_and_keeps_new_evidence(self):
        self.reconcile()
        self.assertTrue(self.engine.can_open())
        self.activity_complete=False
        with self.assertRaisesRegex(ValueError,'external_income_unverifiable'):
            self.anchor()
        self.assertFalse(self.engine.can_open())
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertFalse(self.store.meta('external_admin_snapshot')['activity']['complete'])


class ExternalAPITests(ExternalFixture, unittest.TestCase):
    def test_admin_only_get_status_conflict_audit_and_missing_precondition(self):
        from tools.crossex_exec import api
        real=api._Server
        config=dict(CROSSEX_EXEC_API_TOKEN='read-round11',CROSSEX_EXEC_ADMIN_TOKEN='admin-round11',
                    CROSSEX_EXEC_API_BIND='127.0.0.1')
        with patch.object(api,'_Server',side_effect=lambda addr,handler:real((addr[0],0),handler)):
            service=api.start_api(config,self.path,external_admin=self.engine.external_admin)
            self.addCleanup(service.close)
            self.assertTrue(service.ready.wait(3))
        def request(method,path,body=None,token='admin-round11'):
            conn=http.client.HTTPConnection(*service.addresses[0],timeout=3)
            try:
                conn.request(method,'/api/v1/'+path,None if body is None else json.dumps(body),
                    {'X-Api-Token':token,'Content-Type':'application/json'})
                res=conn.getresponse()
                return res.status,json.loads(res.read())
            finally: conn.close()
        code,view=request('GET','external-positions',token='read-round11')
        self.assertEqual(code,200)
        body=dict(identity=identity(),expected_status=view['status'],reason='Astro')
        self.assertEqual(request('POST','external-positions/register',body,token='read-round11')[0],403)
        self.assertEqual(request('POST','external-positions/register',dict(identity=identity(),reason='x'))[0],400)
        self.assertEqual(request('POST','external-positions/register',body)[0],200)
        self.assertEqual(request('POST','external-positions/register',body)[0],409)
        with patch.object(self.store,'event',side_effect=sqlite3.OperationalError('audit')):
            self.assertEqual(request('POST','external-positions/register',body)[0],503)

    def test_rebaseline_external_evidence_and_margin_refusals_are_conflicts(self):
        from tools.crossex_exec import api
        real=api._Server
        config=dict(CROSSEX_EXEC_API_TOKEN='read-round11',CROSSEX_EXEC_ADMIN_TOKEN='admin-round11',
                    CROSSEX_EXEC_API_BIND='127.0.0.1')
        with patch.object(api,'_Server',side_effect=lambda addr,handler:real((addr[0],0),handler)):
            service=api.start_api(config,self.path,equity_admin=self.engine.rebaseline)
            self.addCleanup(service.close)
            self.assertTrue(service.ready.wait(3))
        self.change()
        for reason in ('external_income_unverifiable','margin_open_limit'):
            with self.subTest(reason=reason):
                self.activity_complete=reason!='external_income_unverifiable'
                self.margin='2.5' if reason=='margin_open_limit' else '4'
                conn=http.client.HTTPConnection(*service.addresses[0],timeout=3)
                try:
                    conn.request('POST','/api/v1/equity/rebaseline',json.dumps(dict(confirm=True,reason='epoch')),
                        {'X-Api-Token':'admin-round11','Content-Type':'application/json'})
                    response=conn.getresponse()
                    value=json.loads(response.read())
                    self.assertEqual(response.status,409)
                    self.assertEqual(value['error'],reason)
                finally:
                    conn.close()

    def test_cli_routes_to_same_admin_and_never_enables_trading(self):
        from tools.crossex_exec import __main__ as cli
        from contextlib import contextmanager
        @contextmanager
        def running(path): yield False
        args=['external-register','--symbol',SYMBOL,'--exchange-type','HYPERLIQUID',
              '--business','FUTURE','--quote','USDC','--dex','io','--expected-status',self.registry().status()]
        with patch.object(cli,'load_env',return_value={'CROSSEX_DB':str(self.path)}), \
             patch.object(cli,'maintenance_lock',running), patch.object(cli,'running_external',return_value={}) as call, \
             patch.object(cli,'emit'):
            self.assertEqual(cli.main(args),0)
        self.assertEqual(call.call_args.args[1].command,'external-register')


if __name__=='__main__':
    unittest.main()
