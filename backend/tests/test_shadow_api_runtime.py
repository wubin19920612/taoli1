import logging
from datetime import timedelta

import httpx
import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from shadow_worker_harness import ShadowRuntime
from app.services.shadow_ledger import ShadowLedger
from app.services.snapshot_consumers import PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from auth_helpers import IsolatedSessionLookup, TEST_TOTP_SECRET, session_headers
from shadow_helpers import NOW, pair, empty_baseline


@pytest.mark.asyncio
async def test_api_auth_validation_and_empty_disabled_status(tmp_path):
    path = str(tmp_path / "api.db")
    app = create_app(settings=Settings(database_url="sqlite:///" + path, dashboard_password="test-only",
                                     dashboard_totp_secret=TEST_TOTP_SECRET),
                     start_background_workers=False)
    app.state.auth_store = IsolatedSessionLookup()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for endpoint in ("/api/shadow/status", "/api/shadow/trades", "/api/shadow/buckets"):
            assert (await client.get(endpoint)).status_code == 401
        db = await connect_database(path)
        try:
            await initialize_schema(db)
            await app.state.shadow_runtime.initialize(db)
            client.headers.update(session_headers("test-password"))
            status = (await client.get("/api/shadow/status")).json()
            assert status["enabled"] is False and status["open_count"] == 0
            assert (await client.get("/api/shadow/buckets")).json() == []
            assert (await client.get("/api/shadow/trades?status=open")).json() == []
            assert (await client.get("/api/shadow/buckets?dims=variant,variant")).status_code == 400
            assert (await client.get("/api/shadow/buckets?dims=invalid")).status_code == 400
            assert (await client.get("/api/shadow/buckets?since=2026-11-03")).status_code == 400
            assert (await client.get("/api/shadow/trades?status=unknown")).status_code == 422
            assert (await client.get("/api/shadow/trades?limit=501")).status_code == 422
            # Data-bearing API checks require explicit enablement under PM round 2.
            app.state.shadow_runtime = ShadowRuntime(
                Settings(shadow_enabled=True, database_url="sqlite:///" + path), SnapshotStore())
            await app.state.shadow_runtime.initialize(db)
            book = ShadowLedger(Settings(shadow_enabled=True))
            empty_baseline(book)
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
            await app.state.shadow_runtime.aclose()
            await db.close()


@pytest.mark.asyncio
async def test_runtime_preserves_published_models_and_isolates_failed_writes(caplog):
    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True, database_url="sqlite:///:memory:"), store)
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        await runtime.initialize(db)
        empty_baseline(runtime.ledger)
        markets, op = pair()
        store.set_opportunities([op])
        store.mark_published(NOW)
        snapshot = PublishedSnapshot(store.generation, NOW, tuple(markets), (op,),
            opportunities_version=runtime.store.opportunities_version)
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
                await runtime.consume(PublishedSnapshot(2, now, closing, (),
                    opportunities_version=runtime.store.opportunities_version))
        assert runtime.errors == 3
        assert sum("shadow consumer failed" in r.message for r in caplog.records) == 1
        assert len(runtime.ledger.pending) == 2
        assert store.get_opportunities()  # older generation never overwrites newer store
        runtime.repo.save_batch = original_save
        await runtime.consume(PublishedSnapshot(2, now, closing, (),
            opportunities_version=runtime.store.opportunities_version))
        assert not runtime.ledger.pending
        assert len(await runtime.repo.list_trades(status="closed")) == 2
    finally:
        await runtime.aclose()
        await db.close()


@pytest.mark.asyncio
async def test_shadow_schema_failure_does_not_escape_startup(monkeypatch):
    import shadow_worker_harness as module
    async def broken(db):
        raise RuntimeError("synthetic shadow schema failure")
    monkeypatch.setattr(module, "initialize_shadow_schema", broken)
    runtime = ShadowRuntime(Settings(shadow_enabled=True, database_url="sqlite:///:memory:"), SnapshotStore())
    await runtime.initialize(None)
    assert not runtime.status()["ready"] and runtime.errors == 1


@pytest.fixture(autouse=True)
def child_core_runtime_for_injection(monkeypatch):
    """These tests inject failures into the child core, not the IPC transport."""
    import app.main
    monkeypatch.setattr(app.main, "ShadowRuntime", ShadowRuntime)
