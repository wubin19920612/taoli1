import logging
from datetime import timedelta

import httpx
import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.services.shadow_runtime import ShadowRuntime
from app.services.shadow_ledger import ShadowLedger
from app.services.snapshot_consumers import PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from shadow_helpers import NOW, pair


@pytest.mark.asyncio
async def test_api_auth_validation_and_empty_disabled_status():
    app = create_app(settings=Settings(database_url="sqlite:///:memory:", dashboard_password="test-only"),
                     start_background_workers=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/api/shadow/status", "/api/shadow/trades", "/api/shadow/buckets"):
            assert (await client.get(path)).status_code == 401
        db = await connect_database(":memory:")
        try:
            await initialize_schema(db)
            await app.state.shadow_runtime.initialize(db)
            client.headers["X-Dashboard-Password"] = "test-only"
            status = (await client.get("/api/shadow/status")).json()
            assert status["enabled"] is False and status["open_count"] == 0
            assert (await client.get("/api/shadow/buckets")).json() == []
            assert (await client.get("/api/shadow/trades?status=open")).json() == []
            assert (await client.get("/api/shadow/buckets?dims=variant,variant")).status_code == 400
            assert (await client.get("/api/shadow/buckets?dims=invalid")).status_code == 400
            assert (await client.get("/api/shadow/buckets?since=2026-11-03")).status_code == 400
            assert (await client.get("/api/shadow/trades?status=unknown")).status_code == 422
            assert (await client.get("/api/shadow/trades?limit=501")).status_code == 422
            book = ShadowLedger(Settings(shadow_enabled=True))
            markets, op = pair()
            book.process(markets, [op], NOW)
            await book.flush(app.state.shadow_runtime.repo)
            closed_at = NOW + timedelta(seconds=5)
            closing = [m.model_copy(update={"timestamp": closed_at, "bid": 100, "ask": 100}) for m in markets]
            book.process(closing, [], closed_at)
            await book.flush(app.state.shadow_runtime.repo)
            response = await client.get("/api/shadow/buckets", params={"dims": "fake_flag,executable",
                "since": NOW.isoformat(), "until": closed_at.isoformat()})
            assert response.status_code == 200
            bucket = response.json()[0]
            assert bucket["n_evt"] == 2
            assert bucket["dimensions"] == {"fake_flag": False, "executable": False}
            assert (await client.get("/api/shadow/trades?status=open")).json() == []
            assert len((await client.get("/api/shadow/trades?status=closed")).json()) == 2
        finally:
            await db.close()


@pytest.mark.asyncio
async def test_runtime_preserves_published_models_and_isolates_failed_writes(caplog):
    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True, database_url="sqlite:///:memory:"), store)
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        await runtime.initialize(db)
        markets, op = pair()
        store.set_opportunities([op])
        store.mark_published(NOW)
        snapshot = PublishedSnapshot(store.generation, NOW, tuple(markets), (op,))
        await runtime.consume(snapshot)
        assert op.fake_score is None and op.buy_routes == []
        assert store.get_opportunities()[0].fake_score == 0
        assert runtime.status()["open_count"] == 2
        original_save = runtime.repo.save_batch
        async def broken(rows):
            raise RuntimeError("fixture write failure")
        runtime.repo.save_batch = broken
        now = NOW + timedelta(seconds=1)
        closing = tuple(m.model_copy(update={"timestamp": now, "bid": 100, "ask": 100}) for m in markets)
        with caplog.at_level(logging.ERROR):
            for _ in range(3):
                await runtime.consume(PublishedSnapshot(2, now, closing, ()))
        assert runtime.errors == 3
        assert sum("shadow consumer failed" in r.message for r in caplog.records) == 1
        assert len(runtime.ledger.pending) == 2
        assert store.get_opportunities()  # older generation never overwrites newer store
        runtime.repo.save_batch = original_save
        await runtime.consume(PublishedSnapshot(2, now, closing, ()))
        assert not runtime.ledger.pending
        assert len(await runtime.repo.list_trades(status="closed")) == 2
    finally:
        await runtime.aclose()
        await db.close()


@pytest.mark.asyncio
async def test_shadow_schema_failure_does_not_escape_startup(monkeypatch):
    import app.services.shadow_runtime as module
    async def broken(db):
        raise RuntimeError("synthetic shadow schema failure")
    monkeypatch.setattr(module, "initialize_shadow_schema", broken)
    runtime = ShadowRuntime(Settings(database_url="sqlite:///:memory:"), SnapshotStore())
    await runtime.initialize(None)
    assert not runtime.status()["ready"] and runtime.errors == 1
