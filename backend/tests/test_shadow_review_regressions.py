"""Regressions for the second PM review, using offline SQLite and HTTP clients."""

import asyncio
import logging
import sqlite3
import threading
from datetime import timedelta

import httpx
import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.db.auth import AuthStore
from app.db.repositories import ShadowTradeRepository
from app.db.schema import initialize_shadow_schema
from app.main import create_app
from app.services.shadow_ledger import ShadowLedger
from shadow_worker_harness import ShadowRuntime
from app.services.snapshot_consumers import PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from auth_helpers import IsolatedSessionLookup, TEST_TOTP_SECRET, seed_sessions, session_headers
from shadow_helpers import NOW, pair, empty_baseline


async def seed_trades(db):
    await initialize_shadow_schema(db)
    book = ShadowLedger(Settings(shadow_enabled=True))
    empty_baseline(book)
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
        await runtime.consume(PublishedSnapshot(1, NOW + timedelta(days=31), (), (),
            opportunities_version=runtime.store.opportunities_version))
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
                                     dashboard_password="test-only", dashboard_totp_secret=TEST_TOTP_SECRET),
                     start_background_workers=False)
    app.state.auth_store = IsolatedSessionLookup()
    db = await connect_database(":memory:")
    try:
        await seed_trades(db)
        await app.state.shadow_runtime.initialize(db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers=session_headers("test-password")) as client:
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
                                     dashboard_password="test-only", dashboard_totp_secret=TEST_TOTP_SECRET),
                     start_background_workers=False)
    app.state.auth_store = IsolatedSessionLookup()
    runtime = app.state.shadow_runtime
    await runtime.initialize(None)
    try:
        await seed_trades(runtime.db)
        rows, _ = await runtime.repo.restore(NOW)
        assert rows == []  # Seed consists of closed trades.
        committed = await runtime.repo.list_trades()
        pending = [{**r, "id": r["id"] + "pending", "opportunity_id": "pending"} for r in committed]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers=session_headers("test-password")) as client:
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
    app = create_app(settings=Settings(shadow_enabled=True, database_url="sqlite:///" + path,
                                     dashboard_password="test-only", dashboard_totp_secret=TEST_TOTP_SECRET),
                     start_background_workers=False)
    app.state.auth_store = await AuthStore.open(":memory:")
    await seed_sessions(app.state.auth_store)
    runtime = app.state.shadow_runtime
    await runtime.initialize(None)
    original = routes.aiosqlite.connect
    connections = []

    def connect(database, **kwargs):
        connections.append((database, kwargs, threading.get_ident()))
        return original(database, **kwargs)

    monkeypatch.setattr(routes.aiosqlite, "connect", connect)
    try:
        await seed_trades(runtime.db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers=session_headers("test-password")) as client:
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
        await app.state.auth_store.close()


@pytest.mark.asyncio
async def test_capture_errors_are_rate_limited_and_counted_by_category(monkeypatch, caplog):
    import shadow_worker_harness as module
    runtime = ShadowRuntime(Settings(shadow_enabled=True), SnapshotStore())
    clock = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])

    def broken(*args):
        raise ValueError("synthetic capture failure")

    monkeypatch.setattr(module, "freeze_snapshot", broken)
    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            assert await runtime.submit(PublishedSnapshot(1, NOW, (), (),
                opportunities_version=runtime.store.opportunities_version)) is None
        assert len(caplog.records) == 1
        assert runtime.status()["error_counts"] == {"capture": 3}
        clock[0] += 299
        await runtime.submit(PublishedSnapshot(1, NOW, (), (),
            opportunities_version=runtime.store.opportunities_version))
        assert len(caplog.records) == 1
        clock[0] += 1
        await runtime.submit(PublishedSnapshot(1, NOW, (), (),
            opportunities_version=runtime.store.opportunities_version))
        assert len(caplog.records) == 2
        assert runtime.status()["errors"] == 5
        counts = runtime.status()["error_counts"]
        counts["capture"] = 999
        assert runtime.status()["error_counts"] == {"capture": 5}


@pytest.mark.asyncio
async def test_catalog_errors_share_runtime_category_counters(monkeypatch, caplog):
    runtime = ShadowRuntime(Settings(shadow_enabled=True), SnapshotStore())

    async def broken():
        raise ValueError("synthetic catalogue failure")

    runtime.catalog.fetch = broken
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            runtime.catalog.retry_at = 0
            await runtime.catalog.refresh()
        assert len(caplog.records) == 1
        assert runtime.status()["error_counts"] == {"catalog": 3}
        assert runtime.status()["catalog_errors"] == 3


@pytest.mark.asyncio
async def test_publish_failure_is_isolated_and_close_still_drains(tmp_path, monkeypatch, caplog):
    runtime = ShadowRuntime(Settings(shadow_enabled=True, database_url="sqlite:///" + str(tmp_path / "publish.db")),
                            SnapshotStore())
    await runtime.initialize(None)
    runtime.store.mark_published(NOW)

    def broken(*args):
        raise ValueError("synthetic publication failure")

    monkeypatch.setattr(runtime.store, "set_opportunities", broken)
    try:
        with caplog.at_level(logging.ERROR):
            for _ in range(3):
                await runtime.consume(PublishedSnapshot(runtime.store.generation, NOW, (), (),
                    opportunities_version=runtime.store.opportunities_version))
        assert len(caplog.records) == 1
        assert runtime.status()["error_counts"] == {"publish": 3}
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_cancelled_close_waits_for_worker_before_closing_connection(tmp_path):
    from test_shadow_performance_safety import wait_started
    runtime = ShadowRuntime(Settings(shadow_enabled=True, database_url="sqlite:///" + str(tmp_path / "close.db")),
                            SnapshotStore())
    await runtime.initialize(None)
    empty_baseline(runtime.ledger)
    entered, release = threading.Event(), threading.Event()
    events = []
    save, close = runtime.repo.save_batch, runtime.db.close

    async def slow_save(rows):
        entered.set()
        assert release.wait(5)
        await save(rows)
        events.append("saved")

    async def recorded_close():
        events.append("closed")
        await close()

    runtime.repo.save_batch = slow_save
    runtime.db.close = recorded_close
    markets, op = pair()
    try:
        await runtime.submit(PublishedSnapshot(1, NOW, tuple(markets), (op,),
            opportunities_version=runtime.store.opportunities_version))
        await wait_started(entered)
        closing = asyncio.create_task(runtime.aclose())
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        assert "closed" not in events
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert events[-1] == "closed" and "saved" in events[:-1]
        assert runtime._active.done()
        await runtime.aclose()  # Idempotent; never writes to the closed connection.
        assert events.count("closed") == 1
        assert runtime.status()["errors"] == 0
    finally:
        release.set()
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["initialize", "consumer", "worker"])
async def test_runtime_error_paths_use_independent_category_limits(monkeypatch, caplog, category):
    import shadow_worker_harness as module
    runtime = ShadowRuntime(Settings(shadow_enabled=True, database_url="sqlite:///:memory:"), SnapshotStore())
    db = await connect_database(":memory:")

    async def broken_async(*args):
        raise RuntimeError("synthetic " + category)

    def broken_sync(*args):
        raise RuntimeError("synthetic " + category)

    try:
        if category == "initialize":
            monkeypatch.setattr(module, "initialize_shadow_schema", broken_async)
        else:
            await runtime.initialize(db)
            if category == "consumer":
                monkeypatch.setattr(runtime.repo, "prune", broken_async)
            else:
                monkeypatch.setattr(runtime, "_calculate", broken_sync)
        with caplog.at_level(logging.ERROR):
            for _ in range(3):
                if category == "initialize":
                    await runtime.initialize(db)
                else:
                    await runtime.consume(PublishedSnapshot(1, NOW, (), (),
                        opportunities_version=runtime.store.opportunities_version))
        assert len(caplog.records) == 1
        assert runtime.status()["error_counts"] == {category: 3}
        # A different category is not suppressed by the first category's window.
        with caplog.at_level(logging.ERROR):
            runtime.error("other")
        assert len(caplog.records) == 2
        assert runtime.status()["errors"] == 4
    finally:
        await runtime.aclose()
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("category,endpoint", [("read_list_trades", "trades"),
                                             ("read_closed_events", "buckets"),
                                             ("statistics", "buckets")])
async def test_api_errors_are_rate_limited_and_visible_in_status(tmp_path, monkeypatch, caplog, category, endpoint):
    import app.api.routes_shadow as routes
    app = create_app(settings=Settings(shadow_enabled=True, database_url="sqlite:///" + str(tmp_path / "errors.db"),
                                     dashboard_password="test-only", dashboard_totp_secret=TEST_TOTP_SECRET),
                     start_background_workers=False)
    app.state.auth_store = IsolatedSessionLookup()
    runtime = app.state.shadow_runtime
    await runtime.initialize(None)

    async def broken_async(*args):
        raise RuntimeError("synthetic read failure")

    def broken_sync(*args):
        raise RuntimeError("synthetic statistics failure")

    if category == "statistics":
        monkeypatch.setattr(runtime, "statistics", broken_async)
    else:
        monkeypatch.setattr(routes.ShadowTradeRepository, category.removeprefix("read_"), broken_async)
    try:
        with caplog.at_level(logging.ERROR):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                         headers=session_headers("test-password")) as client:
                for _ in range(3):
                    assert (await client.get("/api/shadow/" + endpoint)).status_code == 503
                state = (await client.get("/api/shadow/status")).json()
        assert len(caplog.records) == 1
        assert state["error_counts"] == {category: 3}
        assert state["errors"] == 3
    finally:
        await runtime.aclose()


@pytest.fixture(autouse=True)
def child_core_runtime_for_injection(monkeypatch):
    """These tests inject failures into the child core, not the IPC transport."""
    import app.main
    monkeypatch.setattr(app.main, "ShadowRuntime", ShadowRuntime)
