"""Bounded baseline work must not own the account/admission lock for a round."""
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path
import random
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.crossex_exec import baseline as module
from tools.crossex_exec.baseline import Baseline, BATCH_URLS, pair_key
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.storage import Store
from round27_fixtures import LegacyBaseline
from test_exec import setUpModule, tearDownModule


NOW = 1794556800.0


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'exec.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.old = Store(Path(self.tmp.name)/'old.db')
        self.addCleanup(self.old.close)
        self.now = NOW
        self.baseline = Baseline(self.store, lambda: self.now)
        self.legacy = LegacyBaseline(self.old, lambda: self.now)

    def seed(self, bases, count=721):
        rng = random.Random(27)
        for base in bases:
            for a, b in combinations(sorted(BATCH_URLS), 2):
                key = pair_key(base, a, b)
                rows = [(key, int((NOW-n*30)//30), NOW-n*30,
                         str(rng.randrange(-100000, 100000)/1000)) for n in range(count)]
                for store in (self.store, self.old):
                    store.add_baseline_samples(rows, NOW)

    def compare(self, bases):
        for base in bases:
            for a, b in combinations(sorted(BATCH_URLS), 2):
                for buy, sell in ((a, b), (b, a)):
                    self.assertEqual(self.baseline.status(base, buy, sell),
                                     self.legacy.status(base, buy, sell), (base, buy, sell, self.now))

    def engine(self, bases):
        mids = {ex: {base: str(100+n/100) for base in bases}
                for n, ex in enumerate(BATCH_URLS)}
        market = SimpleNamespace(sample_quotes=lambda minimum: (mids, bases))
        engine = Engine(self.store, market, SimpleNamespace(cross=None, bitget=None),
                        config={'CROSSEX_RISK_SCOPE': 'dedicated_account'},
                        clock=lambda: self.now, stop_file=Path(self.tmp.name)/'STOP')
        snapshot = dict(positions={}, margins=['4', '4'], upnl='0',
                        equity_by_venue={'crossex': '1000', 'bitget': '30000'},
                        notionals={}, liabilities=[])
        engine.read_account_snapshot = lambda: snapshot
        engine.limits.initialize(snapshot)
        self.addCleanup(engine.close)
        return engine

    def test_scaled_round_allows_account_refresh_and_store_reads_during_statistics(self):
        bases = tuple('COIN'+str(n) for n in range(8))  # 120 pairs x 721
        self.seed(bases)
        engine = self.engine(bases)
        entered, release = threading.Event(), threading.Event()
        original = module.median

        def slow(values):
            if threading.current_thread().name.startswith('baseline') and not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise AssertionError('statistics_not_released')
            return original(values)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='baseline') as background, \
             ThreadPoolExecutor(max_workers=2) as foreground, patch.object(module, 'median', slow):
            work = background.submit(engine.refresh_baselines)
            try:
                self.assertTrue(entered.wait(3))
                account = foreground.submit(engine.refresh_accounts)
                read = foreground.submit(self.store.meta, 'state')
                account.result(timeout=.75)
                read.result(timeout=.75)
                self.assertEqual(engine.account_cache.read()['captured_at'], NOW)
                self.assertFalse(work.done())
            finally:
                release.set()
                work.result(timeout=10)

    def test_decimal_direction_even_odd_and_window_edges_match_frozen_implementation(self):
        bases = ('BTC', 'ETH')
        self.seed(bases)
        for seconds in (0, .01, 30, 90, 90.01, 599, 600, 21600, 21600.01, -30):
            self.now = NOW+seconds
            self.compare(bases)

    def test_duplicate_bucket_backfill_edit_delete_and_other_connection_match_db(self):
        bases = ('BTC',)
        self.seed(bases, 400)
        self.compare(bases)
        key = pair_key('BTC', 'binance', 'bitget')
        # The first row in a bucket wins; future rows and a backfill are valid DB data.
        for at, value in ((NOW+1, '999'), (NOW+90, '79.99999999999999999999999999'),
                          (NOW-15000, '-80.00000000000000000000000001')):
            for store in (self.store, self.old):
                store.add_baseline_samples([(key, int(at//30), at, value)], NOW)
            self.compare(bases)
        for sql, params in (
            ('UPDATE baseline_samples SET bps=? WHERE pair_key=? AND bucket=?', ('40', key, int(NOW//30))),
            ('DELETE FROM baseline_samples WHERE pair_key=? AND bucket=?', (key, int((NOW-60)//30))),
        ):
            # A second connection and direct SQL must invalidate a warm cache too.
            with Store(self.path) as writer:
                writer.db.execute(sql, params)
            self.old.db.execute(sql, params)
            self.compare(bases)
        self.now += 90
        self.compare(bases)
        self.baseline = Baseline(self.store, lambda: self.now)
        self.compare(bases)

    def test_record_quotes_removal_empty_round_and_latches_match(self):
        self.seed(('BTC', 'ETH'), 361)
        for seconds, mids, bases in (
            (0, {'binance': {'BTC': '100'}, 'bitget': {'BTC': '100.8'}}, ('BTC', 'ETH')),
            (30, {'binance': {'BTC': '100'}, 'bitget': {'BTC': '100.4'}}, ('BTC',)),
            (600, {}, ('BTC',)),
        ):
            self.now = NOW+seconds
            for baseline in (self.baseline, self.legacy):
                baseline.record(mids, bases)
            for key in ('baseline_status', 'baseline_quotes'):
                self.assertEqual(self.store.meta(key), self.old.meta(key))

    def test_close_during_calculation_returns_promptly_and_prevents_late_publication(self):
        self.seed(('BTC',), 361)
        engine = self.engine(('BTC',))
        entered, release = threading.Event(), threading.Event()
        original = module.median

        def slow(values):
            if not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise AssertionError('statistics_not_released')
            return original(values)

        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(module, 'median', slow):
            work = pool.submit(engine.refresh_baselines)
            try:
                self.assertTrue(entered.wait(3))
                pool.submit(engine.close).result(timeout=.75)
                changes = self.store.db.total_changes
            finally:
                release.set()
                work.result(timeout=10)
            self.assertEqual(self.store.db.total_changes, changes)

    def test_sampling_delay_still_revokes_quotes(self):
        engine = self.engine(('BTC',))
        engine.refresh_baselines()
        sample = engine.market.sample_quotes

        def delayed(minimum):
            self.now += 21
            return sample(minimum)

        engine.market.sample_quotes = delayed
        engine.refresh_baselines()
        self.assertEqual(self.baseline.latest('BTC')['mids'], {})


if __name__ == '__main__':
    unittest.main()
