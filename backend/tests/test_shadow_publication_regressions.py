"""Third-round PM additions: publication must not corrupt ledger or identity state."""

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
