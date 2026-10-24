import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.main import _run_alert_loop, create_app
from app.runtime.supervisor import EventLoopLag, StageTimings, TaskSupervisor, beat, safe_error
from app.services.announcement_polling import run_announcement_sources
from app.services.announcements import run_announcement_enrichment_loop
from app.services.collector import MarketCollector, run_collector_loop
from app.services.fs_monitor import FsMonitor
from app.services.snapshot_store import SnapshotStore


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_failure_is_visible_then_restarts_and_cancellation_is_clean(caplog):
    supervisor = TaskSupervisor(initial_backoff=0.02, max_backoff=0.04)
    calls = 0

    async def worker():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("api_key=super-secret https://example.test/hook/private")
        beat("negative-basis-monitor", ok=True)
        await asyncio.Event().wait()

    task = supervisor.start("negative-basis-monitor", worker, restart=True, critical=False)
    try:
        await wait_until(lambda: supervisor.statuses["negative-basis-monitor"].state == "failed")
        status = supervisor.snapshot()["negative-basis-monitor"]
        assert "RuntimeError" in status["last_error"]
        assert "super-secret" not in str(status) + caplog.text
        assert "private" not in str(status) + caplog.text
        assert len(status["last_error"]) <= 500
        assert status["backoff_seconds"] == 0.02
        await wait_until(lambda: calls == 2)
        status = supervisor.snapshot()["negative-basis-monitor"]
        assert status["state"] == "running"
        assert status["restarts"] == 1
        assert status["iterations"] == 1
        assert status["last_error"] is not None  # Evidence survives recovery.
    finally:
        await supervisor.stop()
    assert task.cancelled()
    assert supervisor.snapshot()["negative-basis-monitor"]["state"] == "stopped"


async def test_backoff_doubles_to_cap_without_restart_storm(monkeypatch):
    supervisor = TaskSupervisor()
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    async def worker():
        if len(sleeps) >= 16:
            return
        raise RuntimeError("boom")

    monkeypatch.setattr("app.runtime.supervisor.asyncio.sleep", sleep)
    await supervisor.start("crash", worker, restart=True, critical=False)
    assert [sum(sleeps[i:i + 2]) for i in range(0, 16, 2)] == [5, 10, 20, 40, 80, 160, 300, 300]
    assert supervisor.statuses["crash"].restarts == 8
    await supervisor.stop()


async def test_suspended_iteration_ages_without_fabricated_heartbeats():
    now = datetime(2026, 10, 23, tzinfo=UTC)
    supervisor = TaskSupervisor(now_fn=lambda: now)

    async def worker():
        beat("hung", ok=True)
        await asyncio.Event().wait()

    supervisor.start("hung", worker, restart=True, critical=True)
    try:
        await wait_until(lambda: supervisor.statuses["hung"].iterations == 1)
        last_ok = supervisor.statuses["hung"].last_ok_at
        now += timedelta(seconds=600)
        status = supervisor.snapshot()["hung"]
        assert status["state"] == "running"
        assert status["iteration_age_seconds"] == 600
        assert status["last_ok_at"] == last_ok
        supervisor.beat("hung", ok=False)
        assert supervisor.statuses["hung"].last_ok_at == last_ok
        assert supervisor.statuses["hung"].iterations == 2
    finally:
        await supervisor.stop()


async def test_non_restart_failure_and_cancel_during_backoff():
    supervisor = TaskSupervisor()

    async def fail():
        raise ValueError("secret")

    await supervisor.start("terminal", fail, restart=False, critical=False)
    supervisor.start("retry", fail, restart=True, critical=False)
    await wait_until(lambda: supervisor.statuses["retry"].state == "failed")
    await supervisor.stop()
    assert supervisor.statuses["terminal"].state == "failed"
    assert supervisor.statuses["retry"].state == "stopped"
    assert supervisor.statuses["retry"].restarts == 0


async def test_shutdown_failed_task_still_closes_clients_and_database(tmp_path, caplog):
    app = create_app(
        settings=Settings(database_url=f"sqlite:///{tmp_path / 'shutdown.db'}"),
        start_background_workers=False,
    )
    closed = AsyncMock()

    async def fail():
        raise RuntimeError("credential-must-not-leak")

    async def fail_on_cancel():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise ValueError("credential-must-not-leak") from None

    async with app.router.lifespan_context(app):
        supervisor = app.state.task_supervisor
        # Inject an already-failed task, including the legacy unwrapped failure case.
        task = asyncio.create_task(fail(), name="already-failed")
        supervisor.tasks["already-failed"] = task
        supervisor.tasks["cancel-failure"] = asyncio.create_task(fail_on_cancel())
        await asyncio.sleep(0)
        assert task.done()
        monkey_resource = app.state.service_controller
        original_close = monkey_resource.aclose

        async def close():
            await original_close()
            await closed()

        monkey_resource.aclose = close
        db = app.state.db
    closed.assert_awaited_once()
    with pytest.raises(ValueError, match="no active connection"):
        await db.execute("SELECT 1")
    assert "already-failed failed during shutdown" in caplog.text
    assert "cancel-failure failed during shutdown" in caplog.text
    assert "credential-must-not-leak" not in caplog.text


def test_error_summary_is_bounded_even_with_long_exception_type():
    error_type = type("E" * 600, (RuntimeError,), {})
    assert len(safe_error(error_type("password"))) == 500


async def test_event_loop_lag_measures_delay_and_prunes_old_samples(monkeypatch):
    now = 0.0
    lag = EventLoopLag(clock=lambda: now)

    async def sleep(seconds):
        nonlocal now
        assert seconds == 1
        if now >= 2.5:
            raise asyncio.CancelledError
        now += 1.25

    monkeypatch.setattr("app.runtime.supervisor.asyncio.sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await lag.run()
    assert lag.snapshot() == {
        "unit": "seconds", "window_seconds": 300, "samples": 2,
        "p50": 0.25, "p95": 0.25, "max": 0.25,
    }
    now = 303
    assert lag.snapshot()["samples"] == 0


async def test_collector_generation_stage_timings_and_publish_before_history_failure():
    now = datetime.now(UTC)
    ticks = iter(range(100))
    store = SnapshotStore()
    adapter = SimpleNamespace(
        name="fake", fetch_spot_tickers=AsyncMock(return_value=[]),
        fetch_future_tickers=AsyncMock(return_value=[]),
    )
    history = SimpleNamespace(record=AsyncMock())
    collector = MarketCollector([adapter], store, history_recorder=history, now_fn=lambda: now)
    collector.stage_timings = StageTimings(2, clock=lambda: next(ticks))
    for _ in range(3):
        await collector.collect_once()
    assert store.generation == 3
    assert store.published_at == now
    metrics = collector.stage_timings.snapshot()
    assert metrics["rounds"] == 2
    assert set(metrics["stages"]) == {"poll", "prepare", "build", "publish", "history", "index"}
    assert all(value == {"samples": 2, "p50": 1, "p95": 1, "max": 1}
               for value in metrics["stages"].values())
    history.record.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        await collector.collect_once()
    assert store.generation == 4
    assert collector.stage_timings.snapshot()["stages"]["history"]["samples"] == 2
    assert collector.stage_timings.snapshot()["stages"]["index"]["samples"] == 1


async def test_empty_publication_and_existing_store_setters_are_unchanged():
    store = SnapshotStore()
    assert store.set_markets([]) is None
    assert store.set_all_markets([]) is None
    assert store.set_opportunities([]) is None
    assert store.set_exchange_errors({}) is None
    assert store.generation == 0
    assert store.published_at is None
    await MarketCollector([], store).collect_once()
    assert store.generation == 1
    assert store.published_at is not None


@pytest.mark.parametrize("loop_name", ["market-collector", "alert-loop", "announcement-enrichment-loop"])
@pytest.mark.parametrize("ok", [True, False])
async def test_loop_heartbeats_on_completed_and_failed_iterations(loop_name, ok):
    stop = asyncio.Event()
    supervisor = TaskSupervisor()

    async def operation():
        stop.set()
        if not ok:
            raise RuntimeError("offline injected failure")
        return []

    if loop_name == "market-collector":
        factory = lambda: run_collector_loop(SimpleNamespace(collect_once=operation), 0.01, stop)
    elif loop_name == "alert-loop":
        app = create_app(start_background_workers=False, settings=Settings())
        app.state.alert_rule_repo = SimpleNamespace(list=operation)
        app.state.alert_event_repo = SimpleNamespace()
        factory = lambda: _run_alert_loop(app, 0.01, stop)
    else:
        async def settings():
            await operation()
            return SimpleNamespace(enabled=False)
        factory = lambda: run_announcement_enrichment_loop(
            None, None, settings, stop, startup_delay_seconds=0.001,
        )
    await supervisor.start(loop_name, factory, restart=False, critical=False)
    status = supervisor.statuses[loop_name]
    assert status.iterations == 1
    assert (status.last_ok_at is not None) == ok
    await supervisor.stop()


@pytest.mark.parametrize("ok", [True, False])
async def test_fs_iteration_heartbeat(ok, monkeypatch):
    stop = asyncio.Event()
    supervisor = TaskSupervisor()

    async def settings():
        if not ok:
            stop.set()
            raise RuntimeError("offline")
        return SimpleNamespace(enabled=False, scan_interval_seconds=1)

    async def wait():
        stop.set()

    async def sleep(_):
        pass

    monitor = SimpleNamespace(
        repository=SimpleNamespace(settings=settings), report=SimpleNamespace(),
        wake=SimpleNamespace(clear=lambda: None, wait=wait),
    )
    monkeypatch.setattr("app.services.fs_monitor.asyncio.sleep", sleep)
    await supervisor.start("fs-monitor", lambda: FsMonitor.run(monitor, stop),
                           restart=False, critical=False)
    status = supervisor.statuses["fs-monitor"]
    assert status.iterations == 1
    assert (status.last_ok_at is not None) == ok


async def test_announcement_source_heartbeat_uses_supervisor_context():
    stop = asyncio.Event()
    supervisor = TaskSupervisor()

    async def settings():
        stop.set()
        return SimpleNamespace(enabled=False, poll_interval_seconds=1)

    await supervisor.start(
        "announcement-loop",
        lambda: run_announcement_sources([SimpleNamespace(exchange="fake")], None, settings, stop),
        restart=False, critical=False,
    )
    assert supervisor.statuses["announcement-loop"].iterations == 1
    assert supervisor.statuses["announcement-loop"].last_ok_at is not None
