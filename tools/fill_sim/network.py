"""In-process bounded HTTP and a single public send admission gate."""
from collections import Counter, deque
import http.client
import io
import json
import socket
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler
from .core import EXCHANGES
from .exchanges import check_response


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RateLimited(Exception):
    pass


class Resolver:
    """Isolate uncancellable getaddrinfo; abandoned daemons never send HTTP.

    Only the waiting caller publishes a successful result to the hostname cache.
    A stop latch survives late completions so the main loop cannot miss the cap.
    """
    def __init__(self, lookup=None, clock=time.monotonic, wait_timeout=2):
        self.lookup, self.clock, self.wait_timeout = lookup, clock, wait_timeout
        self.lock = threading.Lock()
        self.cache = {}
        self.abandoned_active = self.abandoned_total = 0
        self.stop_reason = None

    def snapshot(self):
        with self.lock:
            return dict(abandoned_active=self.abandoned_active, abandoned_total=self.abandoned_total,
                        cached_hosts=len(self.cache), stop_reason=self.stop_reason)

    def resolve(self, hostname, deadline):
        with self.lock:
            if self.stop_reason:
                raise OSError(self.stop_reason)
            now = self.clock()
            if now >= deadline:
                raise TimeoutError("request_deadline")
            self.cache = {host: item for host, item in self.cache.items() if now < item[0]}
            if hostname in self.cache:
                return self.cache[hostname][1]
        done = threading.Event()
        job = dict(abandoned=False)

        def lookup():
            try:
                job["result"] = (self.lookup or socket.getaddrinfo)(hostname, None, type=socket.SOCK_STREAM)
                if not job["result"]:
                    raise OSError("dns_no_addresses")
            except Exception as exc:
                job["error"] = exc
            finally:
                with self.lock:
                    if job["abandoned"]:
                        self.abandoned_active -= 1
                    done.set()

        # Mark abandonment under the same lock used by completion.
        started = self.clock()
        wait_until = min(deadline, started+self.wait_timeout)
        thread = threading.Thread(target=lookup, name="fill-sim-dns", daemon=True)
        thread.start()
        completed = done.wait(max(0, wait_until-self.clock()))
        with self.lock:
            if not completed or self.clock() >= wait_until:
                if not done.is_set():
                    job["abandoned"] = True
                    self.abandoned_active += 1
                    self.abandoned_total += 1
                    if self.abandoned_active >= 8:
                        self.stop_reason = "dns_abandoned_limit"
                raise TimeoutError("request_deadline")
            if "error" in job:
                raise job["error"]
            self.cache[hostname] = (self.clock()+300, job["result"])
            return job["result"]


DNS_RESOLVER = Resolver()


def remaining_timeout(deadline, timeout, clock):
    remaining = deadline - clock()
    if remaining <= 0:
        raise TimeoutError("request_deadline")
    return min(timeout, remaining)


def connect_addresses(addresses, port, deadline, timeout, clock):
    """Connect numeric sockaddrs without a second DNS call; one shared budget."""
    error = OSError("dns_no_addresses")
    for family, kind, protocol, _, address in addresses:
        budget = remaining_timeout(deadline, timeout, clock)
        sock = None
        try:
            sock = socket.socket(family, kind, protocol)
            sock.settimeout(budget)
            sock.connect((address[0], port, *address[2:]))
            # HTTPSConnection wraps this socket next; bound the TLS handshake too.
            sock.settimeout(remaining_timeout(deadline, timeout, clock))
            return sock
        except OSError as exc:
            error = exc
            if sock is not None:
                sock.close()
    raise error


class DeadlineReader(io.RawIOBase):
    """Check every recv, including HTTP header lines and chunk framing."""
    def __init__(self, sock, deadline, timeout, clock):
        self.sock, self.deadline, self.timeout, self.clock = sock, deadline, timeout, clock

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError("request_deadline")
        self.sock.settimeout(min(self.timeout, remaining))
        count = self.sock.recv_into(buffer)
        if self.clock() >= self.deadline:
            raise TimeoutError("request_deadline")
        return count


class DeadlineSocket:
    def __init__(self, sock, deadline, timeout, clock):
        self.sock, self.deadline, self.timeout, self.clock = sock, deadline, timeout, clock

    def makefile(self, mode):
        return io.BufferedReader(DeadlineReader(self.sock, self.deadline, self.timeout, self.clock))


def get_json(url, headers=None, timeout=3, total_timeout=8, clock=time.monotonic, resolver=None):
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid_http_url")
    deadline = clock() + total_timeout
    addresses = (resolver or DNS_RESOLVER).resolve(parsed.hostname, deadline)
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    port = parsed.port if parsed.port is not None else cls.default_port
    conn = cls(parsed.hostname, port, timeout=remaining_timeout(deadline, timeout, clock))
    # Keep conn.host unchanged: Host, TLS SNI and certificate verification all use
    # the original hostname. Only the TCP socket factory consumes resolved IPs.
    conn._create_connection = lambda address, *args: connect_addresses(
        addresses, address[1], deadline, timeout, clock)
    response = None
    try:
        conn.connect()
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("request_deadline")
        conn.sock.settimeout(min(timeout, remaining))
        conn.request("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                     headers={"User-Agent": "taoli1-fill-sim/1", "Connection": "close", **(headers or {})})
        # Use HTTPResponse's parser with a deadline-aware file, including slow headers.
        response = http.client.HTTPResponse(DeadlineSocket(conn.sock, deadline, timeout, clock), method="GET")
        response.begin()
        if response.status != 200:
            raise HTTPError(url, response.status, "http_error", response.headers, None)
        body = bytearray()
        while True:
            chunk = response.read1(65536)
            if clock() >= deadline:
                raise TimeoutError("request_deadline")
            if not chunk:
                if response.length not in (None, 0):
                    raise http.client.IncompleteRead(bytes(body), response.length)
                break
            body.extend(chunk)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError("response_too_large")
        result = json.loads(body)
        if clock() >= deadline:
            raise TimeoutError("request_deadline")
        return result
    except TimeoutError:
        raise TimeoutError("request_deadline") from None
    finally:
        if response is not None:
            response.close()
        conn.close()


class Limiter:
    """One rolling send gate shared by depth, specifications and funding."""
    def __init__(self, per_exchange=2, global_rate=8, clock=time.time, state=None):
        if not 0 < per_exchange <= 2 or not 0 < global_rate <= 8:
            raise ValueError("rate_ceiling")
        self.clock, self.lock = clock, threading.RLock()
        self.rates = {"global": global_rate, **{ex: per_exchange for ex in EXCHANGES}}
        self.history = {k: deque() for k in self.rates}
        self.sending = Counter()
        self.stats = {ex: dict(requests=0, rate_limits=0, errors=0, timeouts=0,
                              paused_until=0, strikes=0) for ex in EXCHANGES}
        self.restart_until = clock() + 1 if state else 0
        if state:
            for ex in EXCHANGES:
                self.stats[ex].update(state.get(ex, {}))

    def available(self, ex):
        with self.lock:
            now = self.clock()
            if now < self.restart_until or self.stats[ex]["paused_until"] > now:
                return False
            for key in (ex, "global"):
                history = self.history[key]
                period = max(1, 1 / self.rates[key])
                while history and history[0] <= now - period:
                    history.popleft()
                if len(history) + self.sending[key] >= max(1, int(self.rates[key])):
                    return False
            return True

    def admit_send(self, ex):
        with self.lock:
            if not self.available(ex):
                return False
            for key in (ex, "global"):
                self.sending[key] += 1
            self.stats[ex]["requests"] += 1
            return True

    def finish_send(self, ex):
        # One gate, conservatively occupied through DNS/TLS/transport scheduling.
        # Retaining completion for a second also bounds any delayed wire send.
        with self.lock:
            for key in (ex, "global"):
                self.sending[key] -= 1
                self.history[key].append(self.clock())

    def throttled(self, ex, minimum=60):
        with self.lock:
            s = self.stats[ex]
            s["rate_limits"] += 1
            s["strikes"] += 1
            s["paused_until"] = self.clock() + max(minimum, min(3600, 60 * 2 ** min(s["strikes"] - 1, 6)))

    def snapshot(self):
        with self.lock:
            return {ex: {**s, "paused": s["paused_until"] > self.clock()} for ex, s in self.stats.items()}


class PublicClient:
    def __init__(self, limiter, transport=get_json):
        self.limiter, self.transport = limiter, transport

    def fetch(self, ex, url, reserved=False):
        # reserved is a compatibility argument only; it never bypasses admission.
        if not self.limiter.admit_send(ex):
            raise RateLimited("exchange_paused" if self.limiter.stats[ex]["paused_until"] > self.limiter.clock()
                              else "local_rate_limit")
        return self._perform(ex, url)

    def _perform(self, ex, url):
        try:
            data = self.transport(url)
            check_response(ex, data)
            return data
        except HTTPError as exc:
            if exc.code in (429, 418) or (ex == "bybit" and exc.code == 403):
                minimum = 600 if ex == "bybit" and exc.code == 403 else 60
                try:
                    minimum = max(minimum, float(exc.headers.get("Retry-After", 0)))
                except (ValueError, AttributeError):
                    pass
                self.limiter.throttled(ex, minimum)
                raise RateLimited("exchange_rate_limit") from None
            with self.limiter.lock:
                self.limiter.stats[ex]["errors"] += 1
            raise ValueError("http_%s" % exc.code) from None
        except ValueError as exc:
            if str(exc) == "rate_limited":
                self.limiter.throttled(ex)
                raise RateLimited("exchange_rate_limit") from None
            with self.limiter.lock:
                self.limiter.stats[ex]["errors"] += 1
            raise
        except TimeoutError:
            with self.limiter.lock:
                self.limiter.stats[ex]["errors"] += 1
                self.limiter.stats[ex]["timeouts"] += 1
            raise TimeoutError("request_deadline") from None
        except (OSError, http.client.HTTPException):
            with self.limiter.lock:
                self.limiter.stats[ex]["errors"] += 1
            raise ValueError("network_error") from None
        finally:
            self.limiter.finish_send(ex)


class ShadowAPI:
    def __init__(self, base, token="", transport=get_json, clock=time.time):
        self.url = base.rstrip("/") + "/api/shadow/trades?limit=50"
        self._token, self.transport, self.clock = token, transport, clock
        self.failures, self.streak, self.next_poll = 0, 0, 0

    def poll(self):
        if self.clock() < self.next_poll:
            return None
        try:
            data = self.transport(self.url, headers={"X-Api-Token": self._token}, timeout=3)
            if not isinstance(data, list) or len(data) > 50 or any(not isinstance(x, dict) for x in data):
                raise ValueError("invalid_api_payload")
        except (OSError, ValueError, http.client.HTTPException):
            self.failures += 1
            self.streak += 1
            self.next_poll = self.clock() + min(30, 2 ** min(self.streak, 5))
            return None
        self.streak = 0
        self.next_poll = self.clock() + 1
        return data
