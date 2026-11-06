"""Real spawn integration; core fault injection remains in shadow_worker_harness."""
import asyncio
import os
import sqlite3
import sys
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.db.repositories import ShadowTradeRepository
from app.db.schema import initialize_shadow_schema
from app.services.shadow_runtime import ShadowRuntime
from app.services.snapshot_consumers import PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from shadow_helpers import pair


def checked_child(config, incoming, outgoing, stop):
    from app.services.shadow_worker import run_child
    assert "app.main" not in sys.modules
    assert not any(name.startswith("app.collectors") for name in sys.modules)
    assert "fastapi" not in sys.modules
    run_child(config, incoming, outgoing, stop)


def hung_child(config, incoming, outgoing, stop):
    import time
    outgoing.put(("ready", 0, None, {"ready": True}))
    while True:
        time.sleep(.1)


def delayed_child(config, incoming, outgoing, stop):
    import time
    from app.services.shadow_worker import ShadowWorker
    original = ShadowWorker._process
    async def delayed(self, *args, **kwargs):
        time.sleep(.3)
        return await original(self, *args, **kwargs)
    ShadowWorker._process = delayed
    checked_child(config, incoming, outgoing, stop)


async def eventually(predicate, timeout=10):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.02)


def runtime_for(tmp_path, **kwargs):
    return ShadowRuntime(Settings(shadow_enabled=True,
        database_url="sqlite:///" + str(tmp_path / "shadow.db"), **kwargs), SnapshotStore())


async def tick(runtime, now, markets=(), ops=()):
    runtime.store.mark_published(now)
    await runtime.consume(PublishedSnapshot(runtime.store.generation, now, tuple(markets), tuple(ops),
        opportunities_version=runtime.store.opportunities_version))


async def rows(runtime):
    from pathlib import Path
    import json
    uri = Path(runtime.settings.sqlite_path).as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=.1) as db:
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM shadow_trades")]


def fresh_pair(now):
    quotes, op = pair()
    for quote in quotes:
        quote.timestamp = now
    return quotes, op


@pytest.mark.asyncio
async def test_spawn_lifecycle_minimal_imports_and_graceful_flush(tmp_path, monkeypatch):
    import app.services.shadow_runtime as module
    monkeypatch.setattr(module, "child_entry", checked_child)
    rt = runtime_for(tmp_path)
    try:
        await rt.initialize()
        assert rt.status()["ready"] and rt.status()["worker_pid"] != os.getpid()
        assert not hasattr(rt, "ledger") and not hasattr(rt, "repo")
        now = datetime.now(UTC)
        quotes, op = fresh_pair(now)
        await tick(rt, now)
        await tick(rt, now, quotes, [op])
        assert rt.status()["open_count"] == 2
        assert rt.status()["main_cpu_ms"] >= 0 and rt.status()["child_rss_mb"] > 0
        assert rt.status()["errors"] == 0
        await rt.aclose()
        assert rt._process is None and rt._reader is None
        assert len(await rows(rt)) == 2
        await rt.aclose()
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_disabled_has_no_process_thread_or_writes(tmp_path, monkeypatch):
    import app.services.shadow_runtime as module
    rt = ShadowRuntime(Settings(shadow_enabled=False), SnapshotStore())
    def forbidden(*args, **kwargs):
        pytest.fail("disabled shadow must allocate no process or thread")
    monkeypatch.setattr(module.multiprocessing, "get_context", forbidden)
    monkeypatch.setattr(module.threading, "Thread", forbidden)
    await rt.initialize()
    await tick(rt, datetime.now(UTC))
    await rt.aclose()
    assert rt._process is None and rt._reader is None and rt._supervisor is None
    assert rt.status()["enabled"] is False and rt.status()["skipped_rounds"] == 0


@pytest.mark.asyncio
async def test_child_exception_is_reported_and_next_round_recovers(tmp_path, monkeypatch):
    rt = runtime_for(tmp_path)
    try:
        await rt.initialize()
        pid = rt.status()["worker_pid"]
        original = rt._encode
        monkeypatch.setattr(rt, "_encode", lambda name, data: b"invalid pickle")
        now = datetime.now(UTC)
        await tick(rt, now)
        assert rt.status()["error_counts"] == {"worker": 1}
        assert rt.status()["worker_pid"] == pid and rt.child_restarts == 0
        monkeypatch.setattr(rt, "_encode", original)
        await tick(rt, now)
        quotes, op = fresh_pair(now)
        await tick(rt, now, quotes, [op])
        assert rt.status()["open_count"] == 2 and rt.errors == 1
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_capture_reply_exception_cannot_escape_or_leave_child_waiting(tmp_path, monkeypatch):
    import app.services.shadow_runtime as module
    rt = runtime_for(tmp_path)
    try:
        await rt.initialize()
        def broken(*args):
            raise ValueError("synthetic directory projection failure")
        monkeypatch.setattr(module, "directory_values", broken)
        now = datetime.now(UTC)
        quotes, op = fresh_pair(now)
        async with asyncio.timeout(10):
            await tick(rt, now, quotes, [op])
        assert rt.status()["error_counts"] == {"capture": 1, "transport": 1}
        assert rt._process is None and not rt.status()["ready"]
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_busy_skips_and_cancel_does_not_release_slot(tmp_path, monkeypatch):
    import app.services.shadow_runtime as module
    monkeypatch.setattr(module, "child_entry", delayed_child)
    rt = runtime_for(tmp_path)
    try:
        await rt.initialize()
        now = datetime.now(UTC)
        task = asyncio.create_task(tick(rt, now))
        await asyncio.sleep(.05)
        assert rt._active is not None and not rt._active.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await tick(rt, now)
        assert rt.status()["skipped_rounds"] == 1
        beats = 0
        while not rt._active.done():
            beats += 1
            await asyncio.sleep(.005)
        assert beats > 10
        assert rt.status()["errors"] == 0
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_crash_backoff_three_restarts_then_permanent_error(tmp_path, monkeypatch):
    import app.services.shadow_runtime as module
    assert module.RESTART_BASE_SECONDS == 5 and module.MAX_RESTARTS == 3
    monkeypatch.setattr(module, "RESTART_BASE_SECONDS", .1)
    rt = runtime_for(tmp_path)
    try:
        await rt.initialize()
        for count in range(4):
            pid = rt.status()["worker_pid"]
            rt._process.terminate()
            await eventually(lambda: rt._restart_at is not None or rt.status()["disabled"])
            if count == 3:
                break
            remaining = rt._restart_at - module.monotonic()
            assert remaining > 0
            assert remaining <= .1 * 2 ** count + 1e-6
            await eventually(lambda: rt.status()["ready"] and rt.status()["worker_pid"] != pid)
            assert rt.child_restarts == count + 1
        assert rt.status()["disabled"] and not rt.status()["ready"]
        assert rt.status()["error"] == "crash" and rt.child_restarts == 3
        await tick(rt, datetime.now(UTC))
        await eventually(lambda: rt._process is None)
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_rss_limit_disables_without_restart(tmp_path):
    rt = runtime_for(tmp_path, shadow_child_max_rss_mb=.1)
    try:
        await rt.initialize()
        await eventually(lambda: rt.status()["disabled"])
        assert rt.status()["error"] == "rss_limit"
        assert rt.child_restarts == 0 and not rt.status()["ready"]
        await eventually(lambda: rt._process is None)
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_timeout_and_shutdown_terminate_hung_child(tmp_path, monkeypatch):
    import app.services.shadow_runtime as module
    monkeypatch.setattr(module, "child_entry", hung_child)
    monkeypatch.setattr(module, "SHUTDOWN_SECONDS", .2)
    rt = runtime_for(tmp_path)
    try:
        await rt.initialize()
        monkeypatch.setattr(module, "WORK_TIMEOUT_SECONDS", .3)
        await tick(rt, datetime.now(UTC))
        assert rt.status()["error"] == "timeout"
        assert rt.status()["error_counts"]["timeout"] == 1
    finally:
        await rt.aclose()
    monkeypatch.setattr(module, "WORK_TIMEOUT_SECONDS", 60)
    rt = runtime_for(tmp_path)
    await rt.initialize()
    await rt.aclose()
    assert rt._process is None and rt.status()["error_counts"]["shutdown_timeout"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("volume,minimum,opened", [(None, 0, 2), (None, 1, 0), (99, 100, 0), (100, 100, 2)])
async def test_liquidity_filter_in_real_worker(tmp_path, volume, minimum, opened):
    rt = runtime_for(tmp_path, shadow_min_liquidity_usd=minimum)
    try:
        await rt.initialize()
        now = datetime.now(UTC)
        quotes, op = fresh_pair(now)
        op.buy_volume_24h_usdt, op.sell_volume_24h_usdt = volume, 1000
        await tick(rt, now)
        for _ in range(2):
            await tick(rt, now, quotes, [op])
        assert rt.status()["open_count"] == opened
        assert rt.status()["dropped_by_reason"]["liquidity"] == int(not opened)
        assert rt.status()["dropped"] == int(not opened)
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_restart_restores_positions_cooldown_and_excludes_retired(tmp_path, monkeypatch):
    import app.services.shadow_runtime as module
    monkeypatch.setattr(module, "RESTART_BASE_SECONDS", .01)
    from app.services.shadow_ledger import ShadowLedger
    from shadow_helpers import empty_baseline
    now = datetime.now(UTC)
    rt = runtime_for(tmp_path)
    quotes, op = fresh_pair(now)
    book = ShadowLedger(rt.settings)
    empty_baseline(book)
    book.process(quotes, [op], now)
    seed = list(book.pending.values())
    db = await connect_database(rt.settings.sqlite_path)
    await initialize_shadow_schema(db)
    repo = ShadowTradeRepository(db)
    await repo.save_batch(seed)
    closed = {**seed[0], "id": "closed", "opportunity_id": "cooling", "closed_at": now.isoformat(), "exit_reason": "converged"}
    retired = {**seed[0], "id": "legacy", "opportunity_id": "retired", "strategy_version": "shadow-v1"}
    await repo.save_batch([closed, retired])
    await db.close()
    try:
        await rt.initialize()
        assert rt.status()["open_count"] == 2 and op.id in rt._held[0]
        assert "retired" not in rt._held[0]
        assert next(r for r in await rows(rt) if r["id"] == "legacy")["exit_reason"] == "retired"
        pid = rt.status()["worker_pid"]
        rt._process.terminate()
        await eventually(lambda: rt.status()["ready"] and rt.status()["worker_pid"] != pid)
        assert rt.child_restarts == 1 and rt.status()["open_count"] == 2
        await rt.aclose()
        rt = ShadowRuntime(rt.settings, SnapshotStore())
        await rt.initialize()
        assert rt.status()["open_count"] == 2
        await tick(rt, now)
        cooling = op.model_copy(update={"id": "cooling"})
        new = op.model_copy(update={"id": "retired"})
        await tick(rt, now, quotes, [cooling, new])
        assert rt.status()["open_count"] == 4
        assert "retired" in rt._held[0] and "cooling" not in rt._held[0]
        assert rt.status()["errors"] == 0
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_close_drains_accepted_round_and_cancellation_waits_for_child(tmp_path, monkeypatch):
    import app.services.shadow_runtime as module
    monkeypatch.setattr(module, "child_entry", delayed_child)
    rt = runtime_for(tmp_path)
    try:
        await rt.initialize()
        now = datetime.now(UTC)
        quotes, op = fresh_pair(now)
        await tick(rt, now)
        rt.store.mark_published(now)
        task = await rt.submit(PublishedSnapshot(rt.store.generation, now, tuple(quotes), (op,),
            opportunities_version=rt.store.opportunities_version))
        reader = rt._reader
        closing = asyncio.create_task(rt.aclose())
        await asyncio.sleep(.05)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert task.done() and not reader.is_alive()
        assert len(await rows(rt)) == 2 and rt.status()["pending_writes"] == 0
        assert rt.status()["errors"] == 0
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_real_api_wal_reads_and_child_statistics(tmp_path, monkeypatch):
    import httpx
    from app.main import create_app
    from auth_helpers import IsolatedSessionLookup, TEST_TOTP_SECRET, session_headers
    from datetime import timedelta
    import app.api.routes_shadow as routes
    app = create_app(settings=Settings(shadow_enabled=True,
        database_url="sqlite:///" + str(tmp_path / "api.db"), dashboard_password="test-only",
        dashboard_totp_secret=TEST_TOTP_SECRET), start_background_workers=False)
    app.state.auth_store = IsolatedSessionLookup()
    rt = app.state.shadow_runtime
    shared = await connect_database(rt.settings.sqlite_path)
    trace = []
    await shared.set_trace_callback(trace.append)
    try:
        await rt.initialize(shared)
        now = datetime.now(UTC)
        quotes, op = fresh_pair(now)
        await tick(rt, now)
        await tick(rt, now, quotes, [op])
        for quote in quotes:
            quote.bid = quote.ask = 100
        await tick(rt, now, quotes)
        def forbidden(*args):
            pytest.fail("bootstrap must run in the child, not an API thread")
        monkeypatch.setattr(routes, "buckets", forbidden)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                    headers=session_headers("test-password")) as client:
            trades = await client.get("/api/shadow/trades?status=closed")
            buckets = await client.get("/api/shadow/buckets", params={
                "since": (now - timedelta(seconds=1)).isoformat(), "until": now.isoformat()})
            assert trades.status_code == buckets.status_code == 200
            assert len(trades.json()) == 2
            assert sum(b["n_evt"] for b in buckets.json()) == 2
            assert (await client.get("/api/shadow/status")).json()["worker_pid"] != os.getpid()
        assert not any("shadow_trades" in sql.lower() and sql.lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE")) for sql in trace)
        assert rt.errors == 0
    finally:
        await rt.aclose()
        await shared.close()


@pytest.mark.asyncio
async def test_real_ipc_matches_full_ledger_across_crossings_capacity_and_stale(tmp_path):
    import json
    from datetime import timedelta
    from app.services.shadow_ledger import ShadowLedger
    from app.services.fake_opportunity import FakeOpportunityScorer
    from app.services.execution_routes import ExecutionRoutes
    from app.services.spread_engine import build_directional_opportunity
    from shadow_helpers import market
    rt = runtime_for(tmp_path, shadow_max_open=6)
    full, scorer = ShadowLedger(rt.settings), FakeOpportunityScorer()
    catalog = ExecutionRoutes(rt.settings.native_route_exchanges)
    reference = await connect_database(str(tmp_path / "reference.db"))
    await initialize_shadow_schema(reference)
    repo = ShadowTradeRepository(reference)
    start = datetime.now(UTC)
    def normalized(values):
        return sorted(json.dumps({k: v for k, v in r.items() if k not in {"id", "tracking"}},
                                 sort_keys=True) for r in values)
    try:
        await rt.initialize()
        # Low-price reversals span a minute: intraminute reversals of unrelated
        # assets are the explicit PM 10 sampling exemption, tested in the core suite.
        for second in (0, 1, 2, 10, 61, 120, 600, 662, 723, 1200):
            now = start + timedelta(seconds=second)
            quotes, opportunities = [], []
            for i in range(4):
                sell = 100 if second in (0, 61, 662) else 101
                legs = [market(ex, price, symbol=f"C{i}USDT", raw_symbol=f"C{i}USDT", timestamp=now)
                        for ex, price in (("binance", 100), ("bitget", sell))]
                opportunities.append(build_directional_opportunity(*legs, mode="FF"))
                quotes.extend(legs if second not in (120, 600, 723) or i else legs[:1])
            if second == 10:
                opportunities = []
            annotations = scorer.score(quotes, opportunities, now)
            for op in opportunities:
                annotations[op.id].update(catalog.fields(op))
            full.process(quotes, opportunities, now, annotations)
            await full.flush(repo)
            await tick(rt, now, quotes, opportunities)
            assert rt.errors == 0
            assert rt.status()["open_count"] == full.open_count
            assert rt.status()["dropped_by_reason"] == full.dropped_by_reason
            assert rt.status()["baseline_skipped"] == full.baseline_skipped
            assert normalized(await rows(rt)) == normalized(await repo.list_trades(limit=500))
    finally:
        await rt.aclose()
        await reference.close()


@pytest.mark.asyncio
async def test_version_guard_mixed_publication_and_publish_failure(tmp_path, monkeypatch):
    rt = runtime_for(tmp_path)
    try:
        await rt.initialize()
        now = datetime.now(UTC)
        quotes, op = fresh_pair(now)
        await tick(rt, now)
        low = op.model_copy(update={"id": "low", "open_spread_pct": .1})
        rt.store.mark_published(now)
        snapshot = PublishedSnapshot(rt.store.generation, now, tuple(quotes), (low, op),
                                     opportunities_version=rt.store.opportunities_version)
        task = await rt.submit(snapshot)
        rt.store.set_opportunities([])
        await task
        assert rt.store.get_opportunities() == [] and rt.status()["skipped_publications"] == 1
        assert rt.status()["open_count"] == 2
        await tick(rt, now, quotes, [low, op])
        assert rt.store.get_opportunities()[0] is low
        assert rt.store.get_opportunities()[1].fake_score == 0 and op.fake_score is None
        def broken(*args):
            raise RuntimeError("publish fault")
        monkeypatch.setattr(rt.store, "set_opportunities", broken)
        for quote in quotes:
            quote.bid = quote.ask = 100
        await tick(rt, now, quotes)
        assert rt.status()["open_count"] == 0 and rt._held == (frozenset(), frozenset())
        assert all(r["exit_reason"] == "converged" for r in await rows(rt))
        assert rt.status()["error_counts"]["publish"] == 1
    finally:
        await rt.aclose()
