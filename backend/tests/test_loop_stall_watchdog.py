import asyncio
import io
import json
import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.runtime import loop_stall_watchdog as module
from app.runtime.loop_stall_watchdog import LoopStallWatchdog


@pytest.fixture
def output(monkeypatch):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    monkeypatch.setattr(module.logger, "handlers", [handler])
    return stream


def records(output):
    return [json.loads(line) for line in output.getvalue().splitlines()
            if line.startswith('{"loop_stall":1,')]


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_stall_real_blocking_stack_and_single_compact_log(output):
    watchdog = LoopStallWatchdog()
    task = asyncio.create_task(watchdog.run())
    try:
        await wait_until(lambda: watchdog.thread is not None and watchdog.thread.is_alive())

        def blocking_function():
            time.sleep(1.5)

        blocking_function()
        await wait_until(lambda: records(output))
        await asyncio.sleep(0.3)
        rows = records(output)
        assert len(rows) == 1
        row = rows[0]
        assert row["duration_ms"] >= 1000
        assert 1 <= row["samples"] <= 16
        assert any("blocking_function" in frame
                   for group in row["top"] for frame in group["stack"])
        assert output.getvalue().strip() == json.dumps(row, separators=(",", ":"))
        assert watchdog.thread.daemon
        assert watchdog.loop_thread_id == threading.get_ident()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not watchdog.thread.is_alive()


async def test_watchdog_healthy_loop_produces_no_log(output):
    watchdog = LoopStallWatchdog(threshold_seconds=0.3)
    task = asyncio.create_task(watchdog.run())
    try:
        await asyncio.sleep(0.7)
        assert watchdog.thread.is_alive()
        assert output.getvalue() == ""
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("value", ["0", "false", "OFF", " no "])
async def test_watchdog_disabled_never_starts_thread(monkeypatch, value):
    monkeypatch.setenv("LOOP_STALL_WATCHDOG_ENABLED", value)
    start = Mock(side_effect=AssertionError("thread must not start"))
    monkeypatch.setattr(threading.Thread, "start", start)
    watchdog = LoopStallWatchdog()
    await watchdog.run()
    watchdog.stop()
    assert not watchdog.enabled
    assert watchdog.thread is None
    start.assert_not_called()


@pytest.mark.parametrize("value,expected", [
    (None, 1.0), ("2.5", 2.5), ("bad", 1.0), ("0", 1.0),
    ("-1", 1.0), ("nan", 1.0), ("inf", 1.0),
])
def test_watchdog_environment_defaults_and_threshold(monkeypatch, value, expected):
    monkeypatch.delenv("LOOP_STALL_WATCHDOG_ENABLED", raising=False)
    monkeypatch.delenv("LOOP_STALL_THRESHOLD_SECONDS", raising=False)
    if value is not None:
        monkeypatch.setenv("LOOP_STALL_THRESHOLD_SECONDS", value)
    watchdog = LoopStallWatchdog()
    assert watchdog.enabled
    assert watchdog.threshold_seconds == expected
    assert module.logger.level == logging.INFO
    assert module.logger.handlers
    assert module.logger.propagate is False


def fake_watchdog(monkeypatch):
    now = [0.0]
    watchdog = LoopStallWatchdog(clock=lambda: now[0])
    watchdog.loop_thread_id = threading.get_ident()
    watchdog._heartbeat = 0.0
    sample = Mock(return_value=("app/worker.py:12 blocked",))
    monkeypatch.setattr(watchdog, "_sample_stack", sample)
    return watchdog, now, sample


def test_stall_sample_interval_cap_top_groups_and_recovery(output, monkeypatch):
    watchdog, now, sample = fake_watchdog(monkeypatch)
    sample.side_effect = [(f"app/worker.py:{i % 4} blocked",) for i in range(16)]
    now[0] = 1.0
    watchdog._check()
    sample.assert_not_called()
    for i in range(100):
        now[0] = 1.01 + i * 0.125
        watchdog._check()
    assert sample.call_count == 16
    assert not records(output)  # A stall is reported only after recovery.
    watchdog._heartbeat = now[0] = 20.0
    watchdog._check()
    watchdog._check()
    row, = records(output)
    assert row["duration_ms"] == 20000
    assert row["samples"] == 16
    assert len(row["top"]) == 3
    assert [group["count"] for group in row["top"]] == [4, 4, 4]


def test_stall_rate_limit_is_rolling_ten_minutes(output, monkeypatch):
    watchdog, now, _ = fake_watchdog(monkeypatch)

    def stall(start):
        watchdog._heartbeat = start
        now[0] = start + 1.1
        watchdog._check()
        watchdog._heartbeat = now[0] = start + 1.5
        watchdog._check()

    for index in range(31):
        stall(index * 2.0)
    assert len(records(output)) == 30
    stall(599.0)  # First log at 1.5 has not yet expired.
    assert len(records(output)) == 30
    stall(600.1)
    assert len(records(output)) == 31


def test_stall_stack_keeps_app_and_innermost_three_external_frames(monkeypatch):
    frame = None
    for index in range(30):
        filename = (str(module.APP_ROOT / "worker.py") if index % 2 == 0
                    else f"/external/library{index}.py")
        frame = SimpleNamespace(
            f_back=frame, f_lineno=index,
            f_code=SimpleNamespace(co_filename=filename, co_name=f"function{index}"),
        )
    watchdog = LoopStallWatchdog()
    watchdog.loop_thread_id = 123
    frames = Mock(return_value={123: frame})
    monkeypatch.setattr(module.sys, "_current_frames", frames)
    stack = watchdog._sample_stack()
    assert len(stack) == 18
    assert len([line for line in stack if line.startswith("app/")]) == 15
    assert [line.rsplit(" ", 1)[1] for line in stack if not line.startswith("app/")] == [
        "function25", "function27", "function29",
    ]
    assert all(not line.startswith(("/", "C:")) for line in stack)
    frames.assert_called_once_with()

    for index in range(30, 60):
        frame = SimpleNamespace(
            f_back=frame, f_lineno=index,
            f_code=SimpleNamespace(co_filename=str(module.APP_ROOT / "deep.py"),
                                   co_name=f"function{index}"),
        )
    frames.return_value = {123: frame}
    stack = watchdog._sample_stack()
    assert len(stack) == 20
    assert stack[-1] == "app/deep.py:59 function59"


@pytest.mark.parametrize("failure", ["sample", "log", "warning"])
async def test_stall_thread_survives_failures_and_warns_only_once(monkeypatch, output, failure):
    watchdog = LoopStallWatchdog(threshold_seconds=0.15)
    warning = Mock(side_effect=RuntimeError("private") if failure == "warning" else None)
    monkeypatch.setattr(module.logger, "warning", warning)
    original_sample = watchdog._sample_stack
    original_info = module.logger.info
    if failure in {"sample", "warning"}:
        monkeypatch.setattr(watchdog, "_sample_stack", Mock(side_effect=RuntimeError("private")))
    else:
        monkeypatch.setattr(module.logger, "info", Mock(side_effect=RuntimeError("private")))
    task = asyncio.create_task(watchdog.run())
    try:
        await wait_until(lambda: watchdog.thread is not None)
        for _ in range(2):
            time.sleep(0.55)  # noqa: ASYNC251 - deliberately stall the loop to inject failures.
            await asyncio.sleep(0.25)
        assert watchdog.thread.is_alive()
        assert not task.done()
        assert warning.call_count == 1
        assert "private" not in str(warning.call_args)
        monkeypatch.setattr(watchdog, "_sample_stack", original_sample)
        monkeypatch.setattr(module.logger, "info", original_info)
        before = len(records(output))
        time.sleep(0.55)  # noqa: ASYNC251 - verify sampling resumes after a real loop stall.
        await wait_until(lambda: len(records(output)) > before)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not watchdog.thread.is_alive()


@pytest.mark.parametrize("enabled", [True, False])
async def test_main_lifespan_watchdog_shutdown(monkeypatch, enabled):
    from app.core.config import Settings
    from app.main import create_app

    monkeypatch.setenv("LOOP_STALL_WATCHDOG_ENABLED", str(enabled))
    app = create_app(settings=Settings(database_url="sqlite:///:memory:"),
                     start_background_workers=False)
    async with app.router.lifespan_context(app):
        watchdog = app.state.loop_stall_watchdog
        supervisor = app.state.task_supervisor
        if enabled:
            await wait_until(lambda: watchdog.thread is not None)
            assert watchdog.thread.is_alive()
            assert supervisor.statuses["loop-stall-watchdog"].critical is False
        else:
            assert watchdog.thread is None
            assert "loop-stall-watchdog" not in supervisor.tasks
    assert watchdog.thread is None or not watchdog.thread.is_alive()


def test_watchdog_stop_joins_with_one_second_timeout():
    watchdog = LoopStallWatchdog()
    watchdog.thread = Mock()
    watchdog.stop()
    watchdog.thread.join.assert_called_once_with(timeout=1.0)
