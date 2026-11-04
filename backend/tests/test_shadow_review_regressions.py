"""Regressions for the second PM review, using offline SQLite and HTTP clients."""

import asyncio
import sqlite3
import threading
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


@pytest.mark.asyncio
async def test_api_reads_only_committed_rows_across_rollback_and_commit(tmp_path):
    path = str(tmp_path / "read isolation.db")
    app = create_app(settings=Settings(shadow_enabled=True, database_url="sqlite:///" + path,
                                     dashboard_password="test-only"), start_background_workers=False)
    runtime = app.state.shadow_runtime
    await runtime.initialize(None)
    try:
        await seed_trades(runtime.db)
        rows, _ = await runtime.repo.restore(NOW)
        assert rows == []  # Seed consists of closed trades.
        committed = await runtime.repo.list_trades()
        pending = [{**r, "id": r["id"] + "pending", "opportunity_id": "pending"} for r in committed]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"X-Dashboard-Password": "test-only"}) as client:
            async def counts():
                trades = await client.get("/api/shadow/trades?status=closed")
                buckets = await client.get("/api/shadow/buckets", params={
                    "since": NOW.isoformat(), "until": (NOW + timedelta(days=1)).isoformat()})
                assert trades.status_code == buckets.status_code == 200
                return len(trades.json()), sum(b["n_evt"] for b in buckets.json())

            assert await counts() == (2, 2)
            await runtime.db.execute("BEGIN")
            await runtime.repo.save_batch(pending)
            assert len(await runtime.repo.list_trades()) == 4  # Writer sees its own pending rows.
            assert await counts() == (2, 2)
            await runtime.db.rollback()
            assert await counts() == (2, 2)
            await runtime.db.execute("BEGIN")
            await runtime.repo.save_batch(pending)
            assert await counts() == (2, 2)
            await runtime.db.commit()
            assert await counts() == (4, 4)
    finally:
        await runtime.db.rollback()
        await runtime.aclose()


@pytest.mark.asyncio
async def test_api_reader_connection_is_read_only_and_off_event_loop(tmp_path, monkeypatch):
    import app.api.routes_shadow as routes
    path = str(tmp_path / "read # only.db")
    app = create_app(settings=Settings(shadow_enabled=True, database_url="sqlite:///" + path),
                     start_background_workers=False)
    runtime = app.state.shadow_runtime
    await runtime.initialize(None)
    original = routes.aiosqlite.connect
    connections = []

    def connect(uri, **kwargs):
        connections.append((uri, kwargs, threading.get_ident()))
        return original(uri, **kwargs)

    monkeypatch.setattr(routes.aiosqlite, "connect", connect)
    try:
        await seed_trades(runtime.db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/shadow/trades")).status_code == 200
        assert len(connections) == 1
        uri, kwargs, thread = connections[0]
        assert uri.endswith("?mode=ro") and kwargs == {"uri": True}
        assert thread != threading.get_ident()
        with sqlite3.connect(uri, **kwargs) as db:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                db.execute("DELETE FROM shadow_trades")
    finally:
        await runtime.aclose()
