"""Final-review regressions: real SQLite, simulated clocks and venues, no network."""
import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec.execution import NONTERMINAL
from tools.crossex_exec.storage import Store
from test_exec import intent, setUpModule, tearDownModule
import test_round7 as previous


class FinalExecutionTests(unittest.TestCase):
    setUp = previous.EngineAdmissionTests.setUp
    snapshot = previous.EngineAdmissionTests.snapshot
    pair = previous.EngineAdmissionTests.pair
    trigger = previous.EngineAdmissionTests.trigger
    skips = previous.EngineAdmissionTests.skips

    def test_slow_sent_commit_1_01_seconds_rejects_both_without_refresh(self):
        save = self.store.save_order
        delayed = []
        def slow(order, kind=None):
            result = save(order, kind)
            if kind == 'SENT' and not delayed:
                delayed.append(order['cid'])
                self.now[0] += 1.01
            return result
        with patch.object(self.store, 'save_order', side_effect=slow), patch.object(
                self.market, 'books', wraps=self.market.books) as fetch:
            self.engine.ingest(self.trigger())
        self.assertEqual(len(delayed), 1)
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(any(r[0] == 'place' for r in self.trace))
        self.assertEqual([o['state'] for o in self.store.all_orders()], ['UNSENT', 'UNSENT'])
        self.assertEqual([s['reason'] for s in self.skips()], ['book_stale'])

    def test_sent_commit_exact_one_second_still_releases_both(self):
        save = self.store.save_order
        delayed = []
        def slow(order, kind=None):
            result = save(order, kind)
            if kind == 'SENT' and not delayed:
                delayed.append(order['cid'])
                self.now[0] += 1
            return result
        with patch.object(self.store, 'save_order', side_effect=slow):
            pair = self.engine.ingest(self.trigger())
        self.assertEqual(pair['state'], 'OPEN')
        self.assertEqual(len([r for r in self.trace if r[0] == 'place']), 2)

    def test_second_sent_write_failure_rejects_entire_pair(self):
        save = self.store.save_order
        def fail(order, kind=None):
            if kind == 'SENT' and order['leg'] == 1:
                raise sqlite3.OperationalError('second SENT failed')
            return save(order, kind)
        with patch.object(self.store, 'save_order', side_effect=fail):
            self.engine.ingest(self.trigger())
        self.assertFalse(any(r[0] == 'place' for r in self.trace))
        self.assertEqual([o['state'] for o in self.store.all_orders()], ['UNSENT', 'UNSENT'])
        self.assertFalse(any(e['kind'] == 'SENT' for e in self.store.events()))

    def test_post_persistence_hard_risk_and_stop_reject_entire_pair(self):
        save = self.store.save_order
        for risk in ('equity', 'notional', 'stop'):
            def change(order, kind=None):
                result = save(order, kind)
                if kind == 'SENT' and order['leg'] == 1:
                    if risk == 'equity':
                        self.data['equity_by_venue']['crossex'] = '850'
                    elif risk == 'notional':
                        self.data['notionals'] = {'foreign': '601'}
                    else:
                        self.engine.stop_file.touch()
                    self.engine.account_cache.publish(self.snapshot())
                return result
            with self.subTest(risk=risk), patch.object(self.store, 'save_order', side_effect=change):
                self.engine.ingest(self.trigger(risk))
                self.assertFalse(any(r[0] == 'place' for r in self.trace))
                self.assertTrue(all(o['state'] == 'UNSENT' for o in self.store.all_orders()))
            self.data['equity_by_venue']['crossex'] = '1000'
            self.data['notionals'] = {}
            self.store.set_meta('state', 'RUN')
            self.engine.stops.discard('account_equity_drop')
            if self.engine.stop_file.exists():
                self.engine.stop_file.unlink()

    def test_both_sent_rows_committed_before_either_sender_is_released(self):
        observations = []
        for venue in self.venues:
            original = venue.place
            def place(order, before_send, original=original):
                def observe():
                    before_send()
                    rows = self.store.all_orders()
                    observations.append([r['state'] for r in rows])
                    self.assertEqual(len(rows), 2)
                    self.assertTrue(all(r['state'] != 'INTENT' for r in rows))
                    self.assertFalse(self.store.db.in_transaction)
                return original(order, observe)
            venue.place = place
        self.engine.ingest(self.trigger())
        self.assertEqual(len(observations), 2)
        self.assertEqual(len([r for r in self.trace if r[0] == 'place']), 2)

    def test_second_intent_failure_rolls_back_rows_and_events_recovers_without_restart(self):
        self.store.db.executescript("""
            CREATE TRIGGER fail_second_intent BEFORE INSERT ON orders
            WHEN json_extract(NEW.value,'$.leg')=1 AND json_extract(NEW.value,'$.state')='INTENT'
            BEGIN SELECT RAISE(ABORT,'second INTENT failed'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'second INTENT failed'):
            self.engine.ingest(self.trigger())
        self.assertFalse(self.store.all_orders())
        self.assertFalse(any(e['kind'] == 'INTENT' for e in self.store.events()))
        self.assertFalse(any(r[0] == 'place' for r in self.trace))
        self.store.db.execute('DROP TRIGGER fail_second_intent')
        self.store.set_meta('state', 'HALTING')
        self.engine.tick()
        self.assertEqual(self.store.meta('state'), 'HALTED')
        self.assertFalse(self.store.pairs(active=True))
        self.assertEqual([v.quantity for v in self.venues], [dec(0), dec(0)])

    def test_runtime_orphan_intent_retires_but_sent_and_unknown_are_queried(self):
        for n, state in enumerate(('INTENT', 'SENT', 'UNKNOWN')):
            row = dict(intent('orphan-'+state), leg=0, state=state, filled='0',
                       accounting_pending=True, oid=None)
            self.store.save_order(row)
        self.venues[0].unresolved = True
        self.engine.executor.recover(startup=False)
        self.assertEqual(self.store.order('orphan-INTENT')['state'], 'UNSENT')
        for state in ('SENT', 'UNKNOWN'):
            self.assertIn(self.store.order('orphan-'+state)['state'], NONTERMINAL)
        self.assertTrue(any(r[0] == 'detail' for r in self.trace))
        self.assertTrue(any(r[0] == 'cancel' for r in self.trace))
        self.assertFalse(any(r[0] == 'place' for r in self.trace))

    def test_runtime_recovery_does_not_retire_an_owned_intent(self):
        entered, release = threading.Event(), threading.Event()
        original = self.venues[0].place
        def wait(order, before_send):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('test release')
            return original(order, before_send)
        self.venues[0].place = wait
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.engine.ingest, self.trigger())
            try:
                self.assertTrue(entered.wait(2))
                self.engine.executor.recover(startup=False)
                self.assertEqual([o['state'] for o in self.store.all_orders()], ['INTENT', 'INTENT'])
            finally:
                release.set()
            self.assertEqual(job.result()['state'], 'OPEN')

    def test_actual_sent_transaction_commit_delay_is_inside_final_age_check(self):
        delayed = []
        def trace(statement):
            if statement == 'COMMIT' and not delayed:
                states = [r[0] for r in self.store.db.execute(
                    "SELECT json_extract(value,'$.state') FROM orders")]
                if states == ['SENT', 'SENT']:
                    delayed.append(True)
                    self.now[0] += 1.01
        self.store.db.set_trace_callback(trace)
        try:
            self.engine.ingest(self.trigger())
        finally:
            self.store.db.set_trace_callback(None)
        self.assertEqual(delayed, [True])
        self.assertFalse(any(r[0] == 'place' for r in self.trace))
        self.assertEqual([o['state'] for o in self.store.all_orders()], ['UNSENT', 'UNSENT'])


class FinalHistoryTests(unittest.TestCase):
    setUp = previous.EngineAdmissionTests.setUp
    snapshot = previous.EngineAdmissionTests.snapshot
    pair = previous.EngineAdmissionTests.pair
    trigger = previous.EngineAdmissionTests.trigger

    def history(self):
        self.engine.live = self.engine.executor.live = False
        pair = self.engine.ingest(self.trigger())
        for n in range(257):
            self.store.pair(dict(pair, id='history-'+str(n)))
            self.store.save_order(dict(intent('history-'+str(n)), state='SHADOW', filled='0', leg=0,
                                       pair_id='history-'+str(n), accounting_pending=False))
        return pair

    def test_runtime_paths_do_not_materialize_full_order_or_pair_history(self):
        self.history()
        pairs = self.store.pairs
        def active_only(active=False):
            if not active:
                raise AssertionError('full pair history')
            return pairs(active=True)
        with patch.object(self.store, 'all_orders', side_effect=AssertionError('full order history')), patch.object(
                self.store, 'pairs', side_effect=active_only):
            self.assertTrue(self.engine.can_open())
            self.assertEqual(self.engine.totals(), (dec(0), dec(0)))
            self.assertEqual(self.engine.limits.exposure(self.snapshot(), intent('next')), dec(100))
            self.engine.executor.recover(startup=False)
            self.engine.tick()
            self.engine.reconcile_positions(dict(self.snapshot(), captured_at=self.now[0]+1))

    def test_risk_and_realized_loss_include_records_beyond_first_page(self):
        self.history()
        entry = dict(intent('late-entry'), pair_id='late', leg=0, state='TERMINAL', filled='1',
                     accounting_pending=False, vwap='100', fee='0', created_at=self.now[0])
        close = dict(entry, cid='late-close', reduce_only=True, side='sell', vwap='90')
        self.store.save_order(entry)
        self.store.save_order(close)
        self.assertEqual(self.engine.totals(), (dec(-10), dec(-10)))
        pending = dict(entry, cid='late-pending', pair_id='pending', state='UNKNOWN', quantity='.7',
                       filled='0', vwap=None, accounting_pending=True)
        self.store.save_order(pending)
        self.assertEqual(self.engine.limits.exposure(self.snapshot(), pending), dec(70))
        self.assertFalse(self.engine.can_open())

    def test_api_summary_does_not_decode_shadow_history(self):
        from tools.crossex_exec.api_data import Budget, JournalView
        self.history()
        decoded = []
        decode = Budget.decode
        def observe(budget, raw):
            value = decode(budget, raw)
            if isinstance(value, dict) and value.get('state') == 'SHADOW':
                decoded.append(value)
            return value
        with patch.object(Budget, 'decode', observe):
            summary = JournalView(self.store_path(), {}, clock=lambda:self.now[0]).get('summary', {})
        self.assertEqual(decoded, [])
        self.assertEqual(summary['caps']['open_pairs'], 0)

    def test_api_history_does_not_retain_all_decoded_orders(self):
        from tools.crossex_exec.api_data import Budget, JournalView
        self.history()
        for n in range(129):
            pair = self.store.get_pair('history-'+str(n))
            pair.update(state='CLOSED', closed_at=self.now[0])
            self.store.pair(pair)
        live, peaks = [0], []
        class Observed(dict):
            def __init__(self, value):
                super().__init__(value)
                live[0] += 1
                peaks.append(live[0])
            def __del__(self):
                live[0] -= 1
        decode = Budget.decode
        def observe(budget, raw):
            value = decode(budget, raw)
            return Observed(value) if isinstance(value, dict) and 'cid' in value else value
        with patch.object(Budget, 'decode', observe):
            view = JournalView(self.store_path(), {}, clock=lambda:self.now[0])
            self.assertEqual(view.get('summary', {})['total']['trades'], 129)
            self.assertEqual(len(view.get('trades', {})), 129)
            self.assertEqual(view.get('stats', {})['n'], 129)
        self.assertGreater(len(peaks), 129)
        self.assertLessEqual(max(peaks), 4)

    def store_path(self):
        from pathlib import Path
        return Path(self.tmp.name)/'exec.db'


class StoragePagingTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        for n in range(257):
            self.store.pair(dict(id=str(n), state='SHADOW', last_books=['large-unused-history']))
            self.store.save_order(dict(intent('cid-'+str(n)), state='SHADOW', filled='0', leg=0,
                                       pair_id=str(n), accounting_pending=False))

    def test_active_pair_filter_happens_before_json_decode(self):
        self.store.pair(dict(id='active', state='OPEN'))
        with patch('tools.crossex_exec.storage.json.loads', wraps=json.loads) as decode:
            rows = self.store.pairs(active=True)
        self.assertEqual(rows, [dict(id='active', state='OPEN')])
        self.assertEqual(decode.call_count, 1)

    def test_order_filters_happen_in_sql_before_decode(self):
        self.store.save_order(dict(intent('pending'), pair_id='active', state='UNKNOWN',
                                   filled='0', accounting_pending=True, sent_ms=10))
        with patch('tools.crossex_exec.storage.json.loads', wraps=json.loads) as decode:
            rows = list(self.store.iter_orders(pair_id='active', states=NONTERMINAL,
                        accounting_pending=True, sent_since=10))
        self.assertEqual([r['cid'] for r in rows], ['pending'])
        self.assertEqual(decode.call_count, 1)

    def test_unknown_state_is_not_hidden_by_sql_not_in_null_semantics(self):
        self.store.pair(dict(id='unknown-state', state=None))
        self.store.save_order(dict(intent('unknown-state'), state=None, filled='1'))
        self.assertEqual([p['id'] for p in self.store.pairs(active=True)], ['unknown-state'])
        self.assertEqual([o['cid'] for o in self.store.iter_orders(exclude_states=('SHADOW','UNSENT'))],
                         ['unknown-state'])

    def test_keyset_pages_do_not_skip_rows_while_current_rows_are_updated(self):
        statements = []
        self.store.db.set_trace_callback(statements.append)
        seen = []
        for row in self.store.iter_orders(states=('SHADOW',), page_size=17):
            seen.append(row['cid'])
            row['state'] = 'UNSENT'
            self.store.save_order(row)
        self.assertEqual(len(seen), 257)
        self.assertEqual(len(set(seen)), 257)
        queries = [s for s in statements if s.startswith('SELECT rowid,value FROM orders')]
        self.assertGreater(len(queries), 1)
        self.assertTrue(all('LIMIT 17' in s and 'rowid >' in s for s in queries))

    def test_pair_history_pages_are_complete_and_bounded(self):
        statements = []
        self.store.db.set_trace_callback(statements.append)
        self.assertEqual(len(list(self.store.iter_pairs(page_size=19))), 257)
        queries = [s for s in statements if s.startswith('SELECT rowid,value FROM pairs')]
        self.assertGreater(len(queries), 1)
        self.assertTrue(all('LIMIT 19' in s for s in queries))

    def test_pending_memory_observation_overrides_sql_filter(self):
        from tools.crossex_exec.execution import Executor
        executor = Executor(self.store, {})
        row = self.store.order('cid-0')
        row.update(state='UNKNOWN', accounting_pending=True)
        with patch.object(self.store, 'save_order', side_effect=sqlite3.OperationalError('disk')):
            self.assertFalse(executor.save(row))
        self.assertEqual([o['cid'] for o in executor.orders(states=NONTERMINAL)], ['cid-0'])
        self.assertFalse(executor.orders(pair_id='0', states=('SHADOW',)))


if __name__ == '__main__':
    unittest.main()
