"""Bounded GET transport, token buckets and independent API backoff."""
from collections import Counter, deque
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from .core import EXCHANGES
from .exchanges import check_response
from .http_worker import NoRedirect


class RateLimited(Exception):
    pass


def get_json(url, headers=None, timeout=3):
    # A killable stdlib worker bounds DNS, TLS, headers AND a trickling body.
    # Credentials travel through stdin, never command arguments or diagnostics.
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("http_worker.py"))],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    try:
        raw, _ = proc.communicate(json.dumps(dict(url=url, headers=headers, timeout=timeout)).encode(),
                                  timeout=max(.001, deadline-time.monotonic()))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise TimeoutError("request_deadline") from None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    kind, _, body = raw.partition(b"\n")
    if kind == b"OK":
        result = json.loads(body)
        if time.monotonic() >= deadline:
            raise TimeoutError("request_deadline")
        return result
    if kind == b"HTTP":
        error = json.loads(body)
        raise HTTPError(url, error["code"], "http_error", error["headers"], None)
    raise ValueError("http_transport_error")


class Limiter:
    """Token buckets plus rolling one-second guard: no burst can exceed 2/8.

    Admission is nonblocking. Callers dispatch due positions before new events.
    Specs, funding and depth share the same budget. Counters survive restart.
    """
    def __init__(self, per_exchange=2, global_rate=8, clock=time.time, state=None):
        if not 0 < per_exchange <= 2 or not 0 < global_rate <= 8:
            raise ValueError("rate_ceiling")
        self.clock, self.lock = clock, threading.Lock()
        self.rates = {"global": global_rate, **{ex: per_exchange for ex in EXCHANGES}}
        now = clock()
        self.buckets = {k: [max(1.0, float(v)), now] for k, v in self.rates.items()}
        self.history = {k: deque() for k in self.rates}
        self.sent = {k: deque() for k in self.rates}
        self.sending = Counter()
        self.stats = {ex: dict(requests=0, rate_limits=0, errors=0, paused_until=0, strikes=0) for ex in EXCHANGES}
        if state:
            for ex in EXCHANGES:
                self.stats[ex].update(state.get(ex, {}))
            # Conservative restart: avoid a fresh burst in the previous process's second.
            self.buckets = {k: [0.0, now] for k in self.rates}

    def acquire(self, exchanges):
        counts = Counter(exchanges)
        counts["global"] = len(exchanges)
        with self.lock:
            now = self.clock()
            for ex in exchanges:
                if self.stats[ex]["paused_until"] > now:
                    return False
            for key, count in counts.items():
                tokens, last = self.buckets[key]
                tokens = min(max(1, self.rates[key]), tokens + max(0, now - last) * self.rates[key])
                self.buckets[key] = [tokens, now]
                history = self.history[key]
                while history and history[0] <= now - 1:
                    history.popleft()
                if tokens < count or len(history) + count > max(1, self.rates[key]):
                    return False
            for key, count in counts.items():
                self.buckets[key][0] -= count
                self.history[key].extend([now] * count)
            return True

    def admit_send(self, ex):
        """Reservations schedule work; only this gate authorizes a send now."""
        with self.lock:
            now = self.clock()
            if self.stats[ex]["paused_until"] > now:
                return False
            for key in (ex, "global"):
                history = self.sent[key]
                while history and history[0] <= now - 1:
                    history.popleft()
                if len(history) + self.sending[key] >= max(1, self.rates[key]):
                    return False
            for key in (ex, "global"):
                self.sending[key] += 1
            self.stats[ex]["requests"] += 1
            return True

    def finish_send(self, ex):
        # Keep admission occupied throughout transport (including process startup,
        # DNS and scheduling delays), then retain it for a full rolling second.
        # Completion is >= actual send time, so this conservative gate cannot
        # release a delayed send's quota before its real one-second window ends.
        with self.lock:
            for key in (ex, "global"):
                self.sending[key] -= 1
                self.sent[key].append(self.clock())

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
        if not reserved and not self.limiter.acquire([ex]):
            raise RateLimited("skipped_rate_limit")
        if not self.limiter.admit_send(ex):
            raise RateLimited("skipped_rate_limit")
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
        except OSError:
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
        except (OSError, ValueError):
            self.failures += 1
            self.streak += 1
            self.next_poll = self.clock() + min(30, 2 ** min(self.streak, 5))
            return None
        self.streak = 0
        self.next_poll = self.clock() + 1
        return data
