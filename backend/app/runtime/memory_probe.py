"""Opt-in process measurements; never inspect market objects or cache keys."""

import asyncio
import gc
import json
import logging
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable

from app.runtime.supervisor import TaskStatus, TaskSupervisor, distribution
from app.services.pair_spread_query import realtime_cache_stats
from app.services.snapshot_store import SnapshotStore

logger = logging.getLogger("memory_probe")
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    # Uvicorn's default config only attaches handlers to its own loggers. Keep
    # each probe record as plain JSON on stderr when no application handler exists.
    logger.addHandler(logging.StreamHandler())


def _log(values: dict) -> None:
    # A numeric marker also makes plain Docker stream lines grep-able by logger name.
    logger.info(json.dumps({"memory_probe": 1, **values}, separators=(",", ":"), allow_nan=False))


def read_process_status() -> dict[str, float | int | None]:
    """Read Linux RSS in MiB and OS thread count, including SQLite threads."""
    result = {"rss_mb": None, "rss_anon_mb": None, "threads": None}
    if sys.platform != "linux":
        return result
    fields = {"VmRSS:": "rss_mb", "RssAnon:": "rss_anon_mb", "Threads:": "threads"}
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                parts = line.split()
                if not parts or parts[0] not in fields:
                    continue
                try:
                    value = int(parts[1])
                except (IndexError, ValueError):
                    continue
                field = fields[parts[0]]
                result[field] = value if field == "threads" else value / 1024
    except (OSError, UnicodeError):
        return {"rss_mb": None, "rss_anon_mb": None, "threads": None}
    return result


class TaskRSSDeltas:
    """Bounded correlation signals: whole-process changes between a task's beats.

    Concurrent workers, GC and native allocators all affect these deltas. They
    cannot attribute allocations or retained memory to an individual task.
    """

    def __init__(self, window: int = 120) -> None:
        self.window = max(1, window)
        self.samples: dict[str, deque[float]] = {}

    def record(self, name: str, status: TaskStatus) -> None:
        current = read_process_status()["rss_anon_mb"]
        previous = status.last_rss_anon_mb
        status.last_rss_anon_mb = current
        status.last_rss_delta_mb = (
            current - previous if current is not None and previous is not None else None
        )
        if status.last_rss_delta_mb is not None:
            if name not in self.samples:
                self.samples[name] = deque(maxlen=self.window)
            self.samples[name].append(status.last_rss_delta_mb)

    def summary(self, names) -> dict[str, dict]:
        result = {}
        for name in names:
            stats = distribution(self.samples.get(name, ()))
            result[name] = {key: stats[key] for key in ("samples", "max", "p50")}
        return result


class MemoryProbe:
    def __init__(
        self, store: SnapshotStore, supervisor: TaskSupervisor, *,
        interval_seconds: float = 10, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.supervisor = supervisor
        self.interval_seconds = interval_seconds
        self.clock = clock
        self._last_summary = clock()
        self._collections = [stat["collections"] for stat in gc.get_stats()]
        self._iterations = self._iteration_times()

    def _iteration_times(self) -> dict:
        # Keep only task names and timestamps, never task results or snapshots.
        return {name: status.last_iteration_at for name, status in self.supervisor.statuses.items()}

    def sample(self) -> None:
        collections = [stat["collections"] for stat in gc.get_stats()]
        iterations = self._iteration_times()
        _log({
            **read_process_status(),
            "gc_counts": gc.get_count(),
            "gc_collections": [max(0, new - old) for new, old in zip(
                collections, self._collections, strict=True,
            )],
            "snapshot_sizes": self.store.sizes(),
            "realtime_caches": realtime_cache_stats(),
            "active_tasks": sorted(
                name for name, stamp in iterations.items()
                if stamp is not None and stamp != self._iterations.get(name)
            ),
        })
        self._collections = collections
        self._iterations = iterations
        self._report_deltas()

    def _report_deltas(self) -> None:
        now = self.clock()
        if now - self._last_summary >= 60:
            _log({"task_rss_deltas": self.supervisor.task_rss_deltas()})
            self._last_summary = now

    async def run(self, stop_event: asyncio.Event) -> None:
        next_sample = self.clock()
        while not stop_event.is_set():
            sampling = self.clock() >= next_sample
            try:
                if sampling:
                    self.sample()
                else:
                    self._report_deltas()
            except Exception:  # noqa: BLE001 - isolate arbitrary measurement failures.
                # Exception messages/tracebacks may contain arbitrary user data.
                _log({"sample_errors": 1})
                self._last_summary = self.clock()
                ok = False
            else:
                ok = True
            if sampling:
                self.supervisor.beat("memory-sampler", ok=ok)
                next_sample = self.clock() + self.interval_seconds
            delay = max(0.001, min(next_sample, self._last_summary + 60) - self.clock())
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except TimeoutError:
                pass


class TracemallocWindow:
    """One temporary, single-frame tracing session, owned by this task only."""

    def __init__(
        self, seconds: float, *, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.seconds = seconds
        self.clock = clock
        self.sleep = sleep

    @staticmethod
    def _rows(stats, *, diff: bool = False) -> list[dict]:
        rows = []
        for stat in stats[:25]:
            frame = stat.traceback[0]
            # Basenames suffice for file:line evidence and avoid host path details.
            filename = frame.filename.replace("\\", "/").rsplit("/", 1)[-1]
            rows.append({
                "location": f"{filename}:{frame.lineno}",
                "size_kb": (stat.size_diff if diff else stat.size) / 1024,
                "count": stat.count_diff if diff else stat.count,
            })
        return rows

    async def run(self) -> None:
        if self.seconds <= 0:
            return
        import tracemalloc

        if tracemalloc.is_tracing():
            _log({"tracemalloc_skipped": 1})
            return
        started = False
        previous = current = None
        try:
            tracemalloc.start(1)
            started = True
            deadline = self.clock() + self.seconds
            next_sample = self.clock() + 60
            previous = tracemalloc.take_snapshot()
            while self.clock() < deadline:
                await self.sleep(max(0, min(next_sample, deadline) - self.clock()))
                if self.clock() >= next_sample:
                    current = tracemalloc.take_snapshot()
                    _log({
                        "tracemalloc_top": self._rows(current.statistics("lineno")),
                        "tracemalloc_diff": self._rows(current.compare_to(previous, "lineno"), diff=True),
                    })
                    previous, current = current, None
                    next_sample = self.clock() + 60
        except Exception:  # noqa: BLE001 - tracing must never fail a business worker.
            _log({"tracemalloc_errors": 1})
        finally:
            previous = current = None
            if started:
                try:
                    tracemalloc.stop()
                except Exception:  # noqa: BLE001 - log no exception text on cleanup either.
                    _log({"tracemalloc_errors": 1})
