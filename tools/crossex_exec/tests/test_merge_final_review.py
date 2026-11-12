"""Round-eight checks must survive the final-review durable send barrier."""
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tools.fill_sim.core import Book, dec
from tools.crossex_exec.accounts import FeeSnapshot
from tools.crossex_exec.api_data import JournalView
from tools.crossex_exec.settings import Settings
from tools.crossex_exec.storage import Store
from baseline_fixtures import seed_baseline
from test_exec import setUpModule, tearDownModule
from test_round8_universe import native
import test_round7 as previous


class MergeReleaseTests(unittest.TestCase):
    setUp = previous.EngineAdmissionTests.setUp
    snapshot = previous.EngineAdmissionTests.snapshot
    pair = previous.EngineAdmissionTests.pair
    trigger = previous.EngineAdmissionTests.trigger

    def after_sent(self, change, payload=None):
        """Inject only after a real SQLite commit, before the shared release."""
        save, release = self.store.save_orders, self.engine.executor.check_release
        commits, releases, errors = [], [], []

        def committed(rows, kind=None):
            result = save(rows, kind)
            if kind == 'SENT':
                self.assertFalse(self.store.db.in_transaction)
                self.assertEqual([self.store.order(r['cid'])['state'] for r in rows], ['SENT', 'SENT'])
                commits.append(tuple(r['cid'] for r in rows))
                change(rows)
            return result

        def checked(intents):
            releases.append(tuple(i['cid'] for i in intents))
            try:
                return release(intents)
            except ValueError as error:
                errors.append(getattr(error, 'reason', str(error)))
                raise

        with patch.object(self.store, 'save_orders', side_effect=committed), patch.object(
                self.engine.executor, 'check_release', side_effect=checked), patch.object(
                self.market, 'books', wraps=self.market.books) as books:
            pair = self.engine.ingest(payload or self.trigger())
        self.assertEqual(len(commits), 1)
        self.assertEqual(releases, commits)
        self.assertEqual(books.call_count, 1)  # No refresh after durable SENT.
        return pair, errors

    def assert_rejected(self, errors, reason):
        self.assertEqual(errors, [reason])
        self.assertFalse(any(row[0] == 'place' for row in self.trace))
        self.assertEqual([o['state'] for o in self.store.all_orders()], ['UNSENT', 'UNSENT'])
        self.assertEqual(self.engine.executor.owned, {})

    def test_config_version_is_rechecked_after_both_sent_rows_commit(self):
        _, errors = self.after_sent(lambda rows: Settings(self.store).update(
            {'min_excess_bps': 20}, source='admin_api'))
        self.assert_rejected(errors, 'config_version_changed')

    def test_depth_size_is_rechecked_after_both_sent_rows_commit(self):
        def change(rows):
            pair = self.store.get_pair(rows[0]['pair_id'])
            for book in pair['last_books']:
                book['bids'][0][1] = book['asks'][0][1] = '.4'
            self.store.pair(pair)
        _, errors = self.after_sent(change)
        self.assert_rejected(errors, 'send_size_changed')

    def test_oi_expiring_during_commit_rejects_even_when_books_are_fresh(self):
        interests = self.market.open_interests
        def nearly_expired(instruments, books):
            return [dict(row, at=self.now[0]-299.75) for row in interests(instruments, books)]
        def change(rows):
            self.now[0] += .5
        with patch.object(self.market, 'open_interests', side_effect=nearly_expired):
            _, errors = self.after_sent(change)
        self.assert_rejected(errors, 'oi_missing')
        self.assertEqual(self.store.meta('opening_skip_counts')['oi_missing'], 1)

    def test_configured_age_limit_is_used_after_sent_commit(self):
        Settings(self.store).update({'max_book_age_seconds': .25}, source='admin_api')
        def change(rows):
            self.now[0] += .251
        _, errors = self.after_sent(change)
        self.assert_rejected(errors, 'book_stale')

    def test_configured_age_limit_equality_releases_both(self):
        Settings(self.store).update({'max_book_age_seconds': .25}, source='admin_api')
        def change(rows):
            self.now[0] += .25
        pair, errors = self.after_sent(change)
        self.assertEqual(errors, [])
        self.assertEqual(pair['state'], 'OPEN')
        self.assertEqual(len([r for r in self.trace if r[0] == 'place']), 2)

    def mapped_trigger(self):
        payload = self.trigger('mapped-release')
        rows = [native('binance', '1000SHIB'), native('bitget', 'SHIB')]
        for i, factor in zip(rows, (1000, 1)):
            i.spec = replace(i.spec, qty_step=str(dec('.001')/factor), min_qty='0', min_notional='0')
        self.market.mapping = {i.market.key: i for i in rows}
        self.accounts.fee_snapshots = {i.market.key: FeeSnapshot('0', self.now[0]) for i in rows}
        self.store.set_meta('mapping', dict(at=self.now[0], instruments={i.market.key:i.dump() for i in rows}))
        self.store.set_meta('market_tickers', {i.market.key:dict(mid=str(mid), volume='9000000', at=self.now[0])
                            for i, mid in zip(rows, (1000, 1))})
        self.engine.universe.discover()
        self.engine.universe.decide_mapping(self.engine.universe.mappings()[0]['id'], 'confirmed')
        seed_baseline(self.store, self.now[0], base='SHIB')
        self.store.set_meta('baseline_quotes', dict(at=self.now[0], mids={
            ex:{'SHIB':price} for ex, price in (('binance','1'), ('bitget','1.005'), ('bybit','1'))}))
        self.market.books = lambda instruments: [Book([[p,'100000']], [[p,'100000']], self.now[0])
            for i in instruments for p in ('1000' if i.market.exchange == 'binance' else '1.005',)]
        return dict(payload, symbol='SHIB', buy_market_key=rows[0].dump()['market'],
                    sell_market_key=rows[1].dump()['market'])

    def test_mapping_price_ratio_is_rechecked_after_sent_commit(self):
        payload = self.mapped_trigger()
        def change(rows):
            pair = self.store.get_pair(rows[0]['pair_id'])
            self.assertEqual([dec(i['quantity_multiplier']) for i in pair['instruments']], [1000, 1])
            pair['last_books'][1]['bids'][0][0] = pair['last_books'][1]['asks'][0][0] = '1.02'
            self.store.pair(pair)
        _, errors = self.after_sent(change, payload)
        self.assert_rejected(errors, 'mapping_ratio_mismatch')
        self.assertEqual(self.store.meta('opening_skip_counts')['mapping_ratio_mismatch'], 1)

    def test_mapping_decision_is_rechecked_after_sent_commit(self):
        payload = self.mapped_trigger()
        def change(rows):
            self.engine.universe.decide_mapping(self.engine.universe.mappings()[0]['id'], 'rejected')
        _, errors = self.after_sent(change, payload)
        self.assert_rejected(errors, 'mapping_unconfirmed')

    def test_admin_writer_waits_through_sent_commit_and_shared_release(self):
        entered, done = threading.Event(), threading.Event()
        failures, workers = [], []
        with Store(Path(self.tmp.name)/'exec.db') as writer:
            def update():
                entered.set()
                try:
                    Settings(writer).update({'order_usd_max': 5}, source='admin_api')
                except Exception as error:
                    failures.append(error)
                finally:
                    done.set()
            def change(rows):
                worker = threading.Thread(target=update)
                workers.append(worker)
                worker.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(done.wait(.02))
            try:
                pair, errors = self.after_sent(change)
            finally:
                for worker in workers:
                    worker.join(2)
            self.assertTrue(done.is_set())
            self.assertEqual(failures, [])
            self.assertEqual(errors, [])
            self.assertEqual(pair['config_at_open']['version'], 0)
            self.assertEqual(Settings(writer).read()['version'], 1)
            self.assertEqual(len([r for r in self.trace if r[0] == 'place']), 2)

    def test_recovery_lock_contention_does_not_invert_shared_gate_lock_order(self):
        entered, acquired, done = threading.Event(), threading.Event(), threading.Event()
        inversions, workers = [], []
        def recovery_lock_order():
            entered.set()
            # Executor save/recovery obtains its lock before the journal lock.
            with self.engine.executor.lock:
                acquired.set()
                if self.store.lock.acquire(timeout=.2):
                    self.store.lock.release()
                else:
                    inversions.append('executor_waits_store_while_gate_waits_executor')
            done.set()
        def change(rows):
            worker = threading.Thread(target=recovery_lock_order)
            workers.append(worker)
            worker.start()
            self.assertTrue(entered.wait(1))
            acquired.wait(.02)
        try:
            pair, errors = self.after_sent(change)
        finally:
            for worker in workers:
                worker.join(2)
        self.assertTrue(done.is_set())
        self.assertEqual(inversions, [])
        self.assertEqual(errors, [])
        self.assertEqual(pair['state'], 'OPEN')
        self.assertEqual(len([r for r in self.trace if r[0] == 'place']), 2)

    def test_opening_config_checks_do_not_load_full_history(self):
        for n in range(257):
            self.store.pair(dict(id='old-'+str(n), state='SHADOW', last_books=['unused']))
        pairs = self.store.pairs
        def active_only(active=False):
            self.assertTrue(active, 'opening checks loaded full pair history')
            return pairs(active=True)
        with patch.object(self.store, 'all_orders', side_effect=AssertionError('full orders')), patch.object(
                self.store, 'pairs', side_effect=active_only):
            pair, errors = self.after_sent(lambda rows: None)
        self.assertEqual(errors, [])
        self.assertEqual(pair['state'], 'OPEN')
        self.assertEqual(len([r for r in self.trace if r[0] == 'place']), 2)

    def test_streaming_summary_keeps_frozen_risk_settings(self):
        pair = self.engine.ingest(self.trigger())
        self.assertEqual(pair['state'], 'OPEN')
        Settings(self.store).update(dict(max_equity_drop_usd=10, daily_loss_stop_usd=1,
                                        total_loss_stop_usd=1), source='admin_api')
        summary = JournalView(Path(self.tmp.name)/'exec.db', {}, clock=lambda:self.now[0]).get('summary', {})
        self.assertEqual(summary['effective_risk_limits'], dict(max_equity_drop_usd=150,
                         daily_loss_stop_usd=20, total_loss_stop_usd=100))
        self.assertEqual(summary['config_version'], 1)
        self.assertEqual(summary['caps']['open_pairs'], 1)


if __name__ == '__main__':
    unittest.main()
