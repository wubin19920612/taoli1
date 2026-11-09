"""In-process bounded HTTP and a single public send admission gate."""
from collections import Counter, deque
import http.client
import io
import json
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


def get_json(url, headers=None, timeout=3, total_timeout=8, clock=time.monotonic):
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid_http_url")
    deadline = clock() + total_timeout
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = cls(parsed.hostname, parsed.port, timeout=min(timeout, total_timeout))
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
