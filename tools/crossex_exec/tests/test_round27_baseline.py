"""Bounded baseline work must not own the account/admission lock for a round."""
from concurrent.futures import ThreadPoolExecutor
from decimal import localcontext
from itertools import combinations
from pathlib import Path
import random
from statistics import median
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.crossex_exec import baseline as module
from tools.crossex_exec.baseline import Baseline, BATCH_URLS, pair_key
from tools.crossex_exec.baseline_cache import WindowCache
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.storage import Store, BaselineReader
from tools.fill_sim.core import dec
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

    def test_warm_window_reads_only_new_buckets_and_unchanged_round_skips_statistics(self):
        self.seed(('BTC',))
        self.baseline.status('BTC', 'binance', 'bitget')
        self.legacy.status('BTC', 'binance', 'bitget')
        observed = []
        original = self.store.baseline_samples

        def read(*args):
            rows = original(*args)
            observed.append(len(rows))
            return rows

        key = pair_key('BTC', 'binance', 'bitget')
        self.now += 30
        for store in (self.store, self.old):
            store.add_baseline_samples([(key, int(self.now//30), self.now, '0.000000000000000000000000001')], self.now)
        with patch.object(self.store, 'baseline_samples', side_effect=read):
            self.assertEqual(self.baseline.status('BTC', 'binance', 'bitget'),
                             self.legacy.status('BTC', 'binance', 'bitget'))
        self.assertLessEqual(max(observed), 2)
        with patch.object(module, 'median', side_effect=AssertionError('unchanged_window_recalculated')):
            self.baseline.status('BTC', 'binance', 'bitget')

    def test_cache_eviction_stays_within_budget_and_recovers_from_database(self):
        self.baseline._cache = WindowCache(32*1024)
        self.seed(('BTC', 'ETH'))
        self.compare(('BTC', 'ETH'))
        self.assertLessEqual(self.baseline._cache.bytes, 32*1024)
        self.assertLess(len(self.baseline._cache.entries), 30)
        self.compare(('BTC', 'ETH'))

    def test_rolled_back_observation_cannot_poison_reused_database_revision(self):
        key = pair_key('BTC', 'binance', 'bitget')
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with self.store.transaction():
                self.store.add_baseline_samples([(key, int(NOW//30), NOW, '79')], NOW)
                self.assertEqual(self.baseline.status('BTC', 'binance', 'bitget')['baseline_bps'], '79')
                raise RuntimeError('rollback')
        for store in (self.store, self.old):
            store.add_baseline_samples([(key, int(NOW//30), NOW, '0')], NOW)
        self.compare(('BTC',))

    def test_sample_changed_during_statistics_retries_without_publishing_stale_result(self):
        self.seed(('BTC',), 361)
        key = pair_key('BTC', 'binance', 'bitget')
        original = module.median
        changed = []

        def mutate(values):
            if not changed:
                changed.append(True)
                for store in (self.store, self.old):
                    store.db.execute('UPDATE baseline_samples SET bps=? WHERE pair_key=?', ('80', key))
            return original(values)

        with patch.object(module, 'median', mutate):
            self.assertEqual(self.baseline.status('BTC', 'binance', 'bitget'),
                             self.legacy.status('BTC', 'binance', 'bitget'))
        self.assertTrue(self.store.baseline_state(key)['blocked'])

    def test_failed_calculation_leaves_reference_set_revoked(self):
        engine = self.engine(('BTC',))
        engine.refresh_baselines()
        self.assertTrue(self.baseline.latest('BTC')['mids'])
        self.now += 30
        with patch.object(module, 'median', side_effect=RuntimeError('statistics_failed')):
            with self.assertRaises(RuntimeError):
                engine.refresh_baselines()
        self.assertEqual(self.baseline.latest('BTC')['mids'], {})

    def test_retention_is_batched_and_closed_callback_stops_before_more_writes(self):
        key = pair_key('BTC', 'binance', 'bitget')
        rows = [(key, int((NOW-90000-n*30)//30), NOW-90000-n*30, '0') for n in range(400)]
        self.store.db.executemany('INSERT INTO baseline_samples VALUES (?,?,?,?)', rows)
        calls = []

        def active():
            calls.append(True)
            return len(calls) == 1

        self.assertFalse(self.store.add_baseline_samples([], NOW, active))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM baseline_samples').fetchone()[0], 272)
        self.store.add_baseline_samples([], NOW)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM baseline_samples').fetchone()[0], 0)

    def test_decimal_context_and_noncanonical_imports_never_reuse_wrong_statistics(self):
        key = pair_key('BTC', 'binance', 'bitget')
        rows = [(key, int((NOW-n*30)//30), NOW-n*30, value) for n, value in enumerate(
            ('1.12345678901234567890123456789', '2.12345678901234567890123456789'))]
        for store in (self.store, self.old):
            store.add_baseline_samples(rows, NOW)
        for precision in (28, 12, 38):
            with localcontext() as context:
                context.prec = precision
                self.compare(('BTC',))
        for store in (self.store, self.old):
            store.db.execute('UPDATE baseline_samples SET bps=? WHERE pair_key=?', ('1\n', key))
        self.compare(('BTC',))
        self.compare(('BTC',))

    def test_empty_combinations_do_not_displace_windows_and_entry_limit_is_independent(self):
        self.seed(('BTC',), 1)
        with patch('tools.crossex_exec.baseline_cache.CACHE_ENTRIES', 3):
            self.compare(('BTC',))
            self.assertEqual(len(self.baseline._cache.entries), 3)
            self.compare(('EMPTY',))
            self.assertEqual(len(self.baseline._cache.entries), 3)

    def test_computation_delay_revokes_quotes_without_retimestamping_samples(self):
        engine = self.engine(('BTC',))
        original = module.median
        delayed = []

        def slow(values):
            if not delayed:
                delayed.append(True)
                self.now += 21
            return original(values)

        with patch.object(module, 'median', slow):
            engine.refresh_baselines()
        self.assertEqual(self.baseline.latest('BTC'), dict(at=NOW, mids={}))
        self.assertEqual(self.store.baseline_state(pair_key('BTC', 'binance', 'bitget'))['latest_at'], NOW)

    def test_round_retry_cannot_overwrite_a_newer_on_demand_window(self):
        self.seed(('BTC',), 361)
        key = pair_key('BTC', 'binance', 'bitget')
        original = self.baseline._calculate
        changed = []
        newer = []

        def calculate(identity, *args, **kwargs):
            result = original(identity, *args, **kwargs)
            if identity == key and not changed:
                changed.append(True)
                self.now += 30
                self.store.add_baseline_samples([(key, int(self.now//30), self.now, '81')], self.now)
                newer.append(Baseline(self.store, lambda: self.now).status('BTC', 'binance', 'bitget'))
            return result

        with patch.object(self.baseline, '_calculate', side_effect=calculate):
            self.baseline.record({}, ('BTC',), at=NOW)
        self.assertEqual(self.store.baseline_state(key), newer[0])

    def test_full_scan_larger_than_budget_keeps_a_useful_warm_working_set(self):
        self.baseline._cache = WindowCache(16*1024)
        self.seed(('BTC',), 100)
        self.baseline.record({}, ('BTC',))
        keys = set(self.baseline._cache.entries)
        self.assertTrue(keys)
        original = BaselineReader.baseline_samples
        with patch.object(BaselineReader, 'baseline_samples', autospec=True, side_effect=original) as read:
            self.baseline.record({}, ('BTC',))
        self.assertEqual(read.call_count, 15)
        full_reads = sum(call.args[2] == NOW-21600 for call in read.call_args_list)
        self.assertLessEqual(full_reads, 15-len(keys))
        self.assertEqual(set(self.baseline._cache.entries), keys)

    def test_close_during_sampling_does_not_touch_the_closed_database(self):
        engine = self.engine(('BTC',))
        sample = engine.market.sample_quotes

        def close(minimum):
            engine.close()
            self.store.close()
            return sample(minimum)

        engine.market.sample_quotes = close
        engine.refresh_baselines()

    def test_slow_readonly_window_does_not_hold_the_account_lock(self):
        self.seed(('BTC',), 361)
        engine = self.engine(('BTC',))
        entered, release = threading.Event(), threading.Event()
        original = BaselineReader.baseline_samples

        def read(reader, *args):
            if not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise AssertionError('read_not_released')
            return original(reader, *args)

        with ThreadPoolExecutor(max_workers=2) as pool, \
             patch.object(BaselineReader, 'baseline_samples', read):
            work = pool.submit(engine.refresh_baselines)
            try:
                self.assertTrue(entered.wait(3))
                pool.submit(engine.refresh_accounts).result(timeout=.75)
                self.assertEqual(engine.account_cache.read()['captured_at'], NOW)
            finally:
                release.set()
                work.result(timeout=10)

    def test_mad_ties_keep_legacy_decimal_scale_and_rounding(self):
        rng = random.Random(2701)
        key = pair_key('BTC', 'binance', 'bitget')
        values = ('40', '40.00', '-40.0', '-40.000', '0', '0.00000',
                  '79.99999999999999999999999999', '-0.000000000000000000000000001')
        for count in (1, 2, 3, 300, 361, 720, 721):
            rows = [(key, int((NOW-n*30)//30), NOW-n*30, rng.choice(values)) for n in range(count)]
            for store in (self.store, self.old):
                store.db.execute('DELETE FROM baseline_samples')
                store.add_baseline_samples(rows, NOW)
            for precision in (12, 28, 38):
                with localcontext() as context:
                    context.prec = precision
                    for a, b in (('binance', 'bitget'), ('bitget', 'binance')):
                        self.assertEqual(self.baseline.status('BTC', a, b), self.legacy.status('BTC', a, b))

    def test_rounded_even_center_outside_middle_bracket_still_matches(self):
        key = pair_key('BTC', 'binance', 'bitget')
        rows = [(key, int((NOW-n*30)//30), NOW-n*30, value) for n, value in enumerate(
            ('100.001', '100.002', '100.003', '100.004'))]
        for store in (self.store, self.old):
            store.add_baseline_samples(rows, NOW)
        with localcontext() as context:
            context.prec = 2
            self.compare(('BTC',))

    def test_mad_matches_legacy_sort_across_random_scales_and_rounding(self):
        rng = random.Random(2702)
        for _ in range(150):
            values = [dec(str(rng.randrange(-10**28, 10**28))+'E-'+str(rng.randrange(28)))
                      for _ in range(rng.randrange(1, 722))]
            with localcontext() as context:
                context.prec = rng.choice((2, 4, 12, 28, 38))
                context.rounding = rng.choice(('ROUND_FLOOR', 'ROUND_CEILING', 'ROUND_HALF_EVEN'))
                center = median(values)
                expected = median(abs(v-center) for v in values)
                actual = module.median_deviation(values, sorted(values), center)
                self.assertEqual(str(actual), str(expected))

    def test_large_decimal_statistic_text_is_charged_to_the_cache_budget(self):
        self.baseline._cache = WindowCache(64*1024)
        key = pair_key('BTC', 'binance', 'bitget')
        value = '1'*40000
        for store in (self.store, self.old):
            store.add_baseline_samples([(key, int(NOW//30), NOW, value)], NOW)
        self.assertEqual(self.baseline.status('BTC', 'binance', 'bitget'),
                         self.legacy.status('BTC', 'binance', 'bitget'))
        self.assertTrue(self.baseline._cache.get(key) is None, 'over_budget_window_cached')


if __name__ == '__main__':
    unittest.main()
