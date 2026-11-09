"""Regression tests for all eight read-only review findings; no public network."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools.fill_sim import __main__ as app
from tools.fill_sim.core import EXCHANGES, Market
from tools.fill_sim.network import Limiter, PublicClient, RateLimited, get_json
from tools.fill_sim.reporting import summarize
from tools.fill_sim.tests import test_fill_sim as base


class SendAdmissionTests(base.OfflineCase):
    def test_transport_delayed_after_admission_retains_its_quota(self):
        clock = base.Clock(0)
        entered, release = threading.Event(), threading.Event()
        sent = []
        def transport(url):
            if url == 'delayed':
                entered.set()
                self.assertTrue(release.wait(2))
            sent.append(clock())
            return {}
        client = PublicClient(Limiter(clock=clock), transport)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.fetch, 'gate', 'delayed', True)
            self.assertTrue(entered.wait(1))
            clock.advance(2)
            client.fetch('gate', 'new', True)
            with self.assertRaises(RateLimited):
                client.fetch('gate', 'excess', True)
            release.set()
            future.result(timeout=1)
        self.assertEqual(sent, [2, 2])
        with self.assertRaises(RateLimited):
            client.fetch('gate', 'excess', True)
        clock.advance(1)
        client.fetch('gate', 'next', True)

    def test_delayed_reservations_cannot_be_redeemed_across_windows(self):
        clock = base.Clock(0)
        sent = []
        limiter = Limiter(clock=clock)
        client = PublicClient(limiter, lambda url: (sent.append((clock(), url)) or {}))
        self.assertTrue(limiter.acquire(['gate', 'gate']))
        client.fetch('gate', 'gate', reserved=True)
        clock.advance(1)
        self.assertTrue(limiter.acquire(['gate', 'gate']))
        for _ in range(3):
            try:
                client.fetch('gate', 'gate', reserved=True)
            except RateLimited:
                pass
        self.assertEqual(len(sent), 3)
        # Irregular dispatch times, all venues, and mixed reserved/direct calls.
        for i in range(150):
            clock.advance(.037)
            for ex in EXCHANGES:
                try:
                    client.fetch(ex, ex, reserved=bool(i % 2))
                except RateLimited:
                    pass
        self.assertGreater(len(sent), 20)
        for start, _ in sent:
            counts = Counter(ex for ts, ex in sent if start <= ts < start + 1)
            self.assertLessEqual(sum(counts.values()), 8)
            self.assertTrue(all(n <= 2 for n in counts.values()), counts)

    def test_simultaneous_workers_share_actual_send_admission(self):
        clock = base.Clock(0)
        sent = []
        limiter = Limiter(clock=clock)
        client = PublicClient(limiter, lambda url: (sent.append(url) or {}))
        def fetch(ex):
            try:
                client.fetch(ex, ex, reserved=True)
            except RateLimited:
                pass
        with ThreadPoolExecutor(max_workers=24) as pool:
            list(pool.map(fetch, list(EXCHANGES) * 4))
        self.assertEqual(len(sent), 8)
        self.assertTrue(all(n <= 2 for n in Counter(sent).values()))


class EngineReviewTests(base.OfflineCase):
    setUp = base.EngineTests.setUp
    open_three = base.EngineTests.open_three

    def test_stale_and_future_eligible_triggers_count_in_coverage(self):
        for identity, offset in [('stale', -121), ('future', 6)]:
            payload = base.event(base.Clock(self.clock() + offset), identity)
            self.engine.ingest(payload)
        bad = base.event(base.Clock(self.clock()-121), 'filtered')
        bad['fake_flag'] = True
        self.engine.ingest(bad)
        rows = list(self.store.rows('events'))
        self.assertEqual([r['filter_passed'] for r in rows], [True, True, False])
        result = app.window(self.store, self.clock()-1, self.clock()+1)
        self.assertEqual(result['considered_events'], 2)
        self.assertEqual(result['still_there_rate'], 0)

    def test_unpriced_exit_preserves_known_and_unknown_funding(self):
        self.open_three()
        positions = list(self.engine.positions.values())
        positions[0]['funding_known'] = False
        positions[0]['funding'] = [None, None]
        for p in positions:
            known = p['funding_known']
            p['opened_at'] = self.clock()-172800
            self.engine._track_position(p, [base.book(99,100,self.clock(),.000001)]*2)
            self.assertEqual(p['funding_known'], known)
            self.assertEqual(p['exit_pricing'], 'unpriced')
            self.assertEqual(p['exit_reason'], 'unpriced_exit')
            self.assertFalse(p['main_eligible'])
            self.assertIsNone(p['net_bps'])
        r = summarize([], [], positions)
        self.assertEqual(r['funding_unknown'], 1)
        self.assertEqual(r['funding_observations'], 12)
        self.assertEqual(r['main']['n'], 0)
        self.assertEqual(r['provisional_price_only']['300']['n'], 0)

    def test_expired_event_removes_failed_spec_tasks_and_retries(self):
        self.engine.cache.clear()
        self.engine.ingest(base.event(self.clock))
        self.engine.tick()
        self.engine.tick()
        self.clock.advance(6)
        self.engine.tick()
        self.assertFalse(self.engine.pending)
        self.assertFalse(self.engine.want_specs)
        self.assertFalse(self.engine.spec_retry)
        calls = self.client.limiter.snapshot()
        self.clock.advance(61)
        self.engine.tick()
        self.assertEqual(self.client.limiter.snapshot(), calls)

    def test_spec_cache_capacity_idle_ttl_and_active_dependencies(self):
        from tools.fill_sim.engine import SPEC_CAPACITY, SPEC_IDLE_TTL
        self.open_three()
        active = Market.from_payload(base.event(self.clock)['buy_market_key'])
        for n in range(SPEC_CAPACITY + 20):
            m = Market('gate', 'future', 'C%d_USDT' % n)
            self.engine.cache[m.key] = dict(at=self.clock(), used_at=self.clock(),
                                          market=asdict(m), spec=asdict(base.spec()))
        self.engine._prune_specs()
        self.assertLessEqual(len(self.engine.cache), SPEC_CAPACITY)
        self.assertIn(active.key, self.engine.cache)
        self.clock.advance(SPEC_IDLE_TTL + 1)
        self.engine._prune_specs()
        self.assertEqual(set(self.engine.cache), {Market(**m).key for p in
                         self.engine.positions.values() for m in p['markets']})
        self.assertLessEqual(len(self.engine.want_specs), SPEC_CAPACITY)
        self.assertLessEqual(len(self.engine.spec_retry), SPEC_CAPACITY)

    def test_spec_queue_capacity_and_idle_ttl_without_cache_entries(self):
        from tools.fill_sim.engine import SPEC_CAPACITY, SPEC_IDLE_TTL
        self.engine.cache.clear()
        for n in range(SPEC_CAPACITY+40):
            self.engine.spec(Market('gate','future','C%d_USDT' % n))
        self.assertEqual(len(self.engine.want_specs), SPEC_CAPACITY)
        self.assertLessEqual(len(self.engine.spec_used), SPEC_CAPACITY)
        self.clock.advance(SPEC_IDLE_TTL)
        self.engine._prune_specs()
        self.assertFalse(self.engine.want_specs)
        self.assertFalse(self.engine.spec_used)

    def test_failed_spec_kept_only_until_last_dependency_disappears(self):
        self.engine.cache.clear()
        self.engine.ingest(base.event(self.clock))
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.engine.spec_retry), 2)
        self.engine.pending.clear()
        self.engine._prune_specs()
        self.assertFalse(self.engine.spec_retry)
        self.assertFalse(self.engine.want_specs)

    def test_abandoned_failed_refresh_cannot_requeue_from_stale_cache(self):
        for value in self.engine.cache.values():
            value['at'] = self.clock()-21600
            value['used_at'] = self.clock()
        self.engine.ingest(base.event(self.clock))
        self.engine.tick()
        self.engine.tick()
        self.assertTrue(self.engine.spec_retry)
        self.engine.pending.clear()
        self.engine._prune_specs()
        calls = self.client.limiter.snapshot()
        self.clock.advance(61)
        self.engine.tick()
        self.assertFalse(self.engine.cache)
        self.assertFalse(self.engine.want_specs)
        self.assertFalse(self.engine.spec_retry)
        self.assertEqual(self.client.limiter.snapshot(), calls)


class ResourceReviewTests(base.OfflineCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = base.Store(Path(self.temp.name)/'review.db')
        self.addCleanup(self.store.close)

    def test_status_scalar_queries_match_report_across_archives_and_late_closes(self):
        now = 1794206000
        with self.store.transaction():
            for i in range(4):
                t = now-86400 if i < 2 else now
                e = dict(id=str(i), t1=t, status='evaluated', filter_passed=True)
                self.store.event(e)
                f = dict(id=str(i), event_id=str(i), variant='measured', amount=300,
                         status='opened', symbol='BTC', pair='gate|aster', t1=t)
                self.store.fill(f)
                p = dict(**f, opened_at=t, closed_at=None if i==3 else now,
                         funding_known=i%2==0, main_eligible=False,
                         net_bps=1, net_usd=.1, exit_reason='converged')
                self.store.position(p)
        self.store.rotate(now)
        expected = app.window(self.store, now-100, now+1)
        with patch.object(self.store, 'rows', side_effect=AssertionError('status loaded rows')):
            actual = self.store.status_counts(now-100, now+1)
        self.assertEqual(actual, {key: expected[key] for key in actual})
        self.assertEqual(actual['events'], 2)
        self.assertEqual(actual['funding_observations'], 4)

    def test_upgrade_seeds_counter_baseline_from_limiter_and_missing_evidence_stays_unknown(self):
        with self.store.transaction():
            self.store.set_meta('limiter', {'gate': dict(rate_limits=3)})
            self.store.runtime(100, 30, 40, dict(no_429_increase=True), 3)
            self.store.runtime(130, 30, 40, {}, 3)
        self.assertTrue(self.store.runtime_window(100,130)[1]['no_429_increase'])
        self.assertIsNone(self.store.runtime_window(130,131)[1]['no_429_increase'])

    def test_unpriced_state_excludes_income_independently_of_funding(self):
        p = dict(variant='measured', amount=300, symbol='BTC', pair='gate|aster',
                 spread_tier='1-2%', closed_at=1, funding_known=True, main_eligible=True,
                 net_bps=10, net_usd=1, exit_reason='converged', exit_pricing='unpriced')
        result = summarize([], [], [p])
        self.assertEqual(result['funding_unknown_ratio'], 0)
        self.assertEqual(result['main']['n'], 0)
        self.assertEqual(result['provisional_price_only']['300']['n'], 0)

    def test_429_deltas_survive_restart_and_expire_with_window(self):
        good = dict(taoli_stalls_not_worse=True, no_429_increase=True)
        with self.store.transaction():
            self.store.runtime(1, 1, 40, good, 1)
            self.store.runtime(8*86400, 30, 40, good, 1)
        self.assertFalse(self.store.runtime_window(0,2)[1]['no_429_increase'])
        self.assertTrue(self.store.runtime_window(86400,9*86400)[1]['no_429_increase'])
        other = base.Store(self.store.path)
        try:
            with other.transaction():
                other.runtime(8*86400+30, 30, 40, good, 1)
                other.runtime(8*86400+60, 30, 40, good, 2)
            self.assertTrue(other.runtime_window(8*86400,8*86400+60)[1]['no_429_increase'])
            self.assertFalse(other.runtime_window(8*86400+60,9*86400)[1]['no_429_increase'])
            self.assertEqual(list(other.db.execute('SELECT delta FROM rate_limit_samples ORDER BY ts')),
                             [(1,), (0,), (0,), (1,)])
        finally:
            other.close()

    def test_report_six_thousand_events_peak_rss_under_twenty_mib(self):
        # Lazy inputs have the review's 12 fills/positions per event. Include
        # high-cardinality identities to catch accidental per-event retention.
        class SyntheticStore:
            def meta(self, key, default=None):
                return default
            def rows(self, table, *args, **kwargs):
                for i in range(6000):
                    if table == 'events':
                        yield dict(id=str(i), status='evaluated', filter_passed=True, t1=100)
                        continue
                    for variant in ('measured', 'plus_1s', 'leg_delay'):
                        for amount in (100, 300, 1000, 3000):
                            yield dict(id='%s:%s:%s' % (i,variant,amount), event_id=str(i),
                                       variant=variant, amount=amount, status='opened',
                                       symbol='coin'+str(i%7), pair='pair'+str(i%3), spread_tier='1-2%',
                                       t1=100, opened_at=100, closed_at=100+i%7*86400,
                                       funding_known=True, main_eligible=True,
                                       net_bps=10, net_usd=.3, shadow_net_bps=20,
                                       exit_reason='converged')
        gc.collect()
        baseline = app.rss_mb()
        peaks = [baseline]
        done = threading.Event()
        def sample():
            while not done.wait(.002):
                peaks[0] = max(peaks[0], app.rss_mb())
        sampler = threading.Thread(target=sample)
        sampler.start()
        try:
            result = app.window(SyntheticStore(), 0, 8*86400)
            peaks[0] = max(peaks[0], app.rss_mb())
        finally:
            done.set()
            sampler.join()
        delta = peaks[0] - baseline
        print('REPORT_RSS events=6000 fills=72000 positions=72000 peak_delta_mib=%.3f' % delta)
        self.assertLess(delta, 20)
        self.assertEqual(result['events'], 6000)
        self.assertEqual(result['main']['n'], 6000)
        self.assertEqual(result['funding_observations'], 72000)
        self.assertEqual(result['still_there_rate'], 1)
        self.assertEqual(result['shadow_bias'], dict(n=6000, mean=10, p10=10, p50=10, p90=10))


class DeadlineTests(unittest.TestCase):
    """Real HTTP only on loopback: continuously trickling headers and bodies."""
    def setUp(self):
        self.started = threading.Event()
        started = self.started
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                started.set()
                try:
                    if self.path == '/limited':
                        self.send_response(429)
                        self.send_header('Retry-After', '120')
                        self.end_headers()
                        return
                    if self.path == '/redirect':
                        self.send_response(302)
                        self.send_header('Location', '/body')
                        self.end_headers()
                        return
                    if self.path == '/good':
                        body = b'{"ok":true}'
                        self.send_response(200)
                        self.send_header('Content-Length', str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    if self.path == '/headers':
                        self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                    else:
                        self.send_response(200)
                        self.send_header('Content-Length', '1000000')
                        self.end_headers()
                    for _ in range(100):
                        self.wfile.write(b' ')
                        self.wfile.flush()
                        time.sleep(.1)
                except (ConnectionError, OSError):
                    pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:%d' % self.server.server_port
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_total_deadline_cuts_off_slow_headers_and_continuous_body(self):
        for path in ('/headers', '/body'):
            start = time.monotonic()
            with self.assertRaisesRegex((TimeoutError, ValueError), 'deadline'):
                get_json(self.url+path, timeout=.7)
            self.assertLess(time.monotonic()-start, 2)
            self.assertTrue(self.started.is_set())

    def test_worker_success_http_pause_and_redirect_behavior(self):
        self.assertEqual(get_json(self.url+'/good'), {'ok': True})
        client = PublicClient(Limiter())
        with self.assertRaises(RateLimited):
            client.fetch('gate', self.url+'/limited')
        self.assertGreaterEqual(client.limiter.snapshot()['gate']['paused_until']-time.time(), 119)
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as caught:
            get_json(self.url+'/redirect')
        self.assertEqual(caught.exception.code, 302)

    def test_guard_exits_78_with_inflight_http_within_ten_seconds(self):
        with tempfile.TemporaryDirectory() as temp:
            code = '''
import sys, time
from pathlib import Path
from tools.fill_sim import __main__ as app
from tools.fill_sim.network import PublicClient, get_json
root, url = sys.argv[1:]
original = app.guard
started = time.monotonic()
def guard(path, now):
    if time.monotonic()-started > 1:
        Path(root, 'triggered').write_text(str(time.monotonic()))
        return 'test_guard', {}
    return None, dict(timestamp=now, taoli_stalls_last_hour=0)
app.guard = guard
# Both executor paths use real trickling HTTP. No exchange requests.
PublicClient.fetch = lambda self, *args, **kwargs: get_json(url+'/body')
sys.exit(app.main(['run','--db',root+'/run.db','--out',root+'/out','--api-base',url]))
'''
            proc = subprocess.Popen([sys.executable, '-B', '-c', code, temp, self.url],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = proc.communicate(timeout=12)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
            self.assertTrue(self.started.is_set())
            self.assertEqual(proc.returncode, 78, stdout+stderr)
            elapsed = time.monotonic()-float(Path(temp, 'triggered').read_text())
            print('GUARD_EXIT code=%d seconds_after_trigger=%.3f' % (proc.returncode, elapsed))
            self.assertLessEqual(elapsed, 10)
            self.assertEqual(json.loads(Path(temp,'out/status.json').read_text())['stop_reason'], 'test_guard')
