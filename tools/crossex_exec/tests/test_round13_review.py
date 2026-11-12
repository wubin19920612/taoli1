"""Round 13: reproduce final-review failures with offline, file-backed evidence."""
import copy
import json
import sqlite3
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.fill_sim.core import dec
from tools.crossex_exec.accounts import Accounts
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.equity import EquityAdmin
from tools.crossex_exec.storage import Store
from test_exec import instrument, intent, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, SYMBOL, position
import test_round11_external as r11
from test_round12_fx import quotes
import test_round9_safety as r9


class FXFixture(ExternalFixture):
    def setUp(self):
        super().setUp()
        self.prices=('1.01','1.01')
        self.fx_age=0
        self.market.spot_fx=lambda coins:{c:quotes(self.now,self.prices,c,self.fx_age) for c in coins}
        self.change()
        self.rows=[position()]
        self.anchor()

    def snapshot(self):
        result=super().snapshot()
        result['notionals']={k:str(abs(dec(r['position_qty']))*dec(r['mark_price']))
            for r in self.rows for k in result['positions'] if k.split('|')[1]==r['symbol']}
        return result

    def reconcile(self):
        self.now+=1
        self.engine.reconcile()
        return self.store.meta('external_income',{})


class SnapshotProofTests(FXFixture,unittest.TestCase):
    def test_old_coordinator_cannot_publish_after_new_account_revokes_proof(self):
        self.rows=[position(upnl='1')]; self.equity='1001'
        self.assertTrue(self.reconcile()['verified'])
        original_anchor=self.store.meta('external_equity_anchor')
        self.engine.workers=[SimpleNamespace(close=lambda:None)]
        entered,resume=threading.Event(),threading.Event()
        from tools.crossex_exec import external_accounting as accounting
        continuity=accounting.cash_continuity
        def pause(*args):
            continuity(*args)
            entered.set()
            if not resume.wait(5): raise AssertionError('coordinator_not_resumed')
        with patch.object(accounting,'cash_continuity',side_effect=pause):
            worker=threading.Thread(target=self.engine.reconcile)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.now+=1; self.prices=('1.01',)
                self.engine.refresh_accounts()
            finally:
                resume.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertFalse(self.engine.can_open())
        self.assertEqual(self.store.meta('external_equity_anchor'),original_anchor)
        with self.assertRaises(ValueError): self.engine.check_order(intent(ex='bitget'))

    def test_same_timestamp_publications_get_distinct_persistent_versions_and_final_gate_checks(self):
        self.assertTrue(self.reconcile()['verified'])
        proof=self.store.meta('external_income')
        old=self.engine.account_cache.read()
        self.assertEqual(proof['snapshot_version'],old['snapshot_version'])
        self.engine.account_cache.publish(old,self.now)
        new=self.engine.account_cache.read()
        self.assertGreater(new['snapshot_version'],old['snapshot_version'])
        self.store.set_meta('external_income',proof)  # simulate a legacy stale writer
        with self.assertRaises(ValueError): self.engine.require_external_income()
        with self.assertRaises(ValueError): self.engine.check_order(intent(ex='bitget'))
        with Store(self.path) as reopened:
            other=Engine(reopened,self.market,self.accounts,clock=lambda:self.now)
            try:
                other.account_cache.publish(new,self.now)
                self.assertGreater(other.account_cache.read()['snapshot_version'],new['snapshot_version'])
            finally:
                other.close()

    def test_publication_failure_is_atomic_with_cache_and_certificate(self):
        self.assertTrue(self.reconcile()['verified'])
        old=self.engine.account_cache.read(); proof=self.store.meta('external_income')
        save=self.store.set_meta
        def fail(key,value):
            if key=='account_snapshot': raise sqlite3.OperationalError('injected')
            return save(key,value)
        with patch.object(self.store,'set_meta',side_effect=fail), self.assertRaises(sqlite3.OperationalError):
            self.engine.account_cache.publish(old,self.now)
        self.assertEqual(self.engine.account_cache.read(),old)
        self.assertEqual(self.store.meta('external_income'),proof)


class HistoryTests(ExternalFixture,unittest.TestCase):
    reconcile=r11.AccountingTests.reconcile

    def setUp(self):
        super().setUp()
        self.symbol='BINANCE_FUTURE_BTC_USDT'
        self.change(symbol=self.symbol)
        self.rows=[position(self.symbol,upnl='10')]
        self.anchor()

    def funding_pair(self):
        own=dict(intent('own'),state='TERMINAL',filled='0',fee='0',leg=0,accounting_pending=False)
        own['instrument']['symbol']='BINANCE_FUTURE_ETH_USDT'
        self.store.save_order(own)
        at=str(int((self.now+.5)*1000))
        self.book=[dict(id=name,business_id=name,statement_type='FUNDING_FEE',change=amount,
            symbol=symbol,coin='USDT',exchange_type='BINANCE',create_time=at)
            for name,amount,symbol in (('external','5',self.symbol),('program','-5','BINANCE_FUTURE_ETH_USDT'))]
        self.assertEqual(dec(self.reconcile()['daily']),-5)
        self.assertTrue(self.store.meta('external_income')['verified'])

    def test_post_anchor_verified_funding_disappearance_is_not_zero_income(self):
        anchor=self.store.meta('external_equity_anchor')
        self.funding_pair()
        self.book=[]
        self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertFalse(self.engine.can_open())
        self.assertEqual(dec(self.store.meta('account_loss_current')['daily']),-5)
        self.assertEqual(self.store.meta('external_equity_anchor'),anchor)

    def test_post_anchor_hash_rewrite_rejected_after_file_database_reopen(self):
        self.funding_pair()
        self.book[0]['change']='4'; self.book[1]['change']='-4'
        self.engine.close()
        with Store(self.path) as reopened:
            engine=Engine(reopened,self.market,self.accounts,clock=lambda:self.now,stop_file=self.engine.stop_file)
            try:
                self.now+=1; engine.reconcile()
                self.assertFalse(reopened.meta('external_income')['verified'])
                self.assertFalse(engine.can_open())
                self.assertEqual(dec(reopened.meta('account_loss_current')['daily']),-5)
            finally:
                engine.close()

    def test_digest_growth_keeps_original_epoch_and_verified_old_ids(self):
        anchor=self.store.meta('external_equity_anchor')
        self.funding_pair()
        first=self.store.meta('external_verified_history')
        self.assertEqual(set(first['crossex_book']),{'external','program'})
        self.now+=1
        self.book.append(dict(self.book[0],id='next',change='0',create_time=str(int(self.now*1000))))
        self.reconcile()
        current=self.store.meta('external_verified_history')
        self.assertEqual(set(current['crossex_book']),{'external','program','next'})
        self.assertEqual(current['crossex_book']['external'],first['crossex_book']['external'])
        self.assertEqual(self.store.meta('external_equity_anchor'),anchor)

    def test_rebaseline_cannot_erase_missing_verified_history(self):
        self.funding_pair()
        anchor=self.store.meta('external_equity_anchor')
        self.book=[]
        with self.assertRaisesRegex(ValueError,'external_income_unverifiable'):
            self.anchor()
        self.assertEqual(self.store.meta('external_equity_anchor'),anchor)

    def test_upgrade_seeds_post_anchor_ids_from_last_verified_legacy_activity(self):
        self.funding_pair()
        self.store.db.execute("DELETE FROM meta WHERE key='external_verified_history'")
        self.book=[]
        self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertFalse(self.engine.can_open())


class NotionalTests(FXFixture,unittest.TestCase):
    def test_external_500_usdc_plus_two_50_usd_legs_exceeds_hard_600(self):
        self.rows=[position(qty='5')]
        snapshot=self.engine.read_account_snapshot()
        self.engine.account_cache.publish(snapshot,self.now)
        self.assertEqual(dec(snapshot['notionals_usd']['crossex|'+SYMBOL+'|long']),dec('506.010'))
        for cid,ex in (('a','binance'),('b','bitget')):
            self.store.save_order(dict(intent(cid,ex=ex,quantity='.5'),state='INTENT',filled='0'))
        self.assertEqual(self.engine.limits.exposure(snapshot),dec('606.010'))
        for row in self.store.all_orders():
            with self.assertRaisesRegex(ValueError,'total_notional_limit'):
                self.engine.limits.check_order(row)

    def test_notional_requires_fx_even_with_zero_external_income(self):
        for prices,age in ((('1.01',),0),(('1.01','1.01'),60.01),(('1','9'),0)):
            self.prices,self.fx_age=prices,age
            with self.subTest(prices=prices,age=age):
                self.reconcile()
                self.assertFalse(self.engine.can_open())
                with self.assertRaises(ValueError): self.engine.check_order(intent())
                self.engine.check_order(intent(reduce=True))

    def test_notional_fx_rechecked_at_send_earlier_than_account_ttl(self):
        self.fx_age=59
        self.reconcile()
        snapshot=self.engine.account_cache.read()
        self.assertGreater(self.engine.limits.exposure(snapshot),100)
        self.now+=1.01
        with self.assertRaises(ValueError): self.engine.limits.check_order(intent())

    def test_unregistered_non_usd_positions_are_also_converted(self):
        self.rows=[position('BINANCE_FUTURE_ETH_BTC',qty='2')]
        snapshot=self.engine.read_account_snapshot()
        self.assertIn('BTC',snapshot['external_fx'])
        self.assertEqual(dec(snapshot['notionals_usd']['crossex|BINANCE_FUTURE_ETH_BTC|long']),dec('202.404'))


class FinalSendTests(unittest.TestCase):
    setUp=r9.SafetyTests.setUp
    snapshot=r9.SafetyTests.snapshot
    trigger=r9.SafetyTests.trigger

    def test_non_usd_exposure_arriving_at_durable_sent_blocks_both_transports(self):
        save=self.store.save_orders
        def commit(rows,kind=None):
            save(rows,kind)
            if kind=='SENT':
                snapshot=self.snapshot()
                snapshot['notionals']={'crossex|'+SYMBOL+'|long':'400'}
                snapshot['external_fx']={'USDC':quotes(self.now[0],('1.5','1.5'))}
                self.engine.account_cache.publish(snapshot,self.now[0])
        with patch.object(self.store,'save_orders',side_effect=commit):
            self.engine.ingest(self.trigger())
        self.assertFalse(any(r[0]=='place' for r in self.trace))


def real_accounts(now, debt='0', gate_debt='0'):
    cross=SimpleNamespace(mode='DUAL',account=lambda:dict(position_mode='DUAL',account_mode='CROSS_EXCHANGE',
        exchange_type='CROSSEX',margin_balance='1000',maintenance_margin='250',maintenance_margin_rate='4',
        assets=[dict(coin='USDC',exchange_type='HYPERLIQUID',liability=gate_debt,balance='1000')]),
        positions=lambda:[],open_orders=lambda:[])
    bg=SimpleNamespace(mode='hedge_mode',account=lambda:dict(effEquity='30000',mmr='7500',usdtEquity='30000',
        assets=[dict(coin='USDT',debt=debt,balance='30000')]),
        settings=lambda:dict(accountMode='unified',holdMode='hedge_mode',assetMode='multi_assets'),
        positions=lambda:[],open_orders=lambda:[])
    return Accounts(cross,bg,clock=now)


class LiabilityTests(ExternalFixture,unittest.TestCase):
    def test_invalid_new_debt_read_immediately_revokes_opening_admission(self):
        self.engine.accounts=real_accounts(lambda:self.now)
        self.engine.reconcile()
        self.assertTrue(self.engine.can_open())
        self.engine.accounts=real_accounts(lambda:self.now,'invalid-debt')
        self.engine.refresh_accounts()
        self.assertFalse(self.engine.can_open())
        with self.assertRaises(ValueError): self.engine.check_order(intent())

    def test_both_venue_liabilities_preserve_native_coin_route_and_source(self):
        snapshot=real_accounts(lambda:self.now,'28.5','7').snapshot()
        self.assertEqual(len(snapshot['liabilities']),2)
        debt=next(r for r in snapshot['liabilities'] if r['exchange_type']=='BITGET')
        self.assertEqual((debt['coin'],dec(debt['liability']),debt['source_field']),('USDT',dec('28.5'),'debt'))
        self.engine.account_cache.publish(snapshot,self.now)
        with self.assertRaisesRegex(ValueError,'account_liability'): self.engine.limits.check_order(intent())

    def test_debt_blocks_rebaseline_and_release_halt_without_baseline_changes(self):
        accounts=real_accounts(lambda:self.now,'28.5')
        admin=EquityAdmin(self.store,accounts,clock=lambda:self.now)
        for release in (False,True):
            if release:
                self.store.set_meta('state','HALTED'); self.store.set_meta('halt_reason','total_loss')
            before=self.store.meta('account_equity_baseline')
            with self.subTest(release=release),self.assertRaisesRegex(ValueError,'account_liability'):
                admin.apply(confirm='total_loss' if release else True,release_halt=release,reason='x',source='cli')
            self.assertEqual(self.store.meta('account_equity_baseline'),before)

    def test_debt_clear_requires_three_distinct_snapshots(self):
        self.engine.accounts=real_accounts(lambda:self.now,'28.5')
        self.engine.refresh_accounts()
        self.assertIn('account_liability',self.store.meta('stop_locks'))
        self.engine.accounts=real_accounts(lambda:self.now)
        for n in range(3):
            self.now+=1; self.engine.refresh_accounts()
            self.assertEqual('account_liability' in self.store.meta('stop_locks'),n<2)
            self.engine.observe_account_snapshot(self.engine.account_cache.read(),self.now)
            self.assertEqual('account_liability' in self.store.meta('stop_locks'),n<2)

    def test_invalid_or_missing_debt_is_unavailable_not_zero(self):
        for debt in (None,'NaN','Infinity','-1','unknown'):
            with self.subTest(debt=debt), self.assertRaises((ValueError,TypeError,KeyError)):
                real_accounts(lambda:self.now,debt).snapshot()
        accounts=real_accounts(lambda:self.now)
        original=accounts.bitget.account
        def missing():
            result=original(); result['assets'][0].pop('debt'); return result
        accounts.bitget.account=missing
        with self.assertRaises((ValueError,KeyError)): accounts.snapshot()


class ShutdownAuditTests(FXFixture,unittest.TestCase):
    def test_failed_flatten_audit_does_not_leave_verified_success_in_meta(self):
        emit=self.store.event
        def fail(kind,*args,**kwargs):
            if kind=='PROGRAM_FLATTENED_EXTERNAL_REMAINS':
                raise sqlite3.OperationalError('injected_audit_failure')
            return emit(kind,*args,**kwargs)
        with patch.object(self.store,'event',side_effect=fail):
            result=self.engine.flatten_all('shutdown')
        self.assertFalse(result['verified'])
        self.assertFalse(self.store.meta('flatten_status')['verified'])
        self.assertFalse(self.store.meta('flatten_status')['program_flat'])

    def test_failed_shutdown_revokes_historical_flat_and_returns_current_failure(self):
        self.store.set_meta('flatten_status',dict(program_flat=True,account_flat=True,captured_at=self.now-3600))
        self.accounts.position_snapshot=Mock(side_effect=ValueError('offline'))
        result=self.engine.flatten_all('shutdown')
        self.assertIsInstance(result,dict)
        self.assertFalse(result['verified'])
        self.assertFalse(result['program_flat'])
        self.assertFalse(self.store.meta('flatten_status')['program_flat'])
        self.assertIn('flatten_snapshot_unavailable',self.store.meta('stop_locks'))

    def test_shutdown_bypasses_halt_backoff_and_verifies_even_without_registry(self):
        self.store.set_meta('state','HALTING'); self.engine.next_halt=self.now+60
        result=self.engine.flatten_all('shutdown')
        self.assertTrue(result['verified']); self.assertTrue(result['program_flat'])
        self.assertFalse(result['account_flat'])
        self.assertEqual(self.store.meta('state'),'HALTED')
        self.store.set_meta('external_contracts',{})
        self.store.set_meta('state','RUNNING'); self.rows=[]
        result=self.engine.flatten_all('shutdown')
        self.assertTrue(result['verified']); self.assertTrue(result['account_flat'])

    def test_shutdown_exit_uses_returned_verification_never_meta(self):
        from tools.crossex_exec.__main__ import finish_shutdown
        old=dict(verified=True,program_flat=True,account_flat=True,captured_at=self.now-3600)
        self.store.set_meta('flatten_status',old)
        for result in (None,{},dict(verified=False,program_flat=True),dict(verified=True,program_flat=False)):
            with self.subTest(result=result),patch.object(self.engine,'flatten_all',return_value=result):
                self.assertEqual(finish_shutdown(self.engine),1)
        with patch.object(self.engine,'flatten_all',return_value=dict(verified=True,program_flat=True,account_flat=False)):
            self.assertEqual(finish_shutdown(self.engine),0)

    def test_both_admin_audits_include_old_attribution_baselines_and_rollback(self):
        keys=('external_equity_anchor','program_daily_upnl','program_last_upnl')
        for release in (False,True):
            old={k:self.store.meta(k) for k in keys}
            if release:
                self.store.set_meta('state','HALTED'); self.store.set_meta('halt_reason','total_loss')
            audit=self.engine.rebaseline(confirm='total_loss' if release else True,release_halt=release,reason='x',source='cli')
            self.assertEqual({k:audit['old'][k] for k in keys},old)
        old={k:self.store.meta(k) for k in keys}
        with patch.object(self.store,'event',side_effect=sqlite3.OperationalError('audit')):
            with self.assertRaises(sqlite3.OperationalError): self.anchor()
        self.assertEqual({k:self.store.meta(k) for k in keys},old)
