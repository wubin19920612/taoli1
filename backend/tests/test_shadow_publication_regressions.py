"""Third-round PM additions: publication must not corrupt ledger or identity state."""

import asyncio
from datetime import timedelta

import pytest

from app.core.config import Settings
from app.services.fake_opportunity import market_key
from app.services.shadow_runtime import ShadowRuntime
from app.services.snapshot_consumers import PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from shadow_helpers import NOW, pair, empty_baseline


@pytest.mark.asyncio
async def test_queued_snapshot_before_identity_refresh_cannot_restore_old_opportunities(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.api.routes_market_identities import _refresh_store
    from app.services.collector import MarketCollector
    from app.services.market_validation_review import MarketValidationService
    from app.services.snapshot_consumers import LatestSnapshotSlot

    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True,
        database_url="sqlite:///" + str(tmp_path / "queued-refresh.db")), store)
    await runtime.initialize(None)
    empty_baseline(runtime.ledger)
    shadow_slot, history_slot = LatestSnapshotSlot(), LatestSnapshotSlot()
    recorder = SimpleNamespace(record=AsyncMock())
    collector = MarketCollector([], store, snapshot_slots=[shadow_slot, history_slot],
                                history_recorder=recorder)
    markets, op = pair()
    store.set_all_markets(markets)
    store.set_markets(markets)
    store.set_opportunities([op])
    store.mark_published(NOW)
    try:
        # Exercise the actual publisher/mailbox before any shadow submit runs.
        collector._publish_to_slots(markets, [op])
        _refresh_store(MarketValidationService(None, store))
        assert store.get_opportunities() == []
        assert store.opportunities_version == 2
        queued = await shadow_slot.get_newer(0, asyncio.Event())
        history = await history_slot.get_newer(0, asyncio.Event())
        assert queued is history
        await collector.record_history(history)
        recorder.record.assert_awaited_once_with([op], now=NOW)
        assert history.opportunities[0] is op and history.markets[0] is markets[0]

        await runtime.consume(queued)
        assert store.get_opportunities() == []
        assert store.opportunities_version == 2
        assert runtime.status()["skipped_publications"] == 1
        assert runtime.status()["open_count"] == 2
        assert await runtime.repo.count_open() == 2
        assert runtime._held[0] == frozenset({op.id})
        assert runtime.errors == 0
        assert queued.opportunities_version == 1
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_unversioned_snapshot_processes_ledger_without_publishing(tmp_path):
    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True,
        database_url="sqlite:///" + str(tmp_path / "unversioned.db")), store)
    await runtime.initialize(None)
    empty_baseline(runtime.ledger)
    markets, op = pair()
    store.mark_published(NOW)
    try:
        # Four-argument construction remains valid for legacy consumers, but
        # shadow must never infer its source version from the current store.
        await runtime.consume(PublishedSnapshot(store.generation, NOW, tuple(markets), (op,)))
        assert store.get_opportunities() == []
        assert store.opportunities_version == 0
        assert runtime.status()["skipped_publications"] == 1
        assert runtime.status()["open_count"] == await runtime.repo.count_open() == 2
        assert runtime.errors == 0
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_inline_observer_binds_version_before_submit(tmp_path, monkeypatch):
    from app.api.routes_market_identities import _refresh_store
    from app.services.market_validation_review import MarketValidationService

    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True,
        database_url="sqlite:///" + str(tmp_path / "inline-version.db")), store)
    await runtime.initialize(None)
    empty_baseline(runtime.ledger)
    markets, op = pair()
    store.set_all_markets(markets)
    store.set_markets(markets)
    store.set_opportunities([op])
    store.mark_published(NOW)
    stop = asyncio.Event()
    original_submit = runtime.submit

    async def refresh_before_submit(snapshot):
        assert snapshot.opportunities_version == 1
        assert snapshot.opportunities == (op,)
        _refresh_store(MarketValidationService(None, store))
        task = await original_submit(snapshot)
        await task
        stop.set()
        return task

    monkeypatch.setattr(runtime, "submit", refresh_before_submit)
    try:
        await asyncio.wait_for(runtime.run_inline_observer(stop), timeout=5)
        assert store.get_opportunities() == []
        assert store.opportunities_version == 2
        assert runtime.status()["skipped_publications"] == 1
        assert runtime.status()["open_count"] == 2
        assert runtime.errors == 0
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_publish_failure_preserves_held_snapshot_and_closes_missing_opportunity(tmp_path, monkeypatch):
    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True,
        database_url="sqlite:///" + str(tmp_path / "publish-failure.db")), store)
    await runtime.initialize(None)
    empty_baseline(runtime.ledger)
    markets, op = pair()
    original_set = store.set_opportunities

    def fail_publish(opportunities):
        raise RuntimeError("injected publication failure")

    try:
        store.mark_published(NOW)
        monkeypatch.setattr(store, "set_opportunities", fail_publish)
        await runtime.consume(PublishedSnapshot(store.generation, NOW, tuple(markets), (op,),
            opportunities_version=runtime.store.opportunities_version))
        assert await runtime.repo.count_open() == 2
        assert runtime.status()["error_counts"]["publish"] == 1
        held_after_open = runtime._held
        status_after_open = runtime.status()

        monkeypatch.setattr(store, "set_opportunities", original_set)
        now = NOW + timedelta(seconds=1)
        closing = tuple(m.model_copy(update={"timestamp": now, "bid": 100, "ask": 100}) for m in markets)
        store.mark_published(now)
        await runtime.consume(PublishedSnapshot(store.generation, now, closing, (),
            opportunities_version=runtime.store.opportunities_version))
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
    empty_baseline(runtime.ledger)
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
        task = await runtime.submit(PublishedSnapshot(generation, NOW, tuple(markets), (op,),
            opportunities_version=runtime.store.opportunities_version))
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
        await runtime.consume(PublishedSnapshot(store.generation, now, closing, (),
            opportunities_version=runtime.store.opportunities_version))
        assert runtime.status()["open_count"] == 0
        assert all(row["exit_reason"] == "converged" for row in await runtime.repo.list_trades())
        assert store.opportunities_version == 3
        store.mark_published(now)
        await runtime.consume(PublishedSnapshot(store.generation, now, closing, (),
            opportunities_version=runtime.store.opportunities_version))
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
