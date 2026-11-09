"""Review 4: stale expiry precedence, address fallback and IPv6 URL ports."""
import copy
import errno
import io
import json
import socket
import ssl
from unittest.mock import Mock, call, patch

from tools.fill_sim import network
from tools.fill_sim.tests import test_fill_sim as base


class ExpiryTests(base.OfflineCase):
    setUp = base.EngineTests.setUp

    def position(self, failures):
        now = self.clock()
        books = [base.book(99, 100, now-61), base.book(102, 103, now-61)]
        p = dict(id="expiry", event_id="expiry", opened_at=now-172800,
                 closed_at=None, next_track=now+1000,
                 fill=base.opening(*books, base.spec(), base.spec(), 300, 0, 0),
                 last_books=[b.to_dict() for b in books], last_good=[now-61]*2,
                 failed_since=[None if age is None else now-age for age in failures],
                 funding=[{"spot": True}]*2)
        self.engine.positions[p["id"]] = p
        return p

    def expire(self, p):
        with patch.object(self.store, "position", wraps=self.store.position) as write:
            self.engine.tick()
            self.engine.tick()
        self.assertFalse(self.engine.positions)
        write.assert_called_once_with(p)
        stored = list(self.store.rows("positions"))
        self.assertEqual(stored, json.loads(json.dumps([p])))
        self.assertEqual(p["holding_seconds"], 172800)

    def test_48h_and_real_outage_on_either_leg_exits_stale_outside_main_pnl(self):
        for failures in ((60, None), (None, 60), (61, 61)):
            with self.subTest(failures=failures):
                p = self.position(failures)
                self.expire(p)
                self.assertEqual(p["exit_reason"], "stale_exit")
                self.assertIs(p["main_eligible"], False)
                self.assertEqual(p["exit_pricing"], "priced")
                self.assertIsNotNone(p["net_usd"])
                self.assertEqual(p["close_tA"], self.clock()-61)
                self.assertEqual(p["close_tB"], self.clock()-61)

    def test_48h_without_60s_real_failure_keeps_timeout_and_main_pnl(self):
        for failures in ((None, None), (59.999, None), (None, 59.999)):
            with self.subTest(failures=failures):
                p = self.position(failures)
                # Old quotes from local waiting alone must not create a real outage.
                if failures == (None, None):
                    p["tracking_status"] = "rate_limited_delay"
                self.expire(p)
                self.assertEqual(p["exit_reason"], "timeout_48h")
                self.assertIs(p["main_eligible"], True)
                self.assertIsNotNone(p["net_usd"])

    def test_stale_48h_preserves_depth_fallback_and_unpriced_exclusion(self):
        for fallback in (True, False):
            with self.subTest(fallback=fallback):
                p = self.position((60, None))
                if fallback:
                    p["last_executable_exit"] = copy.deepcopy(p["last_books"])
                p["last_books"] = [base.book(qty=0).to_dict()]*2
                self.expire(p)
                self.assertIs(p["main_eligible"], False)
                if fallback:
                    self.assertEqual(p["exit_reason"], "stale_exit")
                    self.assertIs(p["exit_price_fallback"], True)
                    self.assertIsNotNone(p["net_usd"])
                else:
                    self.assertEqual(p["exit_reason"], "unpriced_exit")
                    self.assertEqual(p["forced_exit_reason"], "stale_exit")
                    self.assertIsNone(p["net_usd"])
                    self.assertIsNone(p["net_bps"])


class SocketTests(base.OfflineCase):
    addresses = [
        (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::1", 0, 7, 4)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 0)),
    ]

    def response_socket(self):
        sock = Mock()
        source = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        sock.recv_into.side_effect = source.readinto
        return sock

    def test_unavailable_ipv6_socket_falls_back_to_ipv4_and_completes_http(self):
        raw = self.response_socket()
        resolver = Mock()
        resolver.resolve.return_value = self.addresses
        with patch.object(network.socket, "socket", side_effect=[
                OSError(errno.EAFNOSUPPORT, "IPv6 unavailable"), raw]) as factory:
            self.assertEqual(network.get_json("http://origin.test/", resolver=resolver), {})
        self.assertEqual(factory.call_args_list, [call(*a[:3]) for a in self.addresses])
        raw.connect.assert_called_once_with(("127.0.0.1", 80))
        self.assertIn(b"Host: origin.test\r\n", raw.sendall.call_args.args[0])
        raw.close.assert_called_once()

    def test_all_socket_creations_fail_only_after_trying_every_address(self):
        errors = [OSError(errno.EAFNOSUPPORT, "IPv6 unavailable"),
                  OSError(errno.EMFILE, "IPv4 unavailable")]
        with patch.object(network.socket, "socket", side_effect=errors) as factory:
            with self.assertRaises(OSError) as caught:
                network.connect_addresses(self.addresses, 443, 8, 3, base.Clock(0))
        self.assertIs(caught.exception, errors[-1])
        self.assertEqual(factory.call_args_list, [call(*a[:3]) for a in self.addresses])

    def test_connect_failure_then_socket_creation_failure_closes_first_socket_once(self):
        raw = self.response_socket()
        raw.connect.side_effect = OSError(errno.ECONNREFUSED, "connect failed")
        error = OSError(errno.EMFILE, "next socket unavailable")
        with patch.object(network.socket, "socket", side_effect=[raw, error]) as factory:
            with self.assertRaises(OSError) as caught:
                network.connect_addresses(self.addresses, 443, 8, 3, base.Clock(0))
        self.assertIs(caught.exception, error)
        self.assertEqual(factory.call_count, 2)
        raw.connect.assert_called_once_with(("::1", 443, 7, 4))
        raw.close.assert_called_once()

    def test_ipv6_literal_default_and_explicit_ports_preserve_host_and_tls_identity(self):
        for scheme, port, suffix in (("http", 80, ""), ("https", 443, ""),
                                     ("http", 8080, ":8080"), ("https", 8443, ":8443")):
            with self.subTest(scheme=scheme, suffix=suffix):
                raw, tls = self.response_socket(), self.response_socket()
                resolver = Mock()
                resolver.resolve.return_value = self.addresses[:1]
                context = ssl.create_default_context()
                with patch.object(ssl, "_create_default_https_context", return_value=context), \
                        patch.object(context, "wrap_socket", return_value=tls) as wrap, \
                        patch.object(network.socket, "socket", return_value=raw):
                    self.assertEqual(network.get_json(scheme+"://[::1]"+suffix+"/",
                                                      resolver=resolver), {})
                self.assertEqual(resolver.resolve.call_args.args[0], "::1")
                raw.connect.assert_called_once_with(("::1", port, 7, 4))
                wire = tls if scheme == "https" else raw
                self.assertIn(("Host: [::1]"+suffix+"\r\n").encode(), wire.sendall.call_args.args[0])
                wire.close.assert_called_once()
                if scheme == "https":
                    wrap.assert_called_once_with(raw, server_hostname="::1")
                    self.assertTrue(context.check_hostname)
                    self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                else:
                    wrap.assert_not_called()
