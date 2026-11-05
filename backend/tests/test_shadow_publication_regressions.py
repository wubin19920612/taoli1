"""Third-round PM additions: publication must not corrupt ledger or identity state."""

import asyncio
from datetime import timedelta

import pytest

from app.core.config import Settings
from app.services.fake_opportunity import market_key
from app.services.shadow_runtime import ShadowRuntime
from app.services.snapshot_consumers import PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from shadow_helpers import NOW, pair


@pytest.mark.asyncio
async def test_publish_failure_preserves_held_snapshot_and_closes_missing_opportunity(tmp_path, monkeypatch):
    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True,
        database_url="sqlite:///" + str(tmp_path / "publish-failure.db")), store)
    await runtime.initialize(None)
    markets, op = pair()
    original_set = store.set_opportunities

    def fail_publish(opportunities):
        raise RuntimeError("injected publication failure")

    try:
        store.mark_published(NOW)
        monkeypatch.setattr(store, "set_opportunities", fail_publish)
        await runtime.consume(PublishedSnapshot(store.generation, NOW, tuple(markets), (op,)))
        assert await runtime.repo.count_open() == 2
        assert runtime.status()["error_counts"]["publish"] == 1
        held_after_open = runtime._held
        status_after_open = runtime.status()

        monkeypatch.setattr(store, "set_opportunities", original_set)
        now = NOW + timedelta(seconds=1)
        closing = tuple(m.model_copy(update={"timestamp": now, "bid": 100, "ask": 100}) for m in markets)
        store.mark_published(now)
        await runtime.consume(PublishedSnapshot(store.generation, now, closing, ()))
        rows = await runtime.repo.list_trades()
        assert len(rows) == 2
        assert all(row["exit_reason"] == "converged" for row in rows)
        assert all(row["closed_at"] == now.isoformat() for row in rows)
        assert held_after_open == (frozenset({op.id}), frozenset(market_key(m) for m in markets))
        assert status_after_open["open_count"] == 2
        assert runtime._held == (frozenset(), frozenset())
        assert runtime.status()["open_count"] == 0
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_at", ["_select_markets", "_work"])
async def test_identity_refresh_during_shadow_round_cannot_restore_old_opportunities(tmp_path, monkeypatch, pause_at):
    from app.api.routes_market_identities import _refresh_store
    from app.services.market_validation_review import MarketValidationService

    store = SnapshotStore()
    validator = MarketValidationService(None, store)
    runtime = ShadowRuntime(Settings(shadow_enabled=True,
        database_url="sqlite:///" + str(tmp_path / "identity-refresh.db")), store)
    await runtime.initialize(None)
    markets, op = pair()
    store.set_all_markets(markets)
    store.set_markets(markets)
    store.set_opportunities([op])
    store.mark_published(NOW)
    generation = store.generation
    entered, resume = asyncio.Event(), asyncio.Event()
    original_to_thread = asyncio.to_thread

    async def paused_dispatch(function, *args, **kwargs):
        if function == getattr(runtime, pause_at):
            entered.set()
            await resume.wait()
        return await original_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", paused_dispatch)
    try:
        task = await runtime.submit(PublishedSnapshot(generation, NOW, tuple(markets), (op,)))
        await asyncio.wait_for(entered.wait(), timeout=5)
        _refresh_store(validator)
        assert store.generation == generation
        assert store.get_opportunities() == []
        assert store.get_markets()[0] is not markets[0]
        resume.set()
        await task
        assert store.get_opportunities() == []
        assert store.opportunities_version == 2  # Collector + real identity invalidation only.
        assert runtime.status()["skipped_publications"] == 1
        assert runtime.status()["open_count"] == 2
        assert await runtime.repo.count_open() == 2
        assert runtime._held[0] == frozenset({op.id})
        assert runtime.errors == 0

        # Dropping presentation must not drop ledger progress. Self-publications
        # also advance the version; the next round must capture the new value.
        now = NOW + timedelta(seconds=1)
        closing = tuple(m.model_copy(update={"timestamp": now, "bid": 100, "ask": 100}) for m in markets)
        store.mark_published(now)
        await runtime.consume(PublishedSnapshot(store.generation, now, closing, ()))
        assert runtime.status()["open_count"] == 0
        assert all(row["exit_reason"] == "converged" for row in await runtime.repo.list_trades())
        assert store.opportunities_version == 3
        store.mark_published(now)
        await runtime.consume(PublishedSnapshot(store.generation, now, closing, ()))
        assert store.opportunities_version == 4
        assert runtime.status()["skipped_publications"] == 1
    finally:
        resume.set()
        await runtime.aclose()


def test_opportunity_versions_cover_every_set_without_changing_legacy_consumers():
    store = SnapshotStore()
    _, op = pair()
    assert store.opportunities_version == 0
    for version, values in enumerate(([op], [op], [], []), start=1):
        assert store.set_opportunities(values) is None
        assert store.opportunities_version == version
        result = store.get_opportunities()
        assert result == values and result is not values
        if values:
            assert result[0] is op
        assert store.sizes()["opportunities"] == len(values)
    store.mark_published(NOW)
    assert store.opportunities_version == 4
    assert not store.set_opportunities_if_version([op], 3)
    assert store.get_opportunities() == [] and store.opportunities_version == 4
    assert store.set_opportunities_if_version([op], 4)
    assert store.get_opportunities() == [op] and store.opportunities_version == 5
