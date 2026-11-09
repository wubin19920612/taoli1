"""PM G1-G3 regressions: actual PublicClient, workers, virtual clock, loopback."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import copy
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
from urllib.parse import parse_qs, urlsplit

from tools.fill_sim import __main__ as app
from tools.fill_sim.network import Limiter, PublicClient, get_json
from tools.fill_sim.scheduler import DaemonPool, RequestQueue
from tools.fill_sim.tests import test_fill_sim as base


class ControlledTransport:
    """Real threads; only network latency and wall clock are virtualized."""
    def __init__(self, clock, latency=.3):
        self.clock, self.latency = clock, latency
        self.local = threading.local()
        self.tasks, self.sent = [], []
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.failure = False

    def submit(self, fn, *args):
        task = dict(stage=threading.Event(), release=threading.Event(), done=threading.Event(), due=float("inf"))
        self.tasks.append(task)
        def run():
            self.local.task = task
            try:
                return fn(*args)
            finally:
                task["done"].set()
                task["stage"].set()
        task["future"] = self.pool.submit(run)
        return task["future"]

    def __call__(self, url):
        task = self.local.task
        self.sent.append((self.clock(), url))
        task["due"] = self.clock() + self.latency
        task["stage"].set()
        if not task["release"].wait(5):
            raise AssertionError("virtual transport not released")
        if self.failure:
            raise OSError("remote_outage")
        gate = "gateio" in url
        return {"bids": [["99" if gate else "102", "10000"]],
                "asks": [["100" if gate else "103", "10000"]]}

    def settle(self):
        for task in self.tasks:
            if not task["stage"].wait(2):
                raise AssertionError("worker did not start")

    def complete(self):
        ready = [t for t in self.tasks if not t["done"].is_set() and t["due"] <= self.clock()+1e-7]
        for task in ready:
            task["release"].set()
        for task in ready:
            task["future"].result(timeout=2)
        self.tasks = [t for t in self.tasks if not t["done"].is_set()]

    def shutdown(self, wait=True):
        for task in self.tasks:
            task["release"].set()
        self.pool.shutdown(wait=wait)


def assert_windows(case, sent):
    for start, _ in sent:
        rows = [url for at, url in sent if start <= at < start+1]
        counts = Counter("gate" if "gateio" in u else "aster" for u in rows)
        case.assertLessEqual(len(rows), 8)
        case.assertTrue(all(n <= 2 for n in counts.values()), counts)


class TrackingTests(base.OfflineCase):
    setUp = base.EngineTests.setUp
    open_three = base.EngineTests.open_three

    def four_groups(self):
        self.open_three()
        seed = copy.deepcopy(next(iter(self.engine.positions.values())))
        self.engine.positions.clear()
        self.engine.pending.clear()
        self.engine.cache.clear()
        self.engine.want_specs.clear()
        start = self.clock()
        for i in range(4):
            p = copy.deepcopy(seed)
            p.update(id="p%d" % i, event_id="e%d" % i, pair_key="C%d|aster|gate" % i,
                     symbol="C%d" % i, opened_at=start, next_track=start,
                     last_good=[start, start], failed_since=[None, None])
            p["markets"][0]["raw_symbol"] = "C%d_USDT" % i
            p["markets"][1]["raw_symbol"] = "C%dUSDT" % i
            self.engine.positions[p["id"]] = p
        transport = ControlledTransport(self.clock)
        self.addCleanup(transport.shutdown)
        client = PublicClient(Limiter(clock=self.clock), transport)
        self.engine.client = client
        self.engine.requests = RequestQueue(client, self.clock)
        self.engine.executor = transport
        return start, transport

    def advance_run(self, start, duration, transport):
        for i in range(round(duration/.05)+1):
            self.clock.now = start + i*.05
            transport.complete()
            self.engine.tick()
            transport.settle()

    def test_four_gate_aster_groups_65_seconds_no_starvation_or_false_exit(self):
        start, transport = self.four_groups()
        self.advance_run(start, 65, transport)
        by_market = defaultdict(list)
        for at, url in transport.sent:
            q = parse_qs(urlsplit(url).query)
            raw = (q.get("contract") or q.get("symbol"))[0]
            by_market[raw].append(at)
        self.assertEqual(len(by_market), 8)
        counts = {}
        for raw, times in sorted(by_market.items()):
            counts[raw] = len(times)
            self.assertGreaterEqual(len(times), 15, raw)
            self.assertLessEqual(max(b-a for a,b in zip([start]+times, times+[start+65])), 6, raw)
        self.assertEqual(len(self.engine.positions), 4)
        self.assertFalse(any(p.get("closed_at") is not None for p in self.store.rows("positions")))
        for p in self.engine.positions.values():
            self.assertEqual(p["failed_since"], [None, None])
            self.assertLessEqual(start+65-min(p["last_good"]), 6)
        assert_windows(self, transport.sent)
        print("FOUR_GROUPS seconds=65 latency=.3 sends=%s open=4 closed=0" % counts)

    def test_local_delay_over_60_seconds_is_recorded_and_not_a_stale_exit(self):
        start, transport = self.four_groups()
        # Lower rate is local congestion, not an exchange outage.
        transport_client = self.engine.client
        transport_client.limiter = Limiter(.01, 8, clock=self.clock)
        self.advance_run(start, 65, transport)
        self.assertEqual(len(self.engine.positions), 4)
        delayed = [p for p in self.engine.positions.values() if p.get("tracking_status") == "rate_limited_delay"]
        self.assertEqual(len(delayed), 4)
        self.assertTrue(all(p["failed_since"] == [None, None] for p in delayed))
        self.assertTrue(all(p["next_track"] <= start+5 for p in delayed))
        self.assertFalse(any(p.get("closed_at") for p in self.store.rows("positions")))
        self.assertEqual(len(transport.sent), 2)

    def test_local_wait_is_subtracted_from_an_existing_real_failure_timer(self):
        start, transport = self.four_groups()
        for p in self.engine.positions.values():
            p["failed_since"] = [start-30, start-30]
        self.engine.client.limiter = Limiter(.01, 8, clock=self.clock)
        self.advance_run(start, 65, transport)
        self.assertEqual(len(self.engine.positions), 4)
        for p in self.engine.positions.values():
            self.assertTrue(all(t is None or self.clock()-t < 31 for t in p["failed_since"]))
        self.assertFalse(any(p.get("closed_at") for p in self.store.rows("positions")))

    def test_one_recovered_leg_clears_failure_while_partner_waits_locally(self):
        start, transport = self.four_groups()
        # Gate can send; Aster has a previous local send occupying its low-rate window.
        limiter = Limiter(clock=self.clock)
        limiter.rates["aster"] = .01
        limiter.history["aster"].append(start)
        self.engine.client.limiter = limiter
        for p in self.engine.positions.values():
            p["failed_since"] = [start-30, None]
        self.advance_run(start, 65, transport)
        self.assertEqual(len(self.engine.positions),4)
        for p in self.engine.positions.values():
            self.assertEqual(p["failed_since"], [None,None])
            self.assertEqual(p["tracking_status"],"rate_limited_delay")
        self.assertEqual(len(transport.sent),4)
        self.assertTrue(all("gateio" in url for _,url in transport.sent))

    def test_expired_opening_cancels_queued_depth_and_never_opens_late(self):
        self.client.limiter.rates["gate"] = .001
        self.client.limiter.history["gate"].append(self.clock())
        self.engine.ingest(base.event(self.clock))
        self.assertIn("e1",self.engine.jobs)
        self.clock.advance(121)
        self.engine.tick()
        self.assertFalse(self.engine.positions)
        self.assertFalse(self.engine.pending)
        self.assertFalse(self.engine.jobs)
        self.assertEqual(len(list(self.store.rows("fills"))),12)
        self.assertTrue(all(f["status"]=="skipped_stale_trigger" for f in self.store.rows("fills")))
        self.assertNotIn("gate",self.calls)

    def test_real_remote_outage_still_closes_at_60_seconds(self):
        start, transport = self.four_groups()
        transport.failure = True
        self.advance_run(start, 65, transport)
        self.assertFalse(self.engine.positions)
        closed = [p for p in self.store.rows("positions") if p["id"].startswith("p")]
        self.assertEqual(len(closed), 4)
        self.assertTrue(all(p["exit_reason"] == "stale_exit" for p in closed))


class FairnessTests(unittest.TestCase):
    def test_every_four_admitted_sends_has_an_opening_under_continuous_tracking(self):
        clock = base.Clock(0)
        transport = ControlledTransport(clock)
        self.addCleanup(transport.shutdown)
        client = PublicClient(Limiter(clock=clock), transport)
        requests = RequestQueue(client, clock)
        futures = []
        for i in range(80):
            ex = "gate" if i%2 == 0 else "aster"
            futures.append((0, requests.submit(ex, "https://%s/track/%d" % ("gateio" if ex=="gate" else "aster", i), 0)))
        for i in range(40):
            ex = "gate" if i%2 == 0 else "aster"
            futures.append((1, requests.submit(ex, "https://%s/open/%d" % ("gateio" if ex=="gate" else "aster", i), 1)))
        for i in range(1401):
            clock.now = i*.05
            transport.complete()
            requests.pump(transport)
            transport.settle()
        self.assertTrue(all(f.done() for _,f in futures))
        self.assertEqual(len(transport.sent), 120)
        ordered = sorted(futures, key=lambda row: row[1].send_order)
        priorities = [priority for priority, _ in ordered]
        self.assertEqual(priorities[:4], [0,0,0,1])
        # Open requests remain queued throughout the first 80 tracking sends.
        last_track = max(i for i,p in enumerate(priorities) if p == 0)
        for i in range(last_track-2):
            self.assertIn(1, priorities[i:i+4], (i, priorities[i:i+4]))
        assert_windows(self, transport.sent)
        print("FAIRNESS sends=120 tracking=80 opening=40 every_four=PASS")

    def test_fourth_slot_waits_for_opening_venue_instead_of_being_stolen(self):
        clock = base.Clock(0)
        sent = []
        client = PublicClient(Limiter(clock=clock), lambda url: (sent.append(url) or {}))
        requests = RequestQueue(client, clock)
        pool = base.ImmediateExecutor()
        client.fetch("gate", "warm1")
        client.fetch("gate", "warm2")
        for ex in ("aster", "binance", "bybit", "okx"):
            requests.submit(ex, "track-"+ex, 0)
        opening = requests.submit("gate", "open-gate", 1)
        requests.pump(pool)
        self.assertEqual(sent, ["warm1","warm2","track-aster","track-binance","track-bybit"])
        self.assertFalse(opening.done())
        clock.advance(1)
        requests.pump(pool)
        self.assertEqual(sent[-2:], ["open-gate", "track-okx"])
        self.assertTrue(opening.done())

    def test_warmup_dependency_is_promoted_to_opening_priority(self):
        clock = base.Clock(0)
        sent = []
        requests = RequestQueue(PublicClient(Limiter(clock=clock), lambda url: (sent.append(url) or {})),clock)
        future = requests.submit("gate","needed-spec",2)
        for ex in ("aster","binance","bybit","okx"):
            requests.submit(ex,"track-"+ex,0)
        requests.promote(future,1)
        requests.pump(base.ImmediateExecutor())
        self.assertEqual(sent,["track-aster","track-binance","track-bybit","needed-spec"])
        self.assertTrue(future.done())

    def test_cancelled_funding_chain_cannot_spend_an_opening_slot(self):
        from tools.fill_sim.core import Market
        from tools.fill_sim.funding import queue_funding
        clock = base.Clock(0)
        sent = []
        requests = RequestQueue(PublicClient(Limiter(clock=clock), lambda url: (sent.append(url) or {})),clock)
        future = queue_funding(requests,Market("aster","future","BTCUSDT"),clock)
        self.assertTrue(future.cancel())
        requests.pump(base.ImmediateExecutor())
        self.assertEqual(sent,[])
        self.assertEqual(requests.send_count,0)
        self.assertFalse(requests.heap)

    def test_smoke_client_uses_same_gate_without_removed_reservation_api(self):
        from tools.fill_sim.smoke import SmokeClient
        clock, sent = base.Clock(0), []
        client = SmokeClient(Limiter(clock=clock),lambda url: (sent.append(clock()) or {}))
        with patch("tools.fill_sim.smoke.time.sleep",side_effect=clock.advance):
            for _ in range(3):
                client.fetch("gate","https://gateio/test",reserved=True)
        self.assertEqual(len(sent),3)
        self.assertEqual(sent[:2],[0,0])
        self.assertGreaterEqual(sent[2],1)
        client.limiter.throttled("gate")
        from tools.fill_sim.network import RateLimited
        with self.assertRaisesRegex(RateLimited,"exchange_paused"):
            client.fetch("gate","https://gateio/test")
        self.assertEqual(len(sent),3)


class MemoryTests(unittest.TestCase):
    def test_service_cgroup_path_and_fallback_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            member = root / "membership"
            member.write_text("0::/system.slice/fill-sim.service\n")
            memory = root / "system.slice/fill-sim.service/memory.current"
            memory.parent.mkdir(parents=True)
            memory.write_text(str(121*1024**2))
            # Root/host usage must not override the service's value.
            (root / "memory.current").write_text("1")
            self.assertEqual(app.service_memory(root, member), (121, "cgroup_memory.current", str(memory.resolve())))
            memory.write_text("invalid")
            with patch.object(app, "rss_mb", return_value=17):
                self.assertEqual(app.service_memory(root, member), (17, "process_rss_fallback", None))
            member.write_text("0::/../../outside\n")
            with patch.object(app, "rss_mb", return_value=18):
                self.assertEqual(app.service_memory(root, member), (18, "process_rss_fallback", None))

    def test_fallback_is_written_to_stop_status(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app,"service_memory",return_value=(17,"process_rss_fallback",None)), \
                patch.object(app,"guard",return_value=("test_guard",{})):
            self.assertEqual(app.main(["run","--db",temp+"/run.db","--out",temp+"/out"]),78)
            state=json.loads(Path(temp,"out/status.json").read_text())
            self.assertEqual(state["memory_source"],"process_rss_fallback")
            self.assertEqual(state["memory_mb"],17)
            self.assertIsNone(state["memory_path"])

    def test_cgroup_guard_writes_source_and_exits_78_even_when_rss_is_small(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, "rss_mb", return_value=1), \
                patch.object(app, "service_memory", return_value=(121, "cgroup_memory.current", "/service/memory.current")), \
                patch.object(app, "guard", return_value=(None, {})):
            self.assertEqual(app.main(["run", "--db", temp+"/run.db", "--out", temp+"/out"]),78)
            state = json.loads(Path(temp,"out/status.json").read_text())
            self.assertEqual(state["memory_mb"],121)
            self.assertEqual(state["memory_source"],"cgroup_memory.current")
            self.assertEqual(state["memory_path"],"/service/memory.current")
            self.assertEqual(state["stop_reason"],"memory_limit")


class LoopbackTests(unittest.TestCase):
    def setUp(self):
        self.active = 0
        self.lock = threading.Lock()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                with owner.lock:
                    owner.active += 1
                try:
                    if self.path == "/headers":
                        self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                    else:
                        self.send_response(200)
                        if self.path == "/chunked":
                            self.send_header("Transfer-Encoding", "chunked")
                        else:
                            self.send_header("Content-Length", "100000")
                        self.end_headers()
                    for _ in range(1500):
                        self.wfile.write(b" " if self.path != "/chunked" else b"1\r\n \r\n")
                        self.wfile.flush()
                        time.sleep(.01)
                except OSError:
                    pass
                finally:
                    with owner.lock:
                        owner.active -= 1
        self.server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_repeated_bytewise_deadlines_close_connections_and_leave_no_workers(self):
        before = {t.ident for t in threading.enumerate()}
        pool = DaemonPool(2, "deadline-test")
        self.assertTrue(all(t.daemon for t in pool.threads))
        try:
            with patch("subprocess.Popen", side_effect=AssertionError("per-request process forbidden")):
                for path in ("/body","/headers","/chunked")*2:
                    start = time.monotonic()
                    future = pool.submit(lambda: get_json(self.url+path,total_timeout=.25))
                    with self.assertRaisesRegex(TimeoutError,"request_deadline"):
                        future.result(timeout=2)
                    self.assertLess(time.monotonic()-start,1)
            limit = time.monotonic()+2
            while self.active and time.monotonic() < limit:
                time.sleep(.01)
            self.assertEqual(self.active,0)
        finally:
            pool.shutdown()
        self.assertTrue(all(not t.is_alive() for t in pool.threads))
        self.assertEqual({t.ident for t in threading.enumerate()}, before)

    def test_default_eight_second_deadline_is_not_reset_by_received_bytes(self):
        start = time.monotonic()
        client = PublicClient(Limiter())
        with self.assertRaisesRegex(TimeoutError,"request_deadline"):
            client.fetch("gate",self.url+"/body")
        elapsed = time.monotonic()-start
        self.assertGreaterEqual(elapsed,7.9)
        self.assertLessEqual(elapsed,9)
        self.assertEqual(client.limiter.snapshot()["gate"]["timeouts"],1)
        print("HTTP_DEFAULT_DEADLINE seconds=%.3f timeout_count=1" % elapsed)

    def test_guard_does_not_join_permanently_blocked_daemon_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            code = """
import sys, threading, time
from pathlib import Path
from tools.fill_sim import __main__ as app
from tools.fill_sim.network import PublicClient, ShadowAPI
root=sys.argv[1]
started=time.monotonic()
def blocked(*a, **k):
    threading.Event().wait(60)
PublicClient._perform=blocked
ShadowAPI.poll=blocked
def guard(path, now):
    if time.monotonic()-started > 1:
        Path(root,'triggered').write_text(str(time.monotonic()))
        return 'test_guard',{}
    return None,{}
app.guard=guard
sys.exit(app.main(['run','--db',root+'/run.db','--out',root+'/out']))
"""
            start = time.monotonic()
            proc = subprocess.Popen([sys.executable,"-B","-c",code,temp], stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                stdout,stderr = proc.communicate(timeout=12)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
            self.assertEqual(proc.returncode,78,(stdout,stderr))
            elapsed = time.monotonic()-float(Path(temp,"triggered").read_text())
            self.assertLessEqual(elapsed,10)
            self.assertLess(time.monotonic()-start,12)
            self.assertEqual(json.loads(Path(temp,"out/status.json").read_text())["stop_reason"],"test_guard")
            print("DAEMON_GUARD_EXIT code=78 seconds_after_trigger=%.3f" % elapsed)
