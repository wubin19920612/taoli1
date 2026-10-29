"""Consumed batches must not survive the collector's later cooperative waits."""

import asyncio
import gc
import hashlib
import inspect
import tracemalloc
import weakref
from datetime import timedelta
from textwrap import dedent
from types import SimpleNamespace

import pytest
from test_collector import FixedAdapter, MutableAdapter, market_on
from test_collector_compact_equivalence import NOW, Clock

from app.models.market import MarketSnapshot, MarketType
from app.models.settings import RiskSettings, SymbolAlias
from app.services import collector as collector_module
from app.services import market_validation_review, pair_validation
from app.services.collector import MarketCollector
from app.services.market_validation_review import MarketValidationService
from app.services.snapshot_consumers import LatestSnapshotSlot
from app.services.snapshot_store import SnapshotStore


@pytest.mark.parametrize("batch", ["adapter", "unpacked"])
async def test_consumed_batches_released_while_prepare_waits(monkeypatch, batch):
    refs = {"adapter": [], "unpacked": [], "reused": []}

    class TrackingAdapter(MutableAdapter):
        async def fetch_spot_tickers(self):
            rows = await super().fetch_spot_tickers()
            rows.append(market_on(self.name, "KEEPUSDT"))
            refs["adapter"].extend(weakref.ref(row) for row in rows)
            return rows

    entered, resume = asyncio.Event(), asyncio.Event()

    async def prepare(rows, risk):
        entered.set()
        await resume.wait()
        return rows

    store = SnapshotStore()
    collector = MarketCollector(
        [TrackingAdapter("fixed", "BTCUSDT")], store,
        risk_settings=RiskSettings(symbol_aliases=[SymbolAlias(
            exchange="fixed", symbol="BTCUSDT", canonical_symbol="RENAMEDUSDT",
            price_multiplier=2,
        )]),
        market_validation=SimpleNamespace(prepare=prepare), snapshot_slots=[],
    )
    combined = collector._combined_exchange_markets

    def track_unpacked(adapters):
        rows = combined(adapters)
        for row in rows:
            key = "unpacked" if row.symbol == "BTCUSDT" else "reused"
            refs[key].append(weakref.ref(row))
        return rows

    monkeypatch.setattr(collector, "_combined_exchange_markets", track_unpacked)
    task = asyncio.create_task(collector.collect_once())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert not task.done()
        assert {key: len(value) for key, value in refs.items()} == {
            "adapter": 2, "unpacked": 1, "reused": 1,
        }
        gc.collect()
        assert all(ref() is None for ref in refs[batch]), f"{batch} batch retained during prepare"
        # An unaliased row is still needed by prepare and must remain alive.
        assert refs["reused"][0]() is not None
    finally:
        resume.set()
        result = await asyncio.wait_for(task, timeout=2)

    assert [row.symbol for row in result.markets] == ["RENAMEDUSDT", "KEEPUSDT"]
    assert [row.raw_symbol for row in result.markets] == ["BTCUSDT", "KEEPUSDT"]
    assert result.markets[0].bid == 198
    assert result.markets[0].symbol_alias_price_multiplier == 2
    assert result.markets[1] is refs["reused"][0]()
    assert result.markets == store.get_markets()
    assert result.opportunities == []
    assert result.exchange_errors == {}
    assert store.generation == 1


class SyntheticAdapter(FixedAdapter):
    """Generate fresh rows per poll without keeping adapter-owned references."""

    def __init__(self, name, ids, kind, bid, ask):
        super().__init__(name=name)
        self.ids, self.kind, self.bid, self.ask = ids, kind, bid, ask

    def rows(self):
        return [MarketSnapshot(
            symbol=f"T{i}USDT", base=f"T{i}", raw_symbol=f"T{i}USDT",
            exchange=self.name, market_type=self.kind, timestamp=NOW,
            bid=self.bid, ask=self.ask, bid_size=10000, ask_size=10000,
            volume_24h_usdt=10_000_000,
        ) for i in self.ids]

    async def fetch_spot_tickers(self):
        return self.rows() if self.kind == MarketType.SPOT else []

    async def fetch_future_tickers(self):
        return self.rows() if self.kind == MarketType.FUTURE else []


class OfflineRepository:
    # Unlike AsyncMock, these fakes do not retain discover()'s input batches.
    async def discover(self, rows):
        pass

    async def invalidate_opportunities(self, ids):
        pass


def retaining_collector_type():
    # Keep every current yield and pipeline step; undo only the two releases
    # in memory to isolate the before/after comparison without editing files.
    source = dedent(inspect.getsource(MarketCollector._collect_once))
    for line in ("            del results\n", "        del markets\n"):
        assert source.count(line) == 1
        source = source.replace(line, "", 1)
    namespace = vars(collector_module).copy()
    exec(compile(source, "<collector before reference release>", "exec"), namespace)
    return type("RetainingCollector", (MarketCollector,), {
        "_collect_once": namespace["_collect_once"],
    })


async def measure_round_peak(cls):
    store = SnapshotStore()
    validation = MarketValidationService(OfflineRepository(), store)
    validation.fx_retry_at = NOW + timedelta(days=1)
    collector = cls([
        SyntheticAdapter("binance", range(2500), MarketType.SPOT, 100.0, 100.1),
        SyntheticAdapter("bybit", range(2500), MarketType.FUTURE, 101.0, 101.1),
        SyntheticAdapter("okx", range(2500, 9900), MarketType.SPOT, 100.0, 100.1),
    ], store, market_validation=validation, now_fn=lambda: NOW,
        risk_settings=RiskSettings(symbol_aliases=[], stale_after_seconds=600),
        snapshot_slots=[LatestSnapshotSlot()],
    )
    await collector.collect_once()  # Warm caches and retain the previous publication.
    for state in collector._poll_states.values():
        state.next_due_at = None
    gc.collect()
    assert not tracemalloc.is_tracing()
    tracemalloc.start(1)
    try:
        result = await collector.collect_once()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert store.sizes() == {"markets": 12400, "all_markets": 12400, "opportunities": 2500}
    assert len(result.markets) == 12400
    assert len(result.opportunities) == 2500
    assert result.exchange_errors == {}
    assert not validation.tasks and validation.fx_task is None
    # Compare all published fields outside the measured interval.
    digest = hashlib.sha256()
    for rows in (store.get_all_markets(), result.markets, result.opportunities):
        for row in rows:
            digest.update(row.model_dump_json().encode())
            digest.update(b"\n")
    return peak, digest.hexdigest()


@pytest.mark.benchmark
async def test_collector_release_refs_peak(monkeypatch):
    monkeypatch.setattr(Clock, "current", NOW)
    for module in (market_validation_review, pair_validation):
        monkeypatch.setattr(module, "datetime", Clock)
    before, before_digest = await measure_round_peak(retaining_collector_type())
    after, after_digest = await measure_round_peak(MarketCollector)
    print(
        f"collector_release_refs: rows=12400 opportunities=2500 "
        f"before_peak_mib={before / 2**20:.3f} after_peak_mib={after / 2**20:.3f} "
        f"saved_mib={(before - after) / 2**20:.3f}"
    )
    assert after_digest == before_digest
    assert after < before
