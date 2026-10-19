"""Shared pacing for public OKX historical candles, including all query clients."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from weakref import WeakKeyDictionary

import httpx


class OkxHistoryLimiter:
    def __init__(
        self,
        interval_seconds: float = 0.25,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        # Four requests/second leaves headroom below OKX's 20 requests/2s/IP.
        self.interval_seconds = interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._next_request_at = 0.0
        self._cooldown_until = 0.0

    async def acquire(self) -> None:
        while True:
            now = self._clock()
            delay = max(self._next_request_at, self._cooldown_until) - now
            if delay <= 0:
                # No await between reading and reserving: atomic within this loop.
                # Cancelled waiters never reserve a slot or retain a lock.
                self._next_request_at = now + self.interval_seconds
                return
            await self._sleep(delay)

    def defer(self, seconds: float) -> None:
        self._cooldown_until = max(self._cooldown_until, self._clock() + seconds)


# All service instances in the production event loop share one budget. The
# limiter does not retain the loop, so closed test/reload loops can be collected.
_limiters: WeakKeyDictionary[asyncio.AbstractEventLoop, OkxHistoryLimiter] = WeakKeyDictionary()


def get_okx_history_limiter() -> OkxHistoryLimiter:
    loop = asyncio.get_running_loop()
    if loop not in _limiters:
        _limiters[loop] = OkxHistoryLimiter()
    return _limiters[loop]


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            seconds = (when - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0.0
    return max(0.0, seconds) if math.isfinite(seconds) else 0.0


async def get_okx_history_json(client: httpx.AsyncClient, url: str) -> dict:
    # Bound even a very long Retry-After for the current caller; the shared
    # cooldown remains in force for later calls. Do not shorten the server's wait.
    try:
        async with asyncio.timeout(30):
            return await _request(client, url)
    except TimeoutError as exc:
        raise RuntimeError("OKX 历史行情请求超时（限流冷却或上游响应延迟）") from exc


async def _request(client: httpx.AsyncClient, url: str) -> dict:
    limiter = get_okx_history_limiter()
    for attempt in range(3):
        await limiter.acquire()
        try:
            response = await client.get(url)
            try:
                payload = response.json()
            except ValueError:
                payload = None
            code = str(payload.get("code", "")) if isinstance(payload, dict) else ""
            if response.status_code == 429 or code == "50011":
                limiter.defer(max(2.0 ** (attempt + 1),
                                  retry_after_seconds(response.headers.get("Retry-After"))))
                if attempt == 2:
                    raise RuntimeError("OKX 历史行情接口限流（HTTP 429 / 50011），稍后重试")
                continue
            response.raise_for_status()
            if not isinstance(payload, dict):
                raise TypeError("响应不是 JSON 对象")
            if code not in {"", "0"}:
                raise RuntimeError(f"OKX 历史行情请求失败（{code}）：{str(payload.get('msg', ''))[:200]}")
            if not isinstance(payload.get("data"), list):
                raise TypeError("响应缺少 K 线数组")
            return payload
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError,
                ValueError, TypeError) as exc:
            if attempt == 2:
                detail = (f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError)
                          else type(exc).__name__)
                raise RuntimeError(f"OKX 历史行情请求失败：{detail}") from exc
            await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError("OKX 历史行情请求失败")  # Defensive: every final attempt returns or raises.
