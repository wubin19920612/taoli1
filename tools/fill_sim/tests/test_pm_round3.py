"""H1/H2: bounded DNS, hostname TLS, worker watchdog and independent 48h exits."""
from contextlib import closing
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from tools.fill_sim import network
from tools.fill_sim.scheduler import DaemonPool, RequestQueue
from tools.fill_sim.tests import test_fill_sim as base
from tools.fill_sim.tests import test_pm_round2 as round2


def addresses(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 0))]


def wait_until(case, predicate, timeout=2):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        time.sleep(.005)
    case.assertTrue(predicate())


class DNSTests(unittest.TestCase):
    def test_four_dns_blocked_workers_release_and_queued_real_http_completes(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        release = threading.Event()
        entered = []
        lock = threading.Lock()
        def lookup(host, *args, **kwargs):
            if host.startswith("blocked"):
                with lock:
                    entered.append(threading.current_thread())
                release.wait()
            return addresses(host, *args, **kwargs)
        resolver = network.Resolver(lookup=lookup)
        limiter = network.Limiter()
        client = network.PublicClient(limiter, lambda url: network.get_json(url, total_timeout=.2, resolver=resolver))
        queue, pool = RequestQueue(client, time.time), DaemonPool(4)
        try:
            blocked = [queue.submit(ex, "http://blocked%d.test/" % i, 0)
                       for i, ex in enumerate(("gate", "aster", "binance", "bybit"))]
            healthy = queue.submit("okx", "http://healthy.test:%d/" % server.server_port, 0)
            queue.pump(pool)
            wait_until(self, lambda: len(entered) == 4, timeout=.15)
            self.assertTrue(all(thread.daemon for thread in entered))
            self.assertFalse(healthy.done())
            for future in blocked:
                with self.assertRaisesRegex(TimeoutError, "request_deadline"):
                    future.result(timeout=1)
            self.assertEqual(limiter.sending["global"], 0)
            self.assertEqual(resolver.snapshot()["abandoned_active"], 4)
            wait_until(self, lambda: pool.snapshot()["busy"] == 0)
            queue.pump(pool)
            self.assertEqual(healthy.result(timeout=1), {})
            self.assertFalse(release.is_set())
            self.assertEqual(limiter.stats["okx"]["requests"], 1)
            print("DNS_FOUR_WORKERS abandoned=4 sending=0 queued_http=PASS")
        finally:
            release.set()
            queue.close()
            pool.shutdown()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1)
            for thread in entered:
                thread.join(timeout=1)
        self.assertTrue(all(not thread.is_alive() for thread in entered + pool.threads))
        self.assertEqual(resolver.snapshot()["abandoned_active"], 0)

    def test_blocked_dns_total_deadline_releases_quota_and_other_requests_progress(self):
        release = threading.Event()
        entered = threading.Event()
        def lookup(host, *args, **kwargs):
            if host == "blocked.test":
                entered.set()
                release.wait()
            return addresses(host, *args, **kwargs)
        resolver = network.Resolver(lookup=lookup)
        clock = base.Clock(0)
        limiter = network.Limiter(clock=clock)
        def transport(url):
            if "blocked.test" in url:
                return network.get_json(url, total_timeout=.05, resolver=resolver)
            return {"ok": True}
        queue = RequestQueue(network.PublicClient(limiter, transport), clock)
        pool = DaemonPool(4)
        try:
            blocked = queue.submit("gate", "https://blocked.test/", 0)
            started = time.monotonic()
            queue.pump(pool)
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(TimeoutError, "request_deadline"):
                blocked.result(timeout=.5)
            self.assertLess(time.monotonic() - started, .5)
            self.assertFalse(release.is_set())
            self.assertEqual(limiter.sending["gate"], 0)
            self.assertEqual(limiter.sending["global"], 0)
            self.assertEqual(limiter.stats["gate"]["timeouts"], 1)
            self.assertEqual(resolver.snapshot()["abandoned_active"], 1)
            # Existing conservative rolling window is kept; the in-flight slot is free.
            clock.advance(1.01)
            good = queue.submit("gate", "https://healthy.test/", 0)
            queue.pump(pool)
            self.assertEqual(good.result(timeout=.5), {"ok": True})
            self.assertFalse(release.is_set())
        finally:
            release.set()
            queue.close()
            pool.shutdown()
        wait_until(self, lambda: resolver.snapshot()["abandoned_active"] == 0)
        self.assertEqual(resolver.snapshot()["abandoned_total"], 1)

    def test_default_dns_wait_is_two_seconds_even_with_eight_second_budget(self):
        release = threading.Event()
        resolver = network.Resolver(lookup=lambda *a, **k: (release.wait(), addresses(*a, **k))[1])
        try:
            started = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "request_deadline"):
                network.get_json("https://blocked.test/", resolver=resolver)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 1.9)
            self.assertLess(elapsed, 3)
            self.assertEqual(resolver.snapshot()["abandoned_active"], 1)
            print("DNS_DEFAULT_WAIT seconds=%.3f abandoned_active=1" % elapsed)
        finally:
            release.set()
        wait_until(self, lambda: resolver.snapshot()["abandoned_active"] == 0)

    def test_cache_is_by_hostname_expires_at_five_minutes_and_does_not_cache_failure(self):
        clock = base.Clock(0)
        lookup = Mock(side_effect=addresses)
        resolver = network.Resolver(lookup=lookup, clock=clock)
        first = resolver.resolve("host.test", 8)
        clock.now = 299.99
        self.assertEqual(resolver.resolve("host.test", 308), first)
        self.assertEqual(lookup.call_count, 1)
        clock.now = 300
        resolver.resolve("host.test", 308)
        self.assertEqual(lookup.call_count, 2)
        resolver.resolve("other.test", 308)
        self.assertEqual(lookup.call_count, 3)
        lookup.side_effect = socket.gaierror("injected")
        with self.assertRaises(socket.gaierror):
            resolver.resolve("bad.test", 308)
        lookup.side_effect = addresses
        resolver.resolve("bad.test", 308)
        self.assertEqual(lookup.call_count, 5)

    def test_late_abandoned_results_are_not_cached_and_stop_is_latched(self):
        release = threading.Event()
        lookup = Mock(side_effect=lambda *a, **k: (release.wait(), addresses(*a, **k))[1])
        resolver = network.Resolver(lookup=lookup, wait_timeout=.01)
        try:
            for i in range(8):
                with self.assertRaises(TimeoutError):
                    resolver.resolve("blocked.test", time.monotonic()+1)
                state = resolver.snapshot()
                self.assertEqual(state["abandoned_active"], i+1)
                self.assertEqual(state["stop_reason"], "dns_abandoned_limit" if i == 7 else None)
            with self.assertRaisesRegex(OSError, "dns_abandoned_limit"):
                resolver.resolve("another.test", time.monotonic()+1)
            self.assertEqual(lookup.call_count, 8)
        finally:
            release.set()
        wait_until(self, lambda: resolver.snapshot()["abandoned_active"] == 0)
        self.assertEqual(resolver.snapshot()["abandoned_total"], 8)
        self.assertEqual(resolver.snapshot()["stop_reason"], "dns_abandoned_limit")
        self.assertEqual(resolver.snapshot()["cached_hosts"], 0)


class SocketTests(unittest.TestCase):
    def socket(self):
        sock = Mock()
        source = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        sock.recv_into.side_effect = source.readinto
        return sock

    def test_https_connects_resolved_ip_but_sni_certificate_and_host_use_hostname(self):
        resolver = network.Resolver(lookup=addresses)
        raw, tls = self.socket(), self.socket()
        context = ssl.create_default_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        with patch.object(ssl, "_create_default_https_context", return_value=context), \
                patch.object(context, "wrap_socket", return_value=tls) as wrap, \
                patch.object(network.socket, "socket", return_value=raw), \
                patch.object(network.socket, "getaddrinfo", side_effect=AssertionError("second DNS forbidden")):
            self.assertEqual(network.get_json("https://origin.test:8443/path", resolver=resolver), {})
        raw.connect.assert_called_once_with(("127.0.0.1", 8443))
        wrap.assert_called_once_with(raw, server_hostname="origin.test")
        self.assertIn(b"Host: origin.test:8443\r\n", tls.sendall.call_args.args[0])
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        tls.close.assert_called_once()

    def test_certificate_mismatch_fails_and_closes_socket(self):
        raw = self.socket()
        context = ssl.create_default_context()
        def reject(sock, server_hostname):
            self.assertEqual(server_hostname, "origin.test")
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            raise ssl.SSLCertVerificationError("hostname mismatch")
        with patch.object(ssl, "_create_default_https_context", return_value=context), \
                patch.object(context, "wrap_socket", side_effect=reject), \
                patch.object(network.socket, "socket", return_value=raw):
            with self.assertRaises(ssl.SSLCertVerificationError):
                network.get_json("https://origin.test/", resolver=network.Resolver(lookup=addresses))
        raw.close.assert_called_once()
        raw.sendall.assert_not_called()

    def test_dns_connect_tls_and_read_share_one_deadline(self):
        clock = base.Clock(0)
        def lookup(*args, **kwargs):
            clock.advance(1.5)
            return addresses(*args, **kwargs)
        raw, tls = self.socket(), self.socket()
        raw.connect.side_effect = lambda addr: clock.advance(2.5)
        context = ssl.create_default_context()
        def wrap(sock, server_hostname):
            self.assertEqual(raw.settimeout.call_args.args[0], 3)
            clock.advance(2.5)
            return tls
        def recv(buffer):
            self.assertEqual(tls.settimeout.call_args.args[0], 1.5)
            clock.advance(1.5)
            return 0
        tls.recv_into.side_effect = recv
        with patch.object(ssl, "_create_default_https_context", return_value=context), \
                patch.object(context, "wrap_socket", side_effect=wrap), \
                patch.object(network.socket, "socket", return_value=raw):
            with self.assertRaisesRegex(TimeoutError, "request_deadline"):
                network.get_json("https://origin.test/", clock=clock,
                                 resolver=network.Resolver(lookup=lookup, clock=clock))
        self.assertEqual(clock(), 8)
        tls.close.assert_called_once()

    def test_multiple_addresses_share_connection_budget_and_close_failed_sockets(self):
        clock = base.Clock(0)
        def lookup(*args, **kwargs):
            clock.advance(1.5)
            return addresses(*args, **kwargs) * 4
        sockets = [self.socket() for _ in range(3)]
        seen = []
        for sock in sockets:
            def fail(addr, sock=sock):
                timeout = sock.settimeout.call_args.args[0]
                seen.append(timeout)
                clock.advance(timeout)
                raise TimeoutError("connect_timeout")
            sock.connect.side_effect = fail
        with patch.object(network.socket, "socket", side_effect=sockets) as factory:
            with self.assertRaisesRegex(TimeoutError, "request_deadline"):
                network.get_json("http://origin.test/", clock=clock,
                                 resolver=network.Resolver(lookup=lookup, clock=clock))
        self.assertEqual(seen, [3, 3, .5])
        self.assertEqual(clock(), 8)
        self.assertEqual(factory.call_count, 3)
        for sock in sockets:
            sock.close.assert_called_once()

    def test_tls_handshake_uses_remaining_budget_after_address_retry(self):
        clock = base.Clock(0)
        def lookup(*args, **kwargs):
            clock.advance(1.5)
            return addresses(*args, **kwargs) * 2
        first, second = self.socket(), self.socket()
        def fail(addr):
            clock.advance(3)
            raise TimeoutError("first_address_timeout")
        first.connect.side_effect = fail
        second.connect.side_effect = lambda addr: clock.advance(3)
        context = ssl.create_default_context()
        def handshake(sock, server_hostname):
            self.assertIs(sock, second)
            self.assertEqual(server_hostname, "origin.test")
            self.assertEqual(sock.settimeout.call_args.args[0], .5)
            clock.advance(.5)
            raise TimeoutError("tls_timeout")
        with patch.object(ssl, "_create_default_https_context", return_value=context), \
                patch.object(context, "wrap_socket", side_effect=handshake), \
                patch.object(network.socket, "socket", side_effect=[first, second]):
            with self.assertRaisesRegex(TimeoutError, "request_deadline"):
                network.get_json("https://origin.test/", clock=clock,
                                 resolver=network.Resolver(lookup=lookup, clock=clock))
        self.assertEqual(clock(), 8)
        first.close.assert_called_once()
        second.close.assert_called_once()
        second.sendall.assert_not_called()


class WatchdogTests(unittest.TestCase):
    def test_all_workers_busy_over_30_seconds_and_completion_resets_timer(self):
        clock = base.Clock(0)
        pool = DaemonPool(4, clock=clock)
        releases = [threading.Event() for _ in range(5)]
        try:
            futures = [pool.submit(releases[i].wait) for i in range(3)]
            wait_until(self, lambda: pool.snapshot()["busy"] == 3)
            clock.advance(100)
            self.assertFalse(pool.stalled())
            futures.append(pool.submit(releases[3].wait))
            wait_until(self, lambda: pool.snapshot()["busy"] == 4)
            clock.advance(30)
            self.assertFalse(pool.stalled())
            clock.advance(.001)
            self.assertTrue(pool.stalled())
            releases[0].set()
            futures[0].result(timeout=1)
            wait_until(self, lambda: pool.snapshot()["busy"] == 3)
            self.assertFalse(pool.stalled())
            pool.submit(releases[4].wait)
            wait_until(self, lambda: pool.snapshot()["busy"] == 4)
            clock.advance(30)
            self.assertFalse(pool.stalled())
            clock.advance(.001)
            self.assertTrue(pool.stalled())
        finally:
            for release in releases:
                release.set()
            pool.shutdown()

    def run_process(self, injection, reason):
        with tempfile.TemporaryDirectory() as temp:
            code = '''
import json, socket, sys, threading, time
from tools.fill_sim import __main__ as app, network
from tools.fill_sim.scheduler import DaemonPool
root = sys.argv[1]
app.guard = lambda *args: (None, {})
app.service_memory = lambda: (20, 'process_rss_fallback', None)
''' + injection + '''
sys.exit(app.main(['run', '--db', root+'/run.db', '--out', root+'/out']))
'''
            started = time.monotonic()
            proc = subprocess.Popen([sys.executable, "-B", "-c", code, temp],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                stdout, stderr = proc.communicate(timeout=12)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
            self.assertEqual(proc.returncode, 78, (stdout, stderr))
            self.assertLess(time.monotonic()-started, 10)
            state = json.loads(Path(temp, "out/status.json").read_text())
            self.assertEqual(state["stop_reason"], reason)
            import sqlite3
            with closing(sqlite3.connect(Path(temp, "run.db"))) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            print("RUNTIME_STOP reason=%s code=78 seconds=%.3f" % (reason, time.monotonic()-started))
            return state

    def test_eight_blocked_dns_threads_cause_actual_process_exit_78(self):
        state = self.run_process('''
socket.getaddrinfo = lambda *a, **k: threading.Event().wait(60)
''', "dns_abandoned_limit")
        self.assertGreaterEqual(state["dns"]["abandoned_active"], 8)
        self.assertGreaterEqual(state["dns"]["abandoned_total"], 8)

    def test_worker_watchdog_causes_actual_process_exit_78(self):
        state = self.run_process('''
clock = [0.0]
original_engine = app.Engine
def engine(*args, **kwargs):
    pool = DaemonPool(4, clock=lambda: clock[0])
    result = original_engine(*args, executor=pool, **kwargs)
    def advance():
        while pool.snapshot()['busy'] != 4:
            time.sleep(.005)
        clock[0] = 30.001
    threading.Thread(target=advance, daemon=True).start()
    return result
app.Engine = engine
network.PublicClient._perform = lambda *a, **k: threading.Event().wait(60)
network.ShadowAPI.poll = lambda *a, **k: []
''', "public_workers_stalled")
        self.assertEqual(state["workers"]["public"]["busy"], 4)
        self.assertGreater(state["workers"]["public"]["all_busy_seconds"], 30)


class ExpiryTests(base.OfflineCase):
    setUp = base.EngineTests.setUp
    open_three = base.EngineTests.open_three
    four_groups = round2.TrackingTests.four_groups
    advance_run = round2.TrackingTests.advance_run

    def test_low_send_rate_does_not_exempt_48h_and_queued_jobs_are_cancelled(self):
        start, transport = self.four_groups()
        self.engine.client.limiter = network.Limiter(.01, 8, clock=self.clock)
        for p in self.engine.positions.values():
            p["opened_at"] = start - 172800 + 65
        self.advance_run(start, 64.95, transport)
        self.assertEqual(len(self.engine.positions), 4)
        self.assertTrue(all(p["tracking_status"] == "rate_limited_delay" for p in self.engine.positions.values()))
        pending = [f for job in self.engine.jobs.values() for f in job["futures"] if not f.done()]
        self.assertTrue(pending)
        self.clock.now = start + 65
        self.engine.tick()
        self.assertFalse(self.engine.positions)
        self.assertFalse(self.engine.jobs)
        self.assertTrue(all(f.cancelled() for f in pending))
        closed = [p for p in self.store.rows("positions") if p["id"].startswith("p")]
        self.assertEqual(len(closed), 4)
        for p in closed:
            self.assertEqual(p["closed_at"], start+65)
            self.assertEqual(p["holding_seconds"], 172800)
            self.assertEqual(p["exit_reason"], "timeout_48h")
            self.assertEqual(p["exit_pricing"], "priced")
            self.assertIsNotNone(p["net_usd"])
        self.assertEqual(len(transport.sent), 2)
        self.clock.advance(100)
        transport.complete()
        self.engine.tick()
        self.assertFalse(self.engine.positions)
        self.assertEqual(len(transport.sent), 2)

    def test_48h_uses_last_executable_or_unpriced_without_waiting_for_inflight(self):
        self.open_three()
        seed = copy.deepcopy(next(iter(self.engine.positions.values())))
        self.engine.positions.clear()
        self.engine.pending.clear()
        self.engine.cache.clear()
        self.engine.want_specs.clear()
        from tools.fill_sim.scheduler import RequestFuture
        cases = []
        for i in range(3):
            p = copy.deepcopy(seed)
            p.update(id="expiry%d" % i, event_id="expiry%d" % i,
                     opened_at=self.clock()-172800, next_track=self.clock()+1000)
            if i == 1:
                p["last_executable_exit"] = copy.deepcopy(p["last_books"])
                p["last_books"] = [base.book(qty=0).to_dict()]*2
            if i == 2:
                p["last_books"] = [None, None]
                p.pop("last_executable_exit", None)
            self.engine.positions[p["id"]] = p
            cases.append(p)
        futures = [RequestFuture(self.clock()) for _ in range(2)]
        for future in futures:
            future.set_running_or_notify_cancel()
            future.sent_at = self.clock()
        self.engine.jobs[cases[0]["event_id"]] = dict(kind="track", futures=futures, accounted=[0, 0], observed=[False, False])
        self.engine.tick()
        self.assertFalse(self.engine.positions)
        self.assertFalse(self.engine.jobs)
        for p in cases[:2]:
            self.assertEqual(p["exit_reason"], "timeout_48h")
            self.assertEqual(p["exit_pricing"], "priced")
        self.assertEqual(cases[2]["exit_reason"], "unpriced_exit")
        self.assertEqual(cases[2]["forced_exit_reason"], "timeout_48h")
        self.assertFalse(cases[2]["main_eligible"])
        self.assertIsNone(cases[2]["net_usd"])
        self.assertIsNone(cases[2]["net_bps"])
        for future in futures:
            future.set_result(base.book())
        self.engine.tick()
        self.assertFalse(self.engine.positions)
