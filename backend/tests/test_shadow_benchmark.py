"""Offline synthetic end-to-end consumer benchmark; opt in with -m benchmark."""

import ctypes
import gc
import json
import math
import statistics
import time
from datetime import timedelta

import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.services.shadow_runtime import ShadowRuntime
from app.services.snapshot_consumers import PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_directional_opportunity
from shadow_helpers import NOW, market


def working_set():
    # Windows RSS (WorkingSetSize), not Python-only allocations.
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    *[(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                       "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]]
    info = Counters()
    info.cb = ctypes.sizeof(info)
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(info), info.cb)
    return info.WorkingSetSize


@pytest.mark.benchmark
@pytest.mark.asyncio
@pytest.mark.parametrize("positions,op_count,change_stride,cpu_budget", [
    pytest.param(1500, 1000, 1, 20, id="stress"),
    pytest.param(500, 400, 3, 8, id="realistic"),
])
async def test_shadow_resource_budgets(tmp_path, positions, op_count, change_stride, cpu_budget):
    markets, opportunities = [], []
    for i in range(positions):
        legs = [market(ex, price, symbol=f"C{i}USDT", raw_symbol=f"C{i}USDT")
                for ex, price in (("binance", 100), ("bitget", 100.5))]
        markets.extend(legs)
        opportunities.append(build_directional_opportunity(*legs, mode="FF"))
    path = str(tmp_path / "benchmark.db")
    db = await connect_database(path)
    await initialize_schema(db)
    store = SnapshotStore()
    gc.collect()
    baseline = working_set()
    runtime = ShadowRuntime(Settings(shadow_enabled=True, shadow_max_open=3000,
                                    database_url="sqlite:///" + path), store)
    runtime.catalog.entries = {("BINANCE", "FUTURE", f"C{i}", "") for i in range(positions)}
    await runtime.initialize(db)
    loop_times, cpus, walls, recomputed, captures, publishes = [], [], [], [], [], []

    async def run(now, ops):
        store.mark_published(now)
        snapshot = PublishedSnapshot(store.generation, now, tuple(markets), tuple(ops))
        # Runtime reports synchronous event-loop occupancy, excluding await time.
        # Includes immutable capture, dispatch and generation-checked publication.
        await runtime.consume(snapshot)
        state = runtime.status()
        loop_times.append(state["last_duration_ms"])
        captures.append(state["capture_ms"])
        publishes.append(state["publish_ms"])
        assert state["errors"] == 0
        return state

    try:
        opened = await run(NOW, opportunities)
        assert runtime.status()["open_count"] == positions * 2
        for i in range(1, 52):
            now = NOW + timedelta(seconds=i)
            for index in range(positions):
                if index % change_stride != i % change_stride:
                    continue
                price = 100 + math.sin(i / 5) * 0.2
                # Reuse fixture models as in the original benchmark; allocation
                # churn in the synthetic producer is not shadow resident memory.
                # Content revisions must work even when object identity is stable.
                for leg in range(2):
                    quote = markets[index * 2 + leg]
                    quote.timestamp = now
                    quote.bid = quote.ask = price * (1.005 if leg else 1)
            state = await run(now, opportunities[:op_count])
            cpus.append(state["worker_cpu_ms"])
            walls.append(state["worker_wall_ms"])
            recomputed.append(state["recomputed_positions"])
        now = NOW + timedelta(seconds=61)
        for quote in markets:
            quote.timestamp = now
        checkpoint = await run(now, opportunities[:op_count])
        gc.collect()
        rss_mib = (working_set() - baseline) / 1024 / 1024
        result = {"scenario": "stress" if change_stride == 1 else "realistic",
                  "open_rows": positions * 2, "opportunities": op_count, "rounds": len(cpus),
                  "changed_fraction": 1 / change_stride,
                  "capture_median_ms": statistics.median(captures),
                  "publish_median_ms": statistics.median(publishes),
                  "event_loop_median_ms": statistics.median(loop_times),
                  "event_loop_p95_ms": sorted(loop_times)[math.ceil(len(loop_times) * .95) - 1],
                  "event_loop_max_ms": max(loop_times),
                  "worker_cpu_median_ms": statistics.median(cpus),
                  "worker_cpu_mean_ms": statistics.mean(cpus),
                  "worker_wall_median_ms": statistics.median(walls),
                  "recomputed_median": statistics.median(recomputed),
                  "open_event_loop_ms": loop_times[0], "open_worker_wall_ms": opened["worker_wall_ms"],
                  "checkpoint_event_loop_ms": loop_times[-1],
                  "checkpoint_worker_wall_ms": checkpoint["worker_wall_ms"],
                  "rss_delta_mib": rss_mib,
                  "within_memory_budget": rss_mib <= 30,
                  "within_time_budget": statistics.median(cpus) <= cpu_budget}
        print("SHADOW_BENCHMARK " + json.dumps(result, sort_keys=True))
        assert runtime.errors == 0
        assert runtime.status()["open_count"] == positions * 2
        assert runtime.status()["pending_writes"] == 0
        assert runtime.status()["skipped_rounds"] == 0
        assert result["within_memory_budget"], "shadow RSS budget exceeded"
        assert result["within_time_budget"], "shadow worker CPU median budget exceeded"
        # Windows thread_time is quantized (often 15.625 ms). Retain the CPU
        # assertion and add a conservative wall-time guard, never accept zero
        # medians alone as proof of the realistic 8 ms budget.
        assert result["worker_wall_median_ms"] <= cpu_budget
        assert result["event_loop_median_ms"] <= 3
        assert result["event_loop_p95_ms"] <= 10
        assert result["event_loop_max_ms"] <= 20
        assert result["recomputed_median"] <= math.ceil(positions / change_stride)
        assert await runtime.repo.count_open() == positions * 2
    finally:
        await runtime.aclose()
        await db.close()
