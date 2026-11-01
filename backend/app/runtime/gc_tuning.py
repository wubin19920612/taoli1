"""Process-wide GC tuning, leased by application lifespans on the event loop."""

import asyncio
import gc
import json
import logging
import math
import os
import time
from collections.abc import Callable

from app.runtime.supervisor import TaskSupervisor

logger = logging.getLogger("gc_tuning")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False

_sleep = asyncio.sleep
_active: "_Session | None" = None
_freeze_done = False


def _enabled(name: str) -> bool:
    return os.getenv(name, "true").strip().lower() not in {"0", "false", "off", "no"}


def _thresholds() -> tuple[int, int, int]:
    try:
        parts = os.getenv("GC_THRESHOLDS", "10000,20,10").split(",")
        values = tuple(int(part.strip()) for part in parts)
        # CPython's set_threshold arguments are signed C ints.
        if len(values) != 3 or any(not 0 < value <= 2**31 - 1 for value in values):
            raise ValueError
        return values
    except ValueError:
        logger.warning("Invalid GC_THRESHOLDS; using CPython defaults (700,10,10)")
        return (700, 10, 10)


def _delay() -> float:
    try:
        value = float(os.getenv("GC_FREEZE_DELAY_SECONDS", "60"))
        if not math.isfinite(value) or not 0 <= value <= 600:
            raise ValueError
        return value
    except ValueError:
        logger.warning("Invalid GC_FREEZE_DELAY_SECONDS; using 60 seconds")
        return 60.0


class _Session:
    def __init__(self) -> None:
        self.original = gc.get_threshold()
        self.users = 0
        self.frozen = False
        self.task: asyncio.Task | None = None

    async def freeze_after(self, delay: float) -> None:
        global _freeze_done
        await _sleep(delay)
        started = time.perf_counter()
        gc.collect()
        collect_ms = (time.perf_counter() - started) * 1000
        gc.freeze()
        self.frozen = True
        _freeze_done = True
        logger.info(json.dumps({
            "gc_tuning": 1, "thresholds": gc.get_threshold(),
            "frozen": gc.get_freeze_count(), "collect_ms": round(collect_ms, 3),
        }, separators=(",", ":"), allow_nan=False))

    def restore(self) -> None:
        # No awaits: a cancelled delayed task cannot freeze after restoration.
        if self.task is not None:
            self.task.cancel()
        try:
            if self.frozen:
                gc.unfreeze()
                self.frozen = False
        finally:
            gc.set_threshold(*self.original)


def start_gc_tuning(supervisor: TaskSupervisor) -> Callable[[], None]:
    """Apply once per active process session; return an idempotent release.

    Repeated/overlapping lifespans share one freeze task. The last release
    restores GC. After one successful freeze, later lifespans only tune thresholds;
    they must not freeze the same process again, even after an intervening release.
    Call release before awaiting shutdown so cancellation cannot skip cleanup.
    """
    global _active
    if not _enabled("GC_TUNING_ENABLED"):
        return lambda: None
    if _active is None:
        session = _Session()
        try:
            gc.set_threshold(*_thresholds())
            if not _freeze_done and _enabled("GC_FREEZE_AFTER_STARTUP"):
                delay = _delay()
                session.task = supervisor.start(
                    "gc-freeze", lambda: session.freeze_after(delay),
                    restart=False, critical=False,
                )
        except BaseException:
            session.restore()
            raise
        _active = session
    session = _active
    session.users += 1
    released = False

    def release() -> None:
        nonlocal released
        global _active
        if released:
            return
        released = True
        session.users -= 1
        if session.users == 0:
            try:
                session.restore()
            finally:
                _active = None

    return release
