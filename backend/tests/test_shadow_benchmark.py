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
from app.db.schema import initialize_schema, initialize_shadow_schema
from app.db.repositories import ShadowTradeRepository
from app.services.shadow_ledger import ShadowLedger
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


def system_cpu_percent():
    """Windows system busy time over a pre-run sampling interval (all cores)."""
    def sample():
        idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
        if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise ctypes.WinError()
        return idle.value, kernel.value + user.value

    idle, total = sample()
    time.sleep(0.2)
    next_idle, next_total = sample()
    return 100 * (1 - (next_idle - idle) / (next_total - total))


@pytest.mark.benchmark
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,positions,op_count,change_stride,cpu_budget", [
    pytest.param("stress", 1500, 1000, 1, 20, id="stress"),
    pytest.param("realistic", 500, 400, 3, 8, id="realistic"),
    pytest.param("production", 750, 5500, 3, None, id="production"),
])
async def test_shadow_resource_budgets(tmp_path, scenario, positions, op_count, change_stride, cpu_budget):
    cpu_before = system_cpu_percent()
    markets, opportunities = [], []
    market_pairs = 6000 if scenario == "production" else positions
    for i in range(market_pairs):
        legs = [market(ex, price, symbol=f"C{i}USDT", raw_symbol=f"C{i}USDT")
                for ex, price in (("binance", 100), ("bitget", 100.5))]
        markets.extend(legs)
        opportunities.append(build_directional_opportunity(*legs, mode="FF"))
    path = str(tmp_path / "benchmark.db")
    db = await connect_database(path)
    await initialize_schema(db)
    if scenario == "production":
        # Seed 750 held pairs before measuring the production snapshot: only
        # 650 of its 5500 opportunities are above threshold, including 100
        # below-threshold held ids that must still be retained by selection.
        seed = ShadowLedger(Settings(shadow_enabled=True, shadow_max_open=1500))
        try:
            await initialize_shadow_schema(db)
            seed.process(markets, opportunities[:positions], NOW)
            await seed.flush(ShadowTradeRepository(db))
            assert seed.open_count == 1500
        except BaseException:
            await db.close()
            raise
        del seed
        # Use actual bid/ask spreads, including wider books on below-threshold
        # held pairs: their close spread stays above the convergence targets.
        # Merely overriding open_spread_pct would make the fixture inconsistent.
        for index in range(650, market_pairs):
            markets[index * 2].bid = 99.5
            markets[index * 2 + 1].bid = 100.1
        opportunities = [build_directional_opportunity(*markets[i * 2:i * 2 + 2], mode="FF")
                         for i in range(op_count)]
        assert len(markets) == 12000 and len(opportunities) == 5500
        assert sum(op.open_spread_pct >= .2 for op in opportunities) == 650
    store = SnapshotStore()
    gc.collect()
    baseline = working_set()
    runtime = ShadowRuntime(Settings(shadow_enabled=True, shadow_max_open=3000,
                                    database_url="sqlite:///" + path), store)
    runtime.catalog.entries = {("BINANCE", "FUTURE", f"C{i}", "") for i in range(positions)}
    await runtime.initialize(db)
    loop_times, cpus, walls, recomputed, captures, publishes = [], [], [], [], [], []
    segments, selections, calculations, phases = [], [], [], []

    async def run(now, ops):
        store.mark_published(now)
        snapshot = PublishedSnapshot(store.generation, now, tuple(markets), tuple(ops),
            opportunities_version=runtime.store.opportunities_version)
        # Runtime reports synchronous event-loop occupancy, excluding await time.
        # Includes immutable capture, dispatch and generation-checked publication.
        await runtime.consume(snapshot)
        state = runtime.status()
        loop_times.append(state["last_duration_ms"])
        captures.append(state["capture_ms"])
        segments.append(state["event_loop_max_segment_ms"])
        selections.append(state["select_wall_ms"])
        calculations.append(state["calculate_wall_ms"])
        phases.append(state["worker_phases_ms"])
        publishes.append(state["publish_ms"])
        assert state["errors"] == 0
        assert state["skipped_publications"] == 0
        return state

    try:
        opened = await run(NOW, opportunities)
        assert runtime.status()["open_count"] == positions * 2
        for i in range(1, 52):
            now = NOW + timedelta(seconds=i)
            for index in range(market_pairs):
                if index % change_stride != i % change_stride:
                    continue
                price = 100 + math.sin(i / 5) * 0.2
                # Reuse fixture models as in the original benchmark; allocation
                # churn in the synthetic producer is not shadow resident memory.
                # Content revisions must work even when object identity is stable.
                for leg in range(2):
                    quote = markets[index * 2 + leg]
                    quote.timestamp = now
                    quote.ask = price * (1.005 if leg else 1)
                    quote.bid = (price * (1.001 if leg else .995)
                                 if scenario == "production" and index >= 650 else quote.ask)
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
        wall_budget = 30 if scenario in {"stress", "production"} else cpu_budget
        result = {"scenario": scenario, "system_cpu_before_pct": cpu_before,
                  "markets": len(markets),
                  "open_rows": positions * 2, "opportunities": op_count, "rounds": len(cpus),
                  "changed_fraction": 1 / change_stride,
                  "capture_median_ms": statistics.median(captures),
                  "publish_median_ms": statistics.median(publishes),
                  "event_loop_median_ms": statistics.median(loop_times),
                  "event_loop_p95_ms": sorted(loop_times)[math.ceil(len(loop_times) * .95) - 1],
                  "event_loop_max_ms": max(segments) if scenario == "production" else max(loop_times),
                  "event_loop_total_max_ms": max(loop_times),
                  "select_wall_median_ms": statistics.median(selections),
                  "calculate_wall_median_ms": statistics.median(calculations),
                  "checkpoint_capture_segments_ms": checkpoint["capture_segments_ms"],
                  "price_states": runtime.status()["scorer_price_states"],
                  "worker_phase_medians_ms": {k: statistics.median(p[k] for p in phases) for k in phases[-1]},
                  "checkpoint_worker_phases_ms": checkpoint["worker_phases_ms"],
                  "asset_index_assets": len(runtime._asset_index),
                  "display_cache_entries": len(runtime._display_cache),
                  "worker_cpu_median_ms": statistics.median(cpus),
                  "worker_cpu_mean_ms": statistics.mean(cpus),
                  "worker_wall_median_ms": statistics.median(walls),
                  "recomputed_median": statistics.median(recomputed),
                  "open_event_loop_ms": loop_times[0], "open_worker_wall_ms": opened["worker_wall_ms"],
                  "checkpoint_event_loop_ms": loop_times[-1],
                  "checkpoint_worker_wall_ms": checkpoint["worker_wall_ms"],
                  "checkpoint_capture_ms": captures[-1],
                  "checkpoint_publish_ms": publishes[-1],
                  "rss_delta_mib": rss_mib,
                  "within_memory_budget": rss_mib <= 30,
                  "within_time_budget": (statistics.median(cpus) <= cpu_budget
                                         if cpu_budget is not None else statistics.median(walls) <= wall_budget)}
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
        assert result["worker_wall_median_ms"] <= wall_budget
        # 本机基准只防退化，开启判断看生产灰度（第三轮补充四第 21 条）。
        assert result["event_loop_median_ms"] <= (7 if scenario == "production" else 5 if scenario == "stress" else 3)
        assert result["event_loop_p95_ms"] <= (15 if scenario == "production" else 10)
        assert result["event_loop_max_ms"] <= (80 if scenario == "production" else 40 if scenario == "stress" else 20)
        assert result["recomputed_median"] <= math.ceil(positions / change_stride)
        assert await runtime.repo.count_open() == positions * 2
    finally:
        await runtime.aclose()
        await db.close()
