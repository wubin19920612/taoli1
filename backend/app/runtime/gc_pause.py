"""Bounded GC accounting; all JSON formatting and logging happens after callbacks."""

import asyncio
import gc
import json
import logging
import math
import os
import time
from collections import deque
from collections.abc import Callable

logger = logging.getLogger("gc_pause")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False


class GcPauseMonitor:
    def __init__(self, *, clock: Callable[[], float] = time.perf_counter) -> None:
        self.enabled = os.getenv("GC_PAUSE_LOG_ENABLED", "true").strip().lower() not in {
            "0", "false", "off", "no",
        }
        try:
            threshold = float(os.getenv("GC_PAUSE_LOG_THRESHOLD_MS", "200"))
        except (ValueError, TypeError):
            threshold = 200.0
        self.threshold_ms = threshold if math.isfinite(threshold) and threshold > 0 else 200.0
        self.clock = clock
        self._started: list[float | None] = [None, None, None]
        # Publish both counters together for the watchdog's observer thread.
        self._totals = (0.0, 0)
        self._pending: deque[tuple[int, float, int]] = deque(maxlen=5)
        self._emitted: deque[float] = deque()

    def start(self) -> None:
        if self.callback not in gc.callbacks:
            gc.callbacks.append(self.callback)

    def stop(self) -> None:
        if self.callback in gc.callbacks:
            gc.callbacks.remove(self.callback)

    def snapshot(self) -> tuple[float, int]:
        return self._totals

    def callback(self, phase: str, info: dict) -> None:
        try:
            generation = info["generation"]
            if generation not in (0, 1, 2):
                return
            if phase == "start":
                self._started[generation] = self.clock()
            elif phase == "stop":
                started = self._started[generation]
                self._started[generation] = None
                if started is None:
                    return
                milliseconds = (self.clock() - started) * 1000
                total_ms, gen2 = self._totals
                self._totals = (total_ms + milliseconds, gen2 + int(generation == 2))
                if self.enabled and milliseconds >= self.threshold_ms:
                    # Five small scalar records at most; never retain GC objects/frames.
                    self._pending.append((generation, milliseconds, int(info.get("collected", 0))))
        except Exception:  # noqa: BLE001, S110 - GC diagnostics cannot affect collection.
            pass

    def flush(self) -> None:
        while self._pending:
            generation, milliseconds, collected = self._pending.popleft()
            try:
                now = self.clock()
                while self._emitted and self._emitted[0] <= now - 60:
                    self._emitted.popleft()
                if len(self._emitted) >= 5:
                    continue
                # Reserve before writing, including partially failed writes.
                self._emitted.append(now)
                logger.info(json.dumps({
                    "gc_pause": 1, "gen": generation, "ms": round(milliseconds, 3),
                    "collected": collected,
                }, separators=(",", ":"), allow_nan=False))
            except Exception:  # noqa: BLE001, S110 - broken logging must stay isolated.
                pass

    async def run(self) -> None:
        self.start()
        try:
            while True:
                await asyncio.sleep(0.1)
                self.flush()
        finally:
            self.stop()
