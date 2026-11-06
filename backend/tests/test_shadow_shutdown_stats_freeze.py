"""PM 42-45: real spawn, bounded shutdown, database statistics and frozen input."""
import asyncio
import os
import signal
import sys
from datetime import timedelta
from time import perf_counter

import pytest

from shadow_helpers import NOW, market
from test_shadow_process import eventually, fresh_pair, rows, runtime_for, tick


def locked_child(config, incoming, outgoing, stop):
    # Hold the exact old notification lock, or an IPC read lock in the new
    # protocol. A killed peer must never leave shutdown acquiring either lock.
    lock = stop._cond if stop is not None else incoming._rlock
    with lock:
        outgoing.put(("ready", 0, None, {"ready": True}))
        import time
        while True:
            time.sleep(.1)


async def measured_close(rt):
    gaps = []
    done = False
    async def heartbeat():
        previous = perf_counter()
        while not done:
            await asyncio.sleep(.005)
            current = perf_counter()
            gaps.append(current - previous)
            previous = current
    beat = asyncio.create_task(heartbeat())
    started = perf_counter()
    try:
        await rt.aclose()
    finally:
        done = True
        await beat
    assert perf_counter() - started < 15
    assert gaps and max(gaps) < .25
    assert rt._process is None and rt._incoming is None and rt._stop is None


@pytest.mark.asyncio
@pytest.mark.parametrize("during_backoff", [False, True])
async def test_killed_child_holding_notification_lock_closes(tmp_path, monkeypatch, during_backoff):
    import app.services.shadow_runtime as module
    monkeypatch.setattr(module, "child_entry", locked_child)
    rt = runtime_for(tmp_path)
    await rt.initialize()
    if sys.platform == "win32":
        rt._process.terminate()
    else:
        os.kill(rt._process.pid, signal.SIGKILL)
    if during_backoff:
        await eventually(lambda: rt._restart_at is not None)
    await measured_close(rt)


@pytest.mark.asyncio
async def test_stopped_child_close_is_bounded_and_does_not_block_loop(tmp_path):
    rt = runtime_for(tmp_path)
    await rt.initialize()
    process = rt._process
    if sys.platform == "win32":
        # Windows has no SIGSTOP. Suspend all child threads via the native
        # process API; POSIX runs the actual SIGSTOP case without a skip.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        suspend = ctypes.WinDLL("ntdll").NtSuspendProcess
        suspend.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x0800, False, process.pid)
        assert handle
        try:
            assert suspend(handle) == 0
        finally:
            kernel.CloseHandle(handle)
    else:
        os.kill(process.pid, signal.SIGSTOP)
    await measured_close(rt)
    assert rt.status()["error_counts"]["shutdown_timeout"] == 1


@pytest.mark.asyncio
async def test_terminate_failure_escalates_to_kill_with_threaded_joins(tmp_path, monkeypatch):
    import threading
    from test_shadow_process import hung_child
    import app.services.shadow_runtime as module
    monkeypatch.setattr(module, "child_entry", hung_child)
    rt = runtime_for(tmp_path)
    await rt.initialize()
    process = rt._process
    joins, kills = [], []
    join, kill = process.join, process.kill
    def checked_join(timeout):
        assert threading.current_thread() is not threading.main_thread()
        assert 0 <= timeout <= 2
        joins.append(timeout)
        return join(timeout)
    def checked_kill():
        kills.append(True)
        kill()
    monkeypatch.setattr(process, "join", checked_join)
    monkeypatch.setattr(process, "terminate", lambda: None)
    monkeypatch.setattr(process, "kill", checked_kill)
    await measured_close(rt)
    assert len(joins) == 2 and kills == [True]


@pytest.mark.asyncio
async def test_shutdown_supervisor_error_still_reaps_child(tmp_path):
    rt = runtime_for(tmp_path)
    await rt.initialize()
    rt._supervisor.cancel()
    await asyncio.gather(rt._supervisor, return_exceptions=True)
    async def broken():
        raise RuntimeError("injected supervisor failure")
    rt._supervisor = asyncio.create_task(broken())
    await measured_close(rt)
    assert rt.status()["error_counts"]["shutdown_wait"] == 1


@pytest.mark.asyncio
async def test_shutdown_deadline_also_bounds_executor_queueing(tmp_path, monkeypatch):
    import app.services.shadow_runtime as module
    rt = runtime_for(tmp_path)
    await rt.initialize()
    async def unavailable_executor(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(module.asyncio, "to_thread", unavailable_executor)
    await measured_close(rt)


@pytest.mark.asyncio
async def test_lifespan_shadow_close_error_does_not_skip_other_resources(tmp_path, monkeypatch):
    from app.main import create_app
    from app.core.config import Settings
    app = create_app(settings=Settings(database_url="sqlite:///" + str(tmp_path / "lifespan.db")),
                     start_background_workers=False)
    closed = []
    async def broken():
        raise RuntimeError("injected aclose failure")
    async def remaining():
        closed.append(True)
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.shadow_runtime, "aclose", broken)
        monkeypatch.setattr(app.state.market_validation, "aclose", remaining)
    assert closed == [True]
    assert app.state.shadow_runtime.status()["error_counts"]["shutdown_lifespan"] == 1


@pytest.mark.asyncio
async def test_real_spawn_all_shards_use_pre_yield_prices(tmp_path, monkeypatch):
    rt = runtime_for(tmp_path)
    try:
        await rt.initialize()
        await tick(rt, NOW - timedelta(minutes=1))
        quotes, op = fresh_pair(NOW)
        background = [market(symbol=f"BG{i}", raw_symbol=f"BG{i}") for i in range(1201)]
        original = rt._yield_capture
        changed = False
        async def mutate():
            nonlocal changed
            if not changed:
                changed = True
                quotes[1].bid = quotes[1].ask = 999
                quotes[1].estimated_fields.append("bid")
                op.open_spread_pct = -1
                op.buy_routes.append("mutated")
            await original()
        monkeypatch.setattr(rt, "_yield_capture", mutate)
        await tick(rt, NOW, background + quotes, [op])
        assert changed and rt.errors == 0
        records = await rows(rt)
        assert len(records) == 2
        assert all(r["fake_flag"] is False for r in records)
        assert rt.status()["scorer_price_states"] == 1203
        assert rt._quotes[1202][8:10] == (101, 101)
        assert rt._quotes[1202][-1] == ()
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_statistics_large_history_stays_in_child_and_bounds_response(tmp_path, monkeypatch):
    import json
    import sqlite3
    import threading
    import app.api.routes_shadow as routes
    from starlette.requests import Request
    from types import SimpleNamespace
    from app.services.shadow_ledger import STRATEGY_VERSION
    from app.services.shadow_stats import buckets
    rt = runtime_for(tmp_path)
    try:
        await rt.initialize()
        quotes, op = fresh_pair(NOW)
        await tick(rt, NOW)
        await tick(rt, NOW, quotes, [op])
        for quote in quotes:
            quote.bid = quote.ask = 100
        await tick(rt, NOW, quotes)
        seed = (await rows(rt))[0]
        def insert():
            with sqlite3.connect(rt.settings.sqlite_path) as db:
                db.executemany("INSERT INTO shadow_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ((f"bulk{i}", seed["opportunity_id"], seed["variant"], seed["symbol"],
                      seed["opened_at"], seed["closed_at"], 0,
                      json.dumps({**seed, "id": f"bulk{i}"})) for i in range(100000)))
        await asyncio.to_thread(insert)
        def forbidden(*args):
            pytest.fail("API must not read history in the parent")
        monkeypatch.setattr(routes, "read_committed", forbidden)
        calls = []
        encode = rt._encode
        def encoding(kind, data):
            result = encode(kind, data)
            if kind == "statistics":
                calls.append((len(result), threading.get_ident()))
            return result
        monkeypatch.setattr(rt, "_encode", encoding)
        request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(shadow_runtime=rt))})
        gaps = []
        async def query():
            return await routes.get_buckets(request, "variant,pair", NOW, NOW + timedelta(seconds=1), False, STRATEGY_VERSION)
        task = asyncio.create_task(query())
        previous = perf_counter()
        while not task.done():
            await asyncio.sleep(.005)
            current = perf_counter()
            gaps.append(current - previous)
            previous = current
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            await task
        assert error.value.status_code == 503  # never silently truncate a curve
        assert calls and calls[0][0] < 1024
        assert max(gaps) < .1
        assert rt.status()["ready"] and rt.child_restarts == 0
        # Small responses preserve every API field, exact curve and statistics.
        def remove_bulk():
            with sqlite3.connect(rt.settings.sqlite_path) as db:
                db.execute("DELETE FROM shadow_trades WHERE id LIKE 'bulk%'")
        await asyncio.to_thread(remove_bulk)
        result = await query()
        expected = sorted(await rows(rt), key=lambda r: (r["closed_at"], r["id"]))
        assert result == buckets(expected, ["variant", "pair"])
    finally:
        await rt.aclose()
