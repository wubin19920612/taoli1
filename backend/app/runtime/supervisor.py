"""Supervise coroutine factories without changing their business decisions."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)
_current_supervisor: ContextVar[TaskSupervisor | None] = ContextVar(
    "task_supervisor", default=None,
)


def safe_error(exc: BaseException) -> str:
    # Vendor exceptions can contain arbitrary credentials, URLs or response bodies.
    # An allowlist of credential names cannot sanitize those reliably. Retain the
    # exception type only; never expose the message, cause or traceback here.
    return f"{type(exc).__name__}: [message redacted]"[:500]


def beat(name: str, ok: bool) -> None:
    """Report a completed iteration; standalone callers need no supervisor."""
    supervisor = _current_supervisor.get()
    if supervisor is not None:
        supervisor.beat(name, ok)


def distribution(values: list[float]) -> dict[str, float | int | None]:
    """Nearest-rank percentiles, in seconds, including empty-window metadata."""
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50": ordered[math.ceil(len(ordered) * 0.50) - 1] if ordered else None,
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None,
        "max": ordered[-1] if ordered else None,
    }


class StageTimings:
    """Keep the last N rounds, including completed stages of failed rounds."""

    names = ("poll", "prepare", "build", "publish", "history", "index")

    def __init__(self, max_rounds: int = 120, clock: Callable[[], float] = time.monotonic):
        self.rounds: deque[dict[str, float]] = deque(maxlen=max(1, max_rounds))
        self.clock = clock

    @contextmanager
    def measure(self, sample: dict[str, float], stage: str) -> Iterator[None]:
        started = self.clock()
        try:
            yield
        finally:
            sample[stage] = max(0.0, self.clock() - started)

    def snapshot(self) -> dict:
        return {
            "unit": "seconds",
            "max_rounds": self.rounds.maxlen,
            "rounds": len(self.rounds),
            "stages": {
                name: distribution([sample[name] for sample in self.rounds if name in sample])
                for name in self.names
            },
        }


class EventLoopLag:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.samples: deque[tuple[float, float]] = deque(maxlen=300)

    def _prune(self, now: float) -> None:
        while self.samples and self.samples[0][0] <= now - 300:
            self.samples.popleft()

    async def run(self) -> None:
        while True:
            started = self.clock()
            await asyncio.sleep(1)
            now = self.clock()
            self.samples.append((now, max(0.0, now - started - 1)))
            self._prune(now)

    def snapshot(self) -> dict:
        self._prune(self.clock())
        return {"unit": "seconds", "window_seconds": 300,
                **distribution([lag for _, lag in self.samples])}


@dataclass
class TaskStatus:
    critical: bool
    state: Literal["running", "backoff", "failed", "stopped"] = "stopped"
    started_at: datetime | None = None
    last_iteration_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_error: str | None = None
    iterations: int = 0
    restarts: int = 0
    last_failure_at: datetime | None = None
    backoff_seconds: float = 0.0


class TaskSupervisor:
    def __init__(
        self, *, initial_backoff: float = 5.0, max_backoff: float = 300.0,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if initial_backoff <= 0 or max_backoff < initial_backoff:
            raise ValueError("backoff must be positive and bounded")
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.now = now_fn or (lambda: datetime.now(UTC))
        self.statuses: dict[str, TaskStatus] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.loop_lag = EventLoopLag()
        self._stopping = False

    def start(
        self, name: str, coro_factory: Callable[[], Coroutine[Any, Any, None]], *,
        restart: bool, critical: bool,
    ) -> asyncio.Task:
        if self._stopping or name in self.tasks:
            raise ValueError("supervisor stopped or duplicate task name")
        status = TaskStatus(critical=critical)
        self.statuses[name] = status
        task = asyncio.create_task(self._run(name, coro_factory, restart), name=name)
        self.tasks[name] = task
        return task

    async def _run(self, name: str, coro_factory, restart: bool) -> None:
        status = self.statuses[name]
        delay = self.initial_backoff
        token = _current_supervisor.set(self)
        try:
            while not self._stopping:
                status.state = "running"
                status.started_at = self.now()
                previous_ok = status.last_ok_at
                try:
                    await coro_factory()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - supervise arbitrary worker failures.
                    status.state = "failed"
                    status.last_error = safe_error(exc)
                    status.last_failure_at = self.now()
                    logger.error("background task %s failed: %s", name, status.last_error)
                    if not restart or self._stopping:
                        return
                    if status.last_ok_at != previous_ok:
                        delay = self.initial_backoff
                    status.backoff_seconds = delay
                    # Keep failure visible for the first second of the SAME backoff
                    # interval; it does not add time to the 5 -> 300 second policy.
                    failed_seconds = min(1.0, delay)
                    await asyncio.sleep(failed_seconds)
                    status.state = "backoff"
                    await asyncio.sleep(delay - failed_seconds)
                    status.restarts += 1
                    status.backoff_seconds = 0.0
                    delay = min(self.max_backoff, delay * 2)
                else:
                    status.state = "stopped"
                    return
        except asyncio.CancelledError:
            status.state = "stopped"
            status.backoff_seconds = 0.0
            raise
        finally:
            _current_supervisor.reset(token)

    def beat(self, name: str, ok: bool) -> None:
        status = self.statuses.get(name)
        if status is None:
            return
        status.last_iteration_at = self.now()
        status.iterations += 1
        if ok:
            status.last_ok_at = status.last_iteration_at

    def snapshot(self) -> dict[str, dict]:
        now = self.now()
        return {
            name: {
                **asdict(status),
                "iteration_age_seconds": (
                    max(0.0, (now - status.last_iteration_at).total_seconds())
                    if status.last_iteration_at is not None else None
                ),
            }
            for name, status in self.statuses.items()
        }

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self.tasks.items())
        for _, task in tasks:
            task.cancel()
        results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for (name, _), result in zip(tasks, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                logger.error("background task %s failed during shutdown: %s", name, safe_error(result))
            status = self.statuses.get(name)
            if status is not None and status.state != "failed":
                status.state = "stopped"
