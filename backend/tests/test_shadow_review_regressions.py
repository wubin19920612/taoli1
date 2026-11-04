"""Regressions for the second PM review, using offline SQLite and HTTP clients."""

import asyncio
from datetime import timedelta

import httpx
import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.db.repositories import ShadowTradeRepository
from app.db.schema import initialize_shadow_schema
from app.main import create_app
from app.services.shadow_ledger import ShadowLedger
from app.services.shadow_runtime import ShadowRuntime
from app.services.snapshot_consumers import PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from shadow_helpers import NOW, pair


async def seed_trades(db):
    await initialize_shadow_schema(db)
    book = ShadowLedger(Settings(shadow_enabled=True))
    markets, op = pair()
    book.process(markets, [op], NOW)
    closed = NOW + timedelta(seconds=1)
    book.process([m.model_copy(update={"timestamp": closed, "bid": 100, "ask": 100})
                  for m in markets], [], closed)
    await book.flush(ShadowTradeRepository(db))


@pytest.mark.asyncio
async def test_disabled_runtime_never_dispatches_or_mutates_shadow_table(monkeypatch):
    db = await connect_database(":memory:")
    runtime = ShadowRuntime(Settings(shadow_enabled=False, database_url="sqlite:///:memory:"), SnapshotStore())
    statements, dispatched = [], []
    original = asyncio.to_thread

    async def record_dispatch(*args, **kwargs):
        dispatched.append(args[0])
        return await original(*args, **kwargs)

    try:
        await seed_trades(db)
        await db.set_trace_callback(statements.append)
        monkeypatch.setattr(asyncio, "to_thread", record_dispatch)
        await runtime.initialize(db)
        await runtime.consume(PublishedSnapshot(1, NOW + timedelta(days=31), (), ()))
        stop = asyncio.Event()
        stop.set()
        await runtime.run_inline_observer(stop)
        await runtime.aclose()
        writes = [s for s in statements if "shadow_trades" in s.lower()
                  and s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        assert writes == []
        assert dispatched == []
        assert runtime._active is None and runtime.db is None
        assert runtime.status()["enabled"] is False
        assert len(await ShadowTradeRepository(db).list_trades()) == 2
    finally:
        await runtime.aclose()
        await db.close()


@pytest.mark.asyncio
async def test_disabled_api_hides_existing_rows():
    app = create_app(settings=Settings(shadow_enabled=False, database_url="sqlite:///:memory:",
                                     dashboard_password="test-only"), start_background_workers=False)
    db = await connect_database(":memory:")
    try:
        await seed_trades(db)
        await app.state.shadow_runtime.initialize(db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"X-Dashboard-Password": "test-only"}) as client:
            assert (await client.get("/api/shadow/trades")).json() == []
            assert (await client.get("/api/shadow/buckets", params={
                "since": NOW.isoformat(), "until": (NOW + timedelta(days=1)).isoformat()})).json() == []
            assert (await client.get("/api/shadow/status")).json()["enabled"] is False
    finally:
        await app.state.shadow_runtime.aclose()
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["inline", "background"])
async def test_disabled_lifespan_registers_no_shadow_tasks_or_consumer(monkeypatch, mode):
    import app.main as main
    names = []
    monkeypatch.setattr(main, "default_exchange_adapters", lambda: [])
    monkeypatch.setattr(main, "_start_background_task", lambda supervisor, factory, *, name: names.append(name))
    app = create_app(settings=Settings(shadow_enabled=False, database_url="sqlite:///:memory:",
                                     collector_post_publish_mode=mode), start_collector=True,
                     start_background_workers=True)
    async with app.router.lifespan_context(app):
        assert not any(name.startswith("shadow-") for name in names)
        assert getattr(app.state, "shadow_consumer", None) is None
        assert app.state.shadow_runtime._active is None
