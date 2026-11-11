"""Offline regressions for the four PM-approved loop-stall review fixes."""

import asyncio
import gc
import inspect
import weakref
from unittest.mock import Mock

import pytest
from loop_stall_fixture import Clock, NOW, dump, prepared, workload

from app.models.market_identity import MarketKey
from app.models.settings import RiskSettings
from app.services import market_validation_review as review, pair_validation
from app.services.market_validation_review import MarketValidationService


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(Clock, "current", NOW)
    for module in (review, pair_validation):
        monkeypatch.setattr(module, "datetime", Clock)


def pending_pair_data():
    raw, bindings, opportunities = workload(3)
    raw = raw[3:]
    pending_key = MarketKey.from_snapshot(raw[1]).key
    bindings = [binding for binding in bindings if binding.market.key != pending_key]
    return raw, bindings, opportunities[1:]


async def test_old_prepared_rows_fail_closed_while_new_collisions_are_published(monkeypatch):
    data = pending_pair_data()
    validator, rows, _ = await prepared(MarketValidationService, data)
    a, b = rows[:2]
    initial = validator.validate(a, b, annotated=True)
    assert set(initial.reason_codes) == {"ASSET_IDENTITY_UNVERIFIED", "UNIT_UNVERIFIED"}
    assert not validator.should_enforce(initial)
    epoch = validator._mutation_epoch
    published = asyncio.Event()
    release = asyncio.Event()

    class PauseAfterCollisionPublication:
        async def maybe_yield(self):
            frame = inspect.currentframe().f_back
            if (frame.f_locals.get("collision_symbols") is validator.collision_symbols
                    and validator.collision_symbols and not published.is_set()):
                published.set()
                await release.wait()

    monkeypatch.setattr(review, "YieldBudget", PauseAfterCollisionPublication)
    next_rows = [row.model_copy(update={"symbol": a.symbol}) for row in data[0]]
    task = asyncio.create_task(validator.prepare(next_rows, RiskSettings()))
    try:
        await asyncio.wait_for(published.wait(), timeout=2)
        assert validator._mutation_epoch == epoch
        assert validator.collision_symbols == {a.symbol}
        assert not validator._prepared_rows
        annotate = Mock(wraps=validator.annotate)
        monkeypatch.setattr(validator, "annotate", annotate)
        actual = validator.validate(a, b, annotated=True)
        assert annotate.call_count == 2
        assert "ASSET_IDENTITY_CONFLICT" in actual.reason_codes
        assert not actual.capabilities.strategy_alert
        assert validator.should_enforce(actual)
        assert dump([actual]) == dump([validator.validate(a, b)])
    finally:
        release.set()
        await task
        await validator.aclose()
    assert validator._prepared_collision is validator.collision_symbols


async def test_collision_object_replacement_invalidates_prepared_reuse(monkeypatch):
    validator, rows, _ = await prepared(MarketValidationService, pending_pair_data())
    a, b = rows[:2]
    validator.collision_symbols = {a.symbol}
    annotate = Mock(wraps=validator.annotate)
    monkeypatch.setattr(validator, "annotate", annotate)
    actual = validator.validate(a, b, annotated=True)
    assert annotate.call_count == 2
    assert "ASSET_IDENTITY_CONFLICT" in actual.reason_codes
    assert not actual.capabilities.strategy_alert
    assert dump([actual]) == dump([validator.validate(a, b)])


@pytest.mark.parametrize("entry", ["filter", "filter_async", "prepare"])
async def test_three_publications_release_old_index_rows_without_recomputation(entry):
    raw, bindings, opportunities = workload(3)
    data = (raw[3:], bindings, opportunities[1:])
    validator, rows, _ = await prepared(MarketValidationService, data)
    oldest = weakref.ref(rows[0])
    validator._market_index()
    assert validator._market_legs_cache
    cached = opportunities[1].model_copy(update={
        "validation": validator.validate(*rows[:2], annotated=True),
    })
    del rows
    for _ in range(2):
        rows = await validator.prepare(data[0], RiskSettings())
        validator.store.set_all_markets(rows)
        validator.store.mark_published(NOW)
        if entry == "filter":
            assert validator.filter_opportunities([cached], RiskSettings()) == [cached]
        elif entry == "filter_async":
            assert await validator.filter_opportunities_async([cached], RiskSettings()) == [cached]
        assert validator.last_filter_recomputed == 0
        del rows
    assert validator.store.generation == 3
    assert validator._market_index_cache is None
    assert validator._market_legs_cache == {}
    gc.collect()
    assert oldest() is None
    await validator.aclose()
