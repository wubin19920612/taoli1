"""Bounded, best-effort stack diagnostics for a blocked asyncio loop."""

import asyncio
import json
import logging
import math
import os
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
_APP_PREFIX = os.path.normcase(str(APP_ROOT)) + os.sep

logger = logging.getLogger("loop_stall")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False


class LoopStallWatchdog:
    def __init__(
        self, *, threshold_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = os.getenv("LOOP_STALL_WATCHDOG_ENABLED", "true").strip().lower() not in {
            "0", "false", "off", "no",
        }
        try:
            threshold = float(threshold_seconds if threshold_seconds is not None else
                              os.getenv("LOOP_STALL_THRESHOLD_SECONDS", "1.0"))
        except (ValueError, TypeError):
            threshold = 1.0
        self.threshold_seconds = threshold if math.isfinite(threshold) and threshold > 0 else 1.0
        self.clock = clock
        self.loop_thread_id: int | None = None
        self.thread: threading.Thread | None = None
        self._heartbeat: float | None = None
        self._stop = threading.Event()
        self._stall_since: float | None = None
        self._next_sample = 0.0
        self._attempts = 0
        self._stacks: Counter[tuple[str, ...]] = Counter()
        self._emitted: deque[float] = deque()
        self._warned = False

    def _warn_once(self) -> None:
        if self._warned:
            return
        self._warned = True
        try:
            # No exception details, frame locals or source text can enter the log.
            logger.warning("loop_stall watchdog diagnostic failed; continuing")
        except Exception:  # noqa: BLE001, S110 - a broken warning handler must stay isolated.
            pass

    async def run(self) -> None:
        if not self.enabled or self._stop.is_set():
            return
        self.loop_thread_id = threading.get_ident()
        try:
            self._heartbeat = self.clock()
            self.thread = threading.Thread(
                target=self._run_thread, name="loop-stall-watchdog", daemon=True,
            )
            self.thread.start()
            while not self._stop.is_set():
                # One scalar shared with the observer; never hold a lock while sampling.
                self._heartbeat = self.clock()
                await asyncio.sleep(0.1)
        except Exception:  # noqa: BLE001 - diagnostics must not interrupt application work.
            self._warn_once()
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self.thread is not None:
                self.thread.join(timeout=1.0)
        except Exception:  # noqa: BLE001 - shutdown must survive diagnostic thread failures.
            self._warn_once()

    def _run_thread(self) -> None:
        delay = 0.1
        while True:
            try:
                if self._stop.wait(delay):
                    return
                self._check()
                delay = 0.1
                if self._stall_since is not None and self._attempts < 16:
                    delay = max(0.001, min(delay, self._next_sample - self.clock()))
            except Exception:  # noqa: BLE001 - keep observing after any sampling/logging failure.
                self._warn_once()
                delay = 0.1

    def _check(self) -> None:
        now = self.clock()
        heartbeat = self._heartbeat
        if heartbeat is None:
            return
        if self._stall_since is not None and heartbeat != self._stall_since:
            duration_ms = round((heartbeat - self._stall_since) * 1000)
            stacks = self._stacks
            # Reset before logging so a broken handler cannot wedge the state machine.
            self._stall_since = None
            self._stacks = Counter()
            self._emit(duration_ms, stacks, now)
        if now - heartbeat <= self.threshold_seconds:
            return
        if self._stall_since is None:
            self._stall_since = heartbeat
            self._attempts = 0
            self._next_sample = now
        if self._attempts < 16 and now >= self._next_sample:
            # Failed samples also consume the budget, bounding failures per stall.
            self._attempts += 1
            self._next_sample = now + 0.25
            stack = self._sample_stack()
            self._stacks[stack] += 1

    def _sample_stack(self) -> tuple[str, ...]:
        frame = sys._current_frames()[self.loop_thread_id]
        stack = []
        external = 0
        try:
            # Walk inside out so truncation retains the blocking function.
            while frame is not None and len(stack) < 20:
                filename = frame.f_code.co_filename
                in_app = os.path.normcase(os.path.abspath(filename)).startswith(_APP_PREFIX)
                if in_app or external < 3:
                    if not in_app:
                        external += 1
                    try:
                        relative = os.path.relpath(filename, APP_ROOT.parent).replace("\\", "/")
                    except ValueError:  # Different Windows drives have no relative path.
                        relative = os.path.basename(filename)
                    stack.append(f"{relative}:{frame.f_lineno} {frame.f_code.co_name}")
                frame = frame.f_back
        finally:
            # Never retain live frames (and their potentially large/local secret objects).
            del frame
        return tuple(reversed(stack))

    def _emit(self, duration_ms: int, stacks: Counter, now: float) -> None:
        while self._emitted and self._emitted[0] <= now - 600:
            self._emitted.popleft()
        if len(self._emitted) >= 30:
            return
        # Reserve before calling the handler: even a partially failed write counts.
        self._emitted.append(now)
        logger.info(json.dumps({
            "loop_stall": 1,
            "duration_ms": duration_ms,
            "samples": sum(stacks.values()),
            "top": [{"stack": stack, "count": count}
                    for stack, count in stacks.most_common(3)],
        }, separators=(",", ":"), allow_nan=False))
