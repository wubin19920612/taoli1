import asyncio
import time
from datetime import timedelta
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from collector_yield_reference import BaselineCollector, BaselineValidation
from test_collector_compact_equivalence import NOW, Clock, output, setup_collector
from test_collector_pipeline import mixed_markets
from test_market_identity_validation import binding, market

from app.models.market_identity import MarketKey
from app.models.settings import RiskSettings, SymbolAlias
from app.services import market_validation_review, pair_validation
from app.services.collector import MarketCollector
from app.services.market_identity import MarketIdentityRegistry
from app.services.market_validation_review import MarketValidationService
from app.services.snapshot_store import SnapshotStore


@pytest.fixture
def fixed_clock(monkeypatch):
    import collector_yield_reference

    monkeypatch.setattr(Clock, "current", NOW)
    for module in (collector_yield_reference, market_validation_review, pair_validation):
        monkeypatch.setattr(module, "datetime", Clock)


async def test_yield_budget_uses_elapsed_time_and_resets_after_resume(monkeypatch):
    from app.runtime import cooperative

    clock = [1.0]
    monkeypatch.setattr(cooperative.time, "perf_counter", lambda: clock[0])
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)
        clock[0] += 1

    monkeypatch.setattr(cooperative.asyncio, "sleep", sleep)
    budget = cooperative.YieldBudget(budget_ms=20)
    await budget.maybe_yield()
    clock[0] += 0.019
    await budget.maybe_yield()
    assert sleeps == []
    clock[0] += 0.002
    await budget.maybe_yield()
    await budget.maybe_yield()
    assert sleeps == [0]


@pytest.mark.parametrize("seed", range(4))
async def test_async_alias_filter_build_equal_sync_all_fields(seed):
    from app.services.data_filters import filter_markets, filter_markets_async
    from app.services.spread_engine import build_all_opportunities, build_all_opportunities_async
    from app.services.symbol_aliases import apply_symbol_aliases, apply_symbol_aliases_async

    rows = mixed_markets(seed)
    before = [row.model_dump() for row in rows]
    risk = RiskSettings(excluded_symbols=["AIUSDT"], ignored_exchanges=["lighter"],
                        symbol_aliases=[SymbolAlias(exchange="bitget", symbol="BTC",
                                                    canonical_symbol="WBTC",
                                                    price_multiplier=0.1)])
    old = apply_symbol_aliases(rows, risk.symbol_aliases)
    new = await apply_symbol_aliases_async(rows, risk.symbol_aliases)
    assert new == old
    old = filter_markets(old, risk)
    new = await filter_markets_async(new, risk)
    assert new == old
    options = {"now": rows[0].timestamp, "stale_after_seconds": 30,
               "spot_fee_pct": 0.13, "future_fee_pct": 0.04, "safety_slippage_pct": 0.06}
    assert await build_all_opportunities_async(new, **options) == build_all_opportunities(old, **options)
    collector = MarketCollector([], SnapshotStore(), risk_settings=risk)
    assert await collector._build_labeled_opportunities_async(new, now=rows[0].timestamp) == (
        collector._build_labeled_opportunities(old, now=rows[0].timestamp)
    )
    assert [row.model_dump() for row in rows] == before


async def test_collector_matches_frozen_pipeline_and_publishes_atomically(fixed_clock, monkeypatch):
    from app.runtime.cooperative import YieldBudget

    old, _, _ = setup_collector(BaselineCollector)
    new, _, _ = setup_collector(MarketCollector)
    old.market_validation.__class__ = BaselineValidation
    previous = output(new.store)
    observations = []

    async def observe(self):
        observations.append(output(new.store))
        await asyncio.sleep(0)

    monkeypatch.setattr(YieldBudget, "maybe_yield", observe)
    await old.collect_once()
    await new.collect_once()
    assert observations and all(item == previous for item in observations)
    assert output(new.store) == output(old.store)
    assert new.store.get_opportunities()
    assert all(row.validation is not None for row in new.store.get_opportunities())


async def test_prepare_exposes_only_complete_collision_and_discovery_sets(fixed_clock, monkeypatch):
    from app.runtime.cooperative import YieldBudget

    rows = [market(), market("bybit"),
            market(raw="OTHERUSDT").model_copy(update={"symbol": "OTHERUSDT"}),
            market("bybit", raw="OTHERUSDT").model_copy(update={"symbol": "OTHERUSDT"})]
    repo = SimpleNamespace(discover=AsyncMock(), invalidate_opportunities=AsyncMock())
    service = MarketValidationService(repo, None)
    service.registry = MarketIdentityRegistry([
        binding(rows[0], "a"), binding(rows[1], "b"),
        binding(rows[2], "c"), binding(rows[3], "c", asset_kind="equity"),
    ])
    service.collision_symbols = {"OLD"}
    service.discovered = {"old-key"}
    new_collisions = {"PRLUSDT", "OTHERUSDT"}
    new_discovered = {"old-key"} | {MarketKey.from_snapshot(row).key for row in rows}
    observed = []

    async def observe(self):
        observed.append((service.collision_symbols.copy(), service.discovered.copy()))
        await asyncio.sleep(0)

    monkeypatch.setattr(YieldBudget, "maybe_yield", observe)
    await service.prepare(rows, RiskSettings())
    assert observed
    assert all(collisions in ({"OLD"}, new_collisions) for collisions, _ in observed)
    assert all(discovered in ({"old-key"}, new_discovered) for _, discovered in observed)
    assert service.collision_symbols == new_collisions
    assert service.discovered == new_discovered


async def test_registry_change_during_validation_does_not_republish(fixed_clock, monkeypatch):
    from app.api.routes_market_identities import _refresh_store
    from app.runtime.cooperative import YieldBudget

    rows = synthetic_markets(4)
    store = SnapshotStore()
    repo = SimpleNamespace(discover=AsyncMock(), invalidate_opportunities=AsyncMock())
    service = MarketValidationService(repo, store)
    service.registry = MarketIdentityRegistry([binding(row) for row in rows])
    service.settings.mode = "enforce"
    service.fx_retry_at = NOW + timedelta(days=1)
    adapters = [SimpleNamespace(name=row.exchange) for row in rows]
    collector = MarketCollector(
        adapters, store, market_validation=service,
        risk_settings=RiskSettings(huge_spread_pct=10000),
        now_fn=lambda: NOW, snapshot_slots=[],
    )
    collector._combined_exchange_markets = lambda _: rows
    for adapter in adapters:
        collector._state_for(adapter.name).next_due_at = NOW + timedelta(days=1)
    checked = 0
    changed = False
    original_validate = service.validate

    def validate(*args, **kwargs):
        nonlocal checked
        result = original_validate(*args, **kwargs)
        checked += 1
        return result

    async def checkpoint(self):
        nonlocal changed
        if checked and not changed:
            changed = True
            service.registry.replace([])
            service.invalidate()
            _refresh_store(service)
        await asyncio.sleep(0)

    monkeypatch.setattr(service, "validate", validate)
    monkeypatch.setattr(YieldBudget, "maybe_yield", checkpoint)
    with pytest.raises(ValueError, match="mapping changed"):
        await collector.collect_once()
    assert changed
    assert store.get_opportunities() == []
    assert store.generation == 0


@pytest.mark.parametrize("refresh_count", [1, 2])
async def test_prepare_retries_mapping_epoch_before_side_effects(
    fixed_clock, monkeypatch, refresh_count,
):
    from app.runtime.cooperative import YieldBudget

    rows = [market(), market("bybit")]
    repo = SimpleNamespace(
        discover=AsyncMock(), invalidate_opportunities=AsyncMock(),
        settings=AsyncMock(return_value=market_validation_review.MarketValidationSettings()),
        list_bindings=AsyncMock(),
    )
    service = MarketValidationService(repo, None)
    service.registry = MarketIdentityRegistry([binding(row, "old") for row in rows])
    service.collision_symbols = {"previous"}
    service.fx_retry_at = NOW + timedelta(days=1)
    annotations = []
    refreshed = 0
    annotate = MarketIdentityRegistry.annotate

    def record(self, row, now=None):
        result = annotate(self, row, now)
        annotations.append(result.asset_id)
        return result

    async def checkpoint(self):
        nonlocal refreshed
        # Refresh after the first row in each cooperative attempt.
        if len(annotations) == 2 * refreshed + 1 and refreshed < refresh_count:
            assert service.collision_symbols == {"previous"}
            assert service.discovered == set()
            repo.discover.assert_not_awaited()
            repo.invalidate_opportunities.assert_not_awaited()
            refreshed += 1
            repo.list_bindings.return_value = [binding(row, f"new{refreshed}") for row in rows]
            await service.refresh()
        # The third attempt must finish without cooperative checkpoints.
        assert len(annotations) < 5
        await asyncio.sleep(0)

    monkeypatch.setattr(MarketIdentityRegistry, "annotate", record)
    monkeypatch.setattr(YieldBudget, "maybe_yield", checkpoint)
    result = await service.prepare(rows, RiskSettings(huge_spread_pct=10000))
    assert refreshed == refresh_count
    assert annotations == ["old", "old"] + [
        asset for version in range(1, refresh_count + 1) for asset in [f"new{version}"] * 2
    ]
    assert [row.asset_id for row in result] == [f"new{refresh_count}"] * 2
    assert service.collision_symbols == set()
    assert not any(row.identity_collision for row in result)
    repo.invalidate_opportunities.assert_not_awaited()
    repo.discover.assert_awaited_once_with(rows)


async def test_collector_queues_rounds_and_pins_risk_across_yields(fixed_clock, monkeypatch):
    from app.runtime.cooperative import YieldBudget
    from app.services import collector as collector_module

    rows = synthetic_markets(4)
    old = RiskSettings(huge_spread_pct=10000, symbol_aliases=[
        SymbolAlias(exchange=row.exchange, symbol=row.symbol, canonical_symbol="OLDUSDT")
        for row in rows
    ])
    new = RiskSettings(huge_spread_pct=10000, excluded_symbols=["OLDUSDT"])
    loader = AsyncMock(side_effect=[old, new])
    service = synthetic_service()
    collector = MarketCollector(
        [SimpleNamespace(name=row.exchange) for row in rows], SnapshotStore(),
        risk_settings_loader=loader, market_validation=service,
        now_fn=lambda: NOW, snapshot_slots=[],
    )
    collector._claim_due_adapters = AsyncMock(return_value=[])
    collector._combined_exchange_markets = lambda _: rows
    entered, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observed = []
    validate = service.validate
    labels = collector_module.risk_labels_for

    def record_validate(a, b, risk):
        observed.append((asyncio.current_task().get_name(), risk))
        return validate(a, b, risk)

    def record_labels(item, risk, *args, **kwargs):
        observed.append((asyncio.current_task().get_name(), risk))
        return labels(item, risk, *args, **kwargs)

    async def checkpoint(self):
        if asyncio.current_task().get_name() == "first" and not entered.is_set():
            entered.set()
            await release.wait()
        await asyncio.sleep(0)

    async def second_round():
        second_started.set()
        return await collector.collect_once()

    monkeypatch.setattr(service, "validate", record_validate)
    monkeypatch.setattr(collector_module, "risk_labels_for", record_labels)
    monkeypatch.setattr(YieldBudget, "maybe_yield", checkpoint)
    first = asyncio.create_task(collector.collect_once(), name="first")
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(second_round(), name="second")
        await second_started.wait()
        for _ in range(10):
            await asyncio.sleep(0)
        assert loader.await_count == 1
        assert not second.done()
        # Even an external replacement must not alter this round's local snapshot.
        collector.risk_settings = new
        release.set()
        first_result, second_result = await asyncio.wait_for(asyncio.gather(first, second), 2)
    finally:
        release.set()
        await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)
    assert {row.symbol for row in first_result.markets} == {"OLDUSDT"}
    assert {row.symbol for row in second_result.markets} == {rows[0].symbol}
    assert first_result.opportunities and second_result.opportunities
    assert {name for name, _ in observed} == {"first", "second"}
    assert all(risk is (old if name == "first" else new) for name, risk in observed)


def synthetic_markets(count):
    rows = []
    for i in range(count):
        symbol = f"TOKEN{i // 4}USDT"
        rows.append(market(exchange=f"venue{i % 4}", raw=symbol, price=100 + i % 4).model_copy(
            update={"symbol": symbol, "base": symbol.removesuffix("USDT"), "timestamp": NOW,
                    "market_type": "spot" if i % 4 == 0 else "future"},
        ))
    # Use validated enum values, as adapters do.
    from app.models.market import MarketType
    for row in rows:
        row.market_type = MarketType(row.market_type)
    return rows


def synthetic_service(cls=MarketValidationService):
    repo = SimpleNamespace(discover=AsyncMock(), invalidate_opportunities=AsyncMock())
    service = cls(repo, None)
    service.fx_retry_at = NOW + timedelta(days=1)
    return service


async def measure_heartbeat(operation):
    beats = []
    done = asyncio.Event()

    async def heartbeat():
        while not done.is_set():
            beats.append(time.perf_counter())
            await asyncio.sleep(0.01)

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    started = time.perf_counter()
    try:
        result = await operation()
        elapsed = time.perf_counter() - started
        beats.append(time.perf_counter())
    finally:
        done.set()
        await task
    return elapsed, max(b - a for a, b in pairwise(beats)), len(beats), result


async def test_prepare_build_heartbeat_under_150ms(fixed_clock, monkeypatch):
    from app.runtime.cooperative import YieldBudget
    from app.services.spread_engine import build_all_opportunities_async

    init = YieldBudget.__init__
    monkeypatch.setattr(YieldBudget, "__init__", lambda self, budget_ms=20: init(self, 1))
    rows = synthetic_markets(2400)
    service = synthetic_service()
    risk = RiskSettings(huge_spread_pct=10000)

    async def operation():
        prepared = await service.prepare(rows, risk)
        return await build_all_opportunities_async(prepared, now=NOW)

    elapsed, gap, beats, result = await measure_heartbeat(operation)
    assert result
    assert beats > 2
    assert gap < 0.150
    assert elapsed < 5
