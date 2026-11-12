"""Round 15 regressions: synthetic evidence, real journal, no private network."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.fill_sim.core import dec
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.execution import Executor
from tools.crossex_exec.external import ExternalRegistry
from tools.crossex_exec.program_risk import debt_attribution, journal_pnl, program_notionals
from tools.crossex_exec.storage import Store
from tools.crossex_exec.transport import APIError
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, SYMBOL, identity, position, rule
from test_round14_isolation import IsolationFixture, EXTERNAL


class IdentityTests(ExternalFixture, unittest.TestCase):
    def test_production_shape_null_order_cap_and_dual_hl_positions_register(self):
        # Minimal projection of the read-only incident: no account/user IDs.
        catalog = rule()
        catalog.update(max_limit_size=None, max_market_size=None, min_notional='10', tick_size='.001')
        self.store.set_meta('external_catalog', dict(at=self.now, rules=[catalog]))
        self.rows = [position(qty='8.107'), position(qty='13.196', side='SHORT')]
        result = self.change()
        self.assertEqual(result['record']['identity'], identity())
        self.assertIsNone(result['record']['rule']['max_limit_size'])
        projection = self.registry().split(self.snapshot())
        self.assertEqual(projection['positions'], {})
        self.assertEqual(projection['external_positions'], {
            'crossex|'+SYMBOL+'|long': dec('8.107'),
            'crossex|'+SYMBOL+'|short': dec('13.196')})
        for reduce in (False, True):
            order = intent(reduce=reduce)
            order['instrument']['symbol'] = SYMBOL
            with self.assertRaisesRegex(ValueError, 'external_contract_forbidden'):
                self.engine.check_order(order)
        with self.assertRaisesRegex(ValueError, 'external_remote_position'):
            self.change(False)

    def test_null_cap_does_not_relax_identity_or_malformed_rule_checks(self):
        original = rule(); original['max_limit_size'] = None
        for field, value in (('tag', 'xyz'), ('exchange_type', 'BINANCE'),
                             ('business_type', 'SPOT'), ('state', 'suspend'),
                             ('support_cross', 'false'), ('delist_time', '1'),
                             ('min_size', None), ('lot_size', '0'), ('tick_size', 'NaN'),
                             ('max_limit_size', '-1'), ('max_limit_size', 'NaN')):
            with self.subTest(field=field, value=value):
                bad = dict(original); bad[field] = value
                self.store.set_meta('external_catalog', dict(at=self.now, rules=[bad]))
                with self.assertRaisesRegex(ValueError, 'external_identity_unverifiable'):
                    self.change()
        self.store.set_meta('external_catalog', dict(at=self.now, rules=[original, original]))
        with self.assertRaisesRegex(ValueError, 'external_identity_unverifiable'):
            self.change()

    def test_register_rejects_raw_identity_conflict_in_either_direction(self):
        for field, value in (('exchange_type', 'BINANCE'), ('business_type', 'SPOT'),
                             ('tag', 'xyz'), ('dex', 'xyz'), ('quote', 'USDT')):
            for side in ('LONG', 'SHORT'):
                with self.subTest(field=field, side=side):
                    self.rows = [dict(position(side=side), **{field: value})]
                    with self.assertRaisesRegex(ValueError, 'external_identity_unverifiable'):
                        self.change()
                    self.assertEqual(self.registry().records(), {})


class RiskIdentityTests(IsolationFixture, unittest.TestCase):
    def test_conflicting_identity_never_excludes_exposure_or_exempts_debt(self):
        self.debt()
        for field, value in (('exchange_type', 'BINANCE'), ('business_type', 'SPOT'),
                             ('tag', 'xyz'), ('dex', 'xyz'), ('quote', 'USDC')):
            with self.subTest(field=field):
                old = copy.deepcopy(self.rows)
                self.rows[0][field] = value
                snapshot = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'external_identity_unverifiable'):
                    self.registry().split(snapshot)
                with self.assertRaises(ValueError):
                    program_notionals(self.store, snapshot, self.now)
                attribution = debt_attribution(self.store, snapshot, self.now)
                self.assertEqual(attribution['external'], [])
                self.assertEqual(attribution['blocking'], self.liabilities)
                from tools.crossex_exec.api_data import JournalView
                self.engine.account_cache.publish(snapshot,self.now)
                shown=JournalView(self.path,{},clock=lambda:self.now).get('remote-positions',{})['positions'][0]
                self.assertFalse(shown['identity_verified'])
                self.assertEqual(shown['ownership'],'unregistered')
                self.rows = old


class HaltingTests(ExternalFixture, unittest.TestCase):
    def test_halting_foreign_orders_do_not_abort_refresh_or_reduction(self):
        self.store.set_meta('state', 'HALTING')
        self.store.set_meta('halt_reason', 'total_loss')
        self.accounts.validate_startup = lambda:dict(self.snapshot(),open_orders=[dict(clientOid='foreign')])
        with patch.object(self.engine,'start_background') as start, patch.object(self.engine,'flatten_all') as flatten:
            self.engine.startup(())
        start.assert_called_once()
        flatten.assert_called_once()
        self.assertEqual(self.store.meta('halt_reason'),'total_loss')
        self.assertFalse(self.engine.can_open())

    def test_real_halting_workers_cannot_block_coordinator_retry(self):
        self.store.set_meta('state', 'HALTING')
        self.store.set_meta('halt_reason', 'total_loss')
        self.accounts.validate_startup = Mock(side_effect=ValueError('offline'))
        release = threading.Event()
        entered = [threading.Event() for _ in range(3)]
        def blocked(n):
            entered[n].set()
            if not release.wait(5): raise AssertionError('refresh_never_released')
        try:
            with patch.object(self.engine,'refresh_accounts',side_effect=lambda:blocked(0)), \
                 patch.object(self.engine,'refresh_reference',side_effect=lambda:blocked(1)), \
                 patch.object(self.engine,'refresh_baselines',side_effect=lambda:blocked(2)), \
                 patch.object(self.engine,'flatten_all') as flatten:
                self.engine.startup(())
                self.assertTrue(all(e.wait(1) for e in entered))
                self.assertTrue(all(w.thread.is_alive() for w in self.engine.workers))
                self.engine.tick()
                self.assertEqual(flatten.call_count,2)
                self.assertFalse(self.engine.can_open())
        finally:
            release.set()
            self.engine.close()

    def test_restart_starts_all_refreshers_even_when_startup_accounts_fail(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                self.engine.workers = []
                self.store.set_meta('state', 'HALTING')
                self.store.set_meta('halt_reason', 'total_loss')
                self.accounts.validate_startup = Mock(side_effect=ValueError('offline')) if failed else self.snapshot
                callbacks = {}
                def worker(callback, interval, name, on_error):
                    callbacks[name] = callback
                    return SimpleNamespace(close=lambda:None)
                with patch('tools.crossex_exec.engine.PeriodicWorker', side_effect=worker), \
                        patch.object(self.engine, 'flatten_all') as flatten:
                    self.engine.startup(('BTC',))
                self.assertEqual(set(callbacks), {'exec-accounts', 'exec-reference', 'exec-baseline'})
                flatten.assert_called_once()
                self.assertEqual(self.store.meta('halt_reason'), 'total_loss')
                self.assertFalse(self.engine.can_open())

    def test_halting_refreshes_expired_catalog_accounts_sampling_and_retries_independently(self):
        self.change(); self.rows = [position()]
        self.now += 21601
        self.store.set_meta('state', 'HALTING')
        self.store.set_meta('halt_reason', 'total_loss')
        self.accounts.validate_startup = self.snapshot
        self.accounts.fee_snapshots = {}
        callbacks = {}
        def worker(callback, interval, name, on_error):
            callbacks[name] = callback
            return SimpleNamespace(close=lambda:None)
        with patch('tools.crossex_exec.engine.PeriodicWorker', side_effect=worker):
            self.engine.startup(())
        self.assertEqual(self.store.meta('state'), 'HALTING')
        self.assertEqual(len(callbacks), 3)
        self.market.bootstrap = Mock(side_effect=lambda bases:self.catalog())
        self.market.sample_quotes = Mock(return_value=({}, ()))
        callbacks['exec-reference']()
        callbacks['exec-baseline']()
        with patch.object(self.accounts, 'snapshot', wraps=self.snapshot) as read:
            callbacks['exec-accounts']()
            self.assertGreaterEqual(read.call_count, 2)
        self.market.bootstrap.assert_called_once()
        self.market.sample_quotes.assert_called_once()
        self.assertFalse(self.engine.can_open())
        self.now = max(self.now+1, self.engine.next_halt+1)
        self.engine.tick()
        self.assertEqual(self.store.meta('state'), 'HALTED')
        self.assertEqual(self.store.meta('halt_reason'), 'total_loss')
        self.assertTrue(self.store.meta('flatten_status')['program_flat'])
        self.assertFalse(self.store.meta('flatten_status')['account_flat'])
        self.assertEqual(self.store.all_orders(), [])


class RejectionTests(ExternalFixture, unittest.TestCase):
    def rejector(self):
        def place(order, before_send):
            before_send(); before_send.on_send()
            raise APIError('rejected')
        adapter = SimpleNamespace(place=Mock(side_effect=place), detail=Mock(side_effect=APIError('unknown')))
        executor = Executor(self.store, {'crossex':adapter}, live=True, clock=lambda:self.now,
                            confirm_timeout=.001, poll_interval=.0001)
        return executor, adapter

    def legacy(self, cid='legacy'):
        sent = dict(intent(cid), state='SENT', filled='0', oid=None,
                    accounting_pending=True, sent_persisted_ms=self.now*1000)
        self.store.save_order(sent, 'SENT')
        row = dict(sent, state='REJECTED', reason='rejected', accounting_pending=False,
                   sent_ms=self.now*1000)
        self.store.save_order(row, 'REJECTED')
        return row

    def test_explicit_rejection_has_complete_zero_accounting_and_audit(self):
        executor, adapter = self.rejector()
        row = executor.execute(intent())
        self.assertEqual(row['state'], 'REJECTED')
        self.assertEqual(row['filled'], '0')
        self.assertEqual(row.get('fee'), '0')
        self.assertIsNone(row.get('fill_ms'))
        self.assertIsNone(row.get('vwap'))
        self.assertEqual(row.get('trade_ids'), [])
        self.assertFalse(row['accounting_pending'])
        self.assertEqual(journal_pnl(self.store, self.now), (dec(0), dec(0)))
        self.assertTrue(row.get('zero_fill_evidence'))
        adapter.detail.assert_not_called()

    def test_legacy_rejection_recovers_from_matching_durable_events_after_reopen(self):
        self.legacy()
        with Store(self.path) as reopened:
            executor = Executor(reopened, {}, live=True, clock=lambda:self.now)
            executor.recover()
            self.assertEqual(journal_pnl(reopened, self.now), (dec(0), dec(0)))
            row = reopened.order('legacy')
            self.assertEqual(row['fee'], '0')
            self.assertEqual(row['trade_ids'], [])
            self.assertTrue(row['zero_fill_evidence'])
            events = reopened.events()
            executor.recover()
            self.assertEqual(reopened.events(), events)

    def test_legacy_repair_rejects_missing_conflicting_or_positive_evidence(self):
        executor, _ = self.rejector()
        for n, change in enumerate(({'filled':'1'}, {'oid':'accepted'}, {'reason':'unknown'},
                                    {'trade_ids':['trade']}, {'fee':'2'}, {'vwap':'100'},
                                    {'instrument':intent(ex='bitget')['instrument']})):
            with self.subTest(change=change):
                row = self.legacy('bad-'+str(n)); row.update(change)
                self.store.save_order(row)
                executor.recover()
                self.assertEqual(self.store.order(row['cid']), row)
        row = dict(self.legacy('without-event'), cid='no-event')
        self.store.save_order(row)
        executor.recover()
        self.assertEqual(self.store.order('no-event'), row)

    def test_unknown_is_never_repaired_as_zero_rejection(self):
        executor, _ = self.rejector()
        row = dict(self.legacy(), state='UNKNOWN', reason='confirmation_deadline', accounting_pending=True)
        self.store.save_order(row)
        executor.recover()
        after = self.store.order(row['cid'])
        self.assertEqual(after['state'], 'UNKNOWN')
        self.assertNotIn('fee', after)
        self.assertTrue(after['accounting_pending'])

    def test_legacy_repair_audit_failure_rolls_back_and_retry_is_idempotent(self):
        import sqlite3
        row=self.legacy()
        executor,_=self.rejector()
        event=self.store.event
        def fail(kind,*args,**kwargs):
            if kind=='REJECTION_ACCOUNTING_RECOVERED': raise sqlite3.OperationalError('injected')
            return event(kind,*args,**kwargs)
        with patch.object(self.store,'event',side_effect=fail), self.assertRaises(sqlite3.OperationalError):
            executor.recover()
        self.assertEqual(self.store.order(row['cid']),row)
        with self.assertRaises(ValueError): journal_pnl(self.store,self.now)
        executor.recover()
        self.assertEqual(journal_pnl(self.store,self.now),(dec(0),dec(0)))

    def test_prior_unknown_or_ack_event_forbids_legacy_zero_upgrade(self):
        executor,_=self.rejector()
        for n,state in enumerate(('ACK','UNKNOWN','TERMINAL')):
            with self.subTest(state=state):
                row=self.legacy('suspicious-'+str(n))
                self.store.event(state,dict(row,state=state),cid=row['cid'])
                executor.recover()
                self.assertEqual(self.store.order(row['cid']),row)


if __name__ == '__main__':
    unittest.main()
