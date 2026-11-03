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
async def test_shadow_3000_rows_1000_opportunities(tmp_path):
    markets, opportunities = [], []
    for i in range(1500):
        legs = [market(ex, price, symbol=f"C{i}USDT", raw_symbol=f"C{i}USDT")
                for ex, price in (("binance", 100), ("bitget", 100.5))]
        markets.extend(legs)
        opportunities.append(build_directional_opportunity(*legs, mode="FF"))
    db = await connect_database(str(tmp_path / "benchmark.db"))
    await initialize_schema(db)
    store = SnapshotStore()
    gc.collect()
    baseline = working_set()
    runtime = ShadowRuntime(Settings(shadow_enabled=True, database_url="sqlite:///:memory:"), store)
    runtime.catalog.entries = {("BINANCE", "FUTURE", f"C{i}", "") for i in range(1500)}
    await runtime.initialize(db)
    store.mark_published(NOW)
    started = time.perf_counter()
    await runtime.consume(PublishedSnapshot(store.generation, NOW, tuple(markets), tuple(opportunities)))
    open_ms = (time.perf_counter() - started) * 1000
    assert runtime.ledger.open_count == 3000 and runtime.errors == 0
    durations = []
    try:
        for i in range(1, 52):
            now = NOW + timedelta(seconds=i)
            for index, m in enumerate(markets):
                m.timestamp = now
                price = 100 + math.sin(i / 5) * 0.2
                m.bid = m.ask = price * (1.005 if index % 2 else 1)
            store.mark_published(now)
            snapshot = PublishedSnapshot(store.generation, now, tuple(markets), tuple(opportunities[:1000]))
            started = time.perf_counter()
            await runtime.consume(snapshot)
            durations.append((time.perf_counter() - started) * 1000)
        now = NOW + timedelta(seconds=61)
        for m in markets:
            m.timestamp = now
        store.mark_published(now)
        started = time.perf_counter()
        await runtime.consume(PublishedSnapshot(store.generation, now, tuple(markets), tuple(opportunities[:1000])))
        checkpoint_ms = (time.perf_counter() - started) * 1000
        gc.collect()
        rss_mib = (working_set() - baseline) / 1024 / 1024
        result = {"open_rows": 3000, "opportunities": 1000, "rounds": len(durations),
                  "median_ms": statistics.median(durations), "p95_ms": sorted(durations)[int(len(durations) * .95)],
                  "open_batch_ms": open_ms, "checkpoint_batch_ms": checkpoint_ms, "rss_delta_mib": rss_mib,
                  "within_time_budget": statistics.median(durations) <= 5, "within_memory_budget": rss_mib <= 30}
        print("SHADOW_BENCHMARK " + json.dumps(result, sort_keys=True))
        assert runtime.errors == 0
        assert result["within_memory_budget"], "shadow RSS budget exceeded"
        assert result["within_time_budget"], "shadow per-round median budget exceeded"
    finally:
        await runtime.aclose()
        await db.close()
