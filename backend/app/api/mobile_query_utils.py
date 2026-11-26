import asyncio
import logging
import re
from asyncio import wait
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from itertools import groupby
from time import monotonic
from typing import Any, TypeVar

from fastapi import HTTPException, Request

QUERY_TIMEOUT_SECONDS = 60
CLOSE_TIMEOUT_SECONDS = 10
MIN_START_INTERVAL_SECONDS = 3
MAX_UPSTREAM_TASKS = 32
MAX_POINTS = 800
_Service = TypeVar("_Service")
_Result = TypeVar("_Result")

_SENSITIVE_UPSTREAM_TEXT = re.compile(
    r"""(?ix)
    -----BEGIN\s+[^\r\n-]*PRIVATE\s+KEY-----[\s\S]*
    |(?:[a-z][a-z0-9+.-]*://|(?<!\w)/[^/\s]|\?)[^\s'"<>]*
    |\b(?:bearer|basic)\s+[^\s,;'"<>]+
    |\b(?:[\w-]*(?:key|token|secret|signature|password|passphrase|cookie|authorization|webhook)|sig)
        ['"]?\s*[:=][\s\S]*
    """
)


def redact_upstream(message: str) -> str:
    return _SENSITIVE_UPSTREAM_TEXT.sub("[已隐藏]", message)[:1000]


async def stop_cleanup_tasks(
    tasks: Iterable[asyncio.Task], *, timeout: float, logger: logging.Logger, label: str,
) -> None:
    tasks = set(tasks)
    if tasks:
        for task in tasks:
            task.cancel()
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning("应用关闭时仍有 %s 个手机%s清理任务未完成", len(pending), label)


async def _wait_for_disconnect(request: Request) -> None:
    # The GET routes consume no request body.
    while (await request.receive())["type"] != "http.disconnect":
        pass


class MobileQueryLimits:
    def __init__(
        self,
        cleanup_tasks: set[asyncio.Task],
        *,
        logger: logging.Logger,
        label: str,
        log_upstream: Callable[[str], None],
        clock: Callable[[], float] | None = None,
        min_start_interval_seconds: int = MIN_START_INTERVAL_SECONDS,
    ):
        self._active_queries: set[str] = set()
        self._starts: dict[tuple[str, str], float] = {}
        self._cleanup_at = 0.0
        self._upstream_tasks: set[asyncio.Task] = set()
        self._cleanup_tasks = cleanup_tasks
        self._logger = logger
        self._label = label
        self._log_upstream = log_upstream
        self._clock = clock or monotonic
        self._min_start_interval_seconds = min_start_interval_seconds

    def start(self, session: str, operation: str) -> float:
        now = self._clock()
        interval = self._min_start_interval_seconds
        cutoff = now - interval
        if now >= self._cleanup_at:
            self._starts = {key: start for key, start in self._starts.items() if start > cutoff}
            self._cleanup_at = now + interval
        key = (session, operation)
        if self._starts.get(key, cutoff) > cutoff:
            raise HTTPException(429, f"请求过于频繁，请至少间隔 {interval} 秒")
        self._starts[key] = now
        return now

    @contextmanager
    def query(self, session: str) -> Iterator[float]:
        if session in self._active_queries:
            raise HTTPException(429, "上一个查询还在进行")
        started = self.start(session, "query")
        self._active_queries.add(session)
        try:
            yield started
        finally:
            self._active_queries.remove(session)

    async def _cleanup_upstream(self, service: object, task: asyncio.Task, timeout: float) -> None:
        try:
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)
        finally:
            close = getattr(service, "aclose", None)
            if close is not None:
                try:
                    async with asyncio.timeout(timeout):
                        await close()
                except Exception as exc:  # noqa: BLE001 - keep cleanup failures out of the response.
                    self._log_upstream(f"清理交易所连接失败: {type(exc).__name__}: {exc}")

    def _start_cleanup(self, service: object, task: asyncio.Task, timeout: float) -> None:
        self._upstream_tasks.remove(task)
        cleanup = asyncio.create_task(self._cleanup_upstream(service, task, timeout))
        self._upstream_tasks.add(cleanup)
        self._cleanup_tasks.add(cleanup)

        def expire() -> None:
            if not cleanup.done():
                self._upstream_tasks.discard(cleanup)
                self._logger.warning("手机%s清理超过 %s 秒，释放查询名额", self._label, timeout)

        # Keep slow cleanups registered for shutdown after their admission deadline.
        deadline = asyncio.get_running_loop().call_later(timeout, expire)

        def finished(done: asyncio.Task) -> None:
            deadline.cancel()
            self._upstream_tasks.discard(done)
            self._cleanup_tasks.discard(done)

        cleanup.add_done_callback(finished)

    async def run(
        self,
        request: Request,
        name: str,
        started: float,
        factory: Callable[[], _Service],
        operation: Callable[[_Service], Coroutine[Any, Any, _Result]],
        *,
        wait_for: Callable[..., Awaitable[tuple[set[asyncio.Task], set[asyncio.Task]]]] | None = None,
        query_timeout: float = QUERY_TIMEOUT_SECONDS,
        close_timeout: float = CLOSE_TIMEOUT_SECONDS,
        max_upstream_tasks: int = MAX_UPSTREAM_TASKS,
    ) -> _Result:
        # Count running operations and cleanups within their admission deadline.
        if len(self._upstream_tasks) >= max_upstream_tasks:
            raise HTTPException(503, "交易所查询繁忙，请稍后重试")
        service = factory()
        task = asyncio.create_task(operation(service))
        self._upstream_tasks.add(task)

        def retrieve_error(done: asyncio.Task) -> None:
            # The request may have already returned a 504 or disconnected.
            if not done.cancelled():
                done.exception()

        task.add_done_callback(retrieve_error)
        key = (request.state.auth_session_hash, name)
        disconnected = asyncio.create_task(_wait_for_disconnect(request))
        try:
            done, _ = await (wait_for or wait)(
                {task, disconnected}, timeout=query_timeout, return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnected in done:
                disconnected.result()
                if self._starts.get(key) == started:
                    self._starts.pop(key)
                raise HTTPException(499, "客户端已断开连接")
            if task not in done:
                raise HTTPException(504, "查询超时，可缩短时长或加大周期")
            return task.result()
        except asyncio.CancelledError:
            if self._starts.get(key) == started:
                self._starts.pop(key)
            raise
        finally:
            if not task.done():
                task.cancel()
            self._start_cleanup(service, task, close_timeout)
            if not disconnected.done():
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)


def downsample[Point](
    points: list[Point], *, timestamp: Callable[[Point], datetime], value: Callable[[Point], float],
) -> list[Point]:
    if not points:
        return []

    def magnitude(point: Point) -> float:
        return abs(value(point))

    points = sorted(points, key=timestamp)
    if len(points) <= MAX_POINTS:
        selected = {at: max(group, key=magnitude) for at, group in groupby(points, key=timestamp)}
    else:
        first, last = points[0], points[-1]
        start = timestamp(first)
        span = max((timestamp(last) - start).total_seconds(), 1e-6)
        # Reserve slots for both endpoints and both global extrema.
        bucket_count = (MAX_POINTS - 4) // 2

        def bucket(point: Point) -> int:
            elapsed = (timestamp(point) - start).total_seconds()
            return min(bucket_count - 1, int(elapsed / span * bucket_count))

        selected = {start: first, timestamp(last): last}
        for _, group in groupby(points, key=bucket):
            members = list(group)
            extrema = (min(members, key=value), max(members, key=value))
            for point in sorted(extrema, key=magnitude):
                selected[timestamp(point)] = point

    # Prefer distinct timestamps for tied extrema. If both can only be represented
    # at one timestamp, keep the larger absolute value.
    low = min(points, key=value)
    high = max(points, key=lambda point: (value(point), timestamp(point) != timestamp(low)))
    if timestamp(low) == timestamp(high):
        low = min(points, key=lambda point: (value(point), timestamp(point) == timestamp(high)))
    for point in sorted((low, high), key=magnitude):
        selected[timestamp(point)] = point
    return [selected[at] for at in sorted(selected)]
