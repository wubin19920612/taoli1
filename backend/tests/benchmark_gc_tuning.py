"""Manual Windows benchmark: python tests/benchmark_gc_tuning.py (two 60s runs)."""

import asyncio
import ctypes
import gc
import json
import os
import statistics
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from pydantic import BaseModel

from app.runtime.gc_tuning import start_gc_tuning
from app.runtime.supervisor import TaskSupervisor


class Snapshot(BaseModel):
    symbol: str
    exchange: str
    bid: float
    ask: float
    volume: float
    funding_rate: float
    timestamp: float


def batch():
    return [Snapshot(symbol=f"ASSET{i}", exchange="simulation", bid=100.0,
                     ask=100.1, volume=1_000_000, funding_rate=0.0001,
                     timestamp=time.time()) for i in range(15_000)]


def peak_rss_mib():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize / 1024**2


async def measure(mode, interval):
    original = gc.get_threshold()
    supervisor = TaskSupervisor()
    release = lambda: None
    durations = []
    started_gc = 0.0

    def callback(phase, info):
        nonlocal started_gc
        if info["generation"] == 2:
            if phase == "start":
                started_gc = time.perf_counter()
            else:
                durations.append((time.perf_counter() - started_gc) * 1000)

    try:
        gc.set_threshold(700, 10, 10)
        # Individually tracked, long-lived containers, not 3m references to one object.
        resident = [[None] for _ in range(3_000_000)]
        current = batch()
        setup_ms = None
        if mode == "tuned":
            os.environ.update(GC_TUNING_ENABLED="true", GC_THRESHOLDS="10000,20,10",
                              GC_FREEZE_AFTER_STARTUP="true", GC_FREEZE_DELAY_SECONDS="0")
            before = time.perf_counter()
            release = start_gc_tuning(supervisor)
            await supervisor.tasks["gc-freeze"]
            setup_ms = (time.perf_counter() - before) * 1000
        else:
            gc.collect()  # Equal warm-up boundary; excluded from the measured window.
        frozen = gc.get_freeze_count()
        stats = [row["collections"] for row in gc.get_stats()]
        gc.callbacks.append(callback)
        start = time.perf_counter()
        rounds = 0
        while time.perf_counter() - start < 60:
            current = batch()  # Old batch stays live until its replacement is complete.
            rounds += 1
            await asyncio.sleep(max(0, min(start + rounds * interval, start + 60)
                                    - time.perf_counter()))
        elapsed = time.perf_counter() - start
        gc.callbacks.remove(callback)
        result = {
            "mode": mode, "seconds": round(elapsed, 3), "resident_objects": len(resident),
            "batch_objects": len(current), "rounds": rounds, "interval_seconds": interval,
            "gc_collections": [row["collections"] - old
                               for row, old in zip(gc.get_stats(), stats, strict=True)],
            "gen2_count": len(durations),
            "gen2_max_ms": round(max(durations), 3) if durations else None,
            "gen2_median_ms": round(statistics.median(durations), 3) if durations else None,
            "peak_rss_mib": round(peak_rss_mib(), 3), "gc_frozen": frozen,
            "freeze_setup_ms": round(setup_ms, 3) if setup_ms is not None else None,
        }
    finally:
        if callback in gc.callbacks:
            gc.callbacks.remove(callback)
        release()
        await supervisor.stop()
        gc.set_threshold(*original)
    assert gc.get_threshold() == original
    # This isolated process owns all newly frozen objects; immortal baseline may
    # reappear on later collections but no resident workload should remain frozen.
    assert gc.get_freeze_count() < 1000
    result["restored_thresholds"] = gc.get_threshold()
    result["restored_frozen"] = gc.get_freeze_count()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        asyncio.run(measure(sys.argv[1], float(sys.argv[2])))
    else:
        # Calibrate churn to the spec's observed 55 gen0 collections/second.
        # Both fresh child processes then use exactly the same batch cadence.
        original = gc.get_threshold()
        try:
            gc.set_threshold(700, 10, 10)
            current = batch()
            before = gc.get_stats()[0]["collections"]
            for _ in range(10):
                current = batch()
            interval = (gc.get_stats()[0]["collections"] - before) / 10 / 55
        finally:
            gc.set_threshold(*original)
        for mode in ("default", "tuned"):
            subprocess.run([sys.executable, str(Path(__file__).resolve()), mode, str(interval)],
                           check=True, timeout=180)
