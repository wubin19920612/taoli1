import asyncio
import gc
import importlib
import io
import json
import logging
from unittest.mock import Mock

import pytest


@pytest.fixture
def monitor(monkeypatch):
    module = importlib.import_module("app.runtime.gc_pause")
    monkeypatch.delenv("GC_PAUSE_LOG_ENABLED", raising=False)
    monkeypatch.delenv("GC_PAUSE_LOG_THRESHOLD_MS", raising=False)
    instance = module.GcPauseMonitor()
    try:
        yield instance
    finally:
        instance.stop()


@pytest.fixture
def pause_output(monkeypatch, monitor):
    module = importlib.import_module("app.runtime.gc_pause")
    stream = io.StringIO()
    monkeypatch.setattr(module.logger, "handlers", [logging.StreamHandler(stream)])
    return stream


def pause(monitor, now, milliseconds, generation=2):
    monitor.callback("start", {"generation": generation})
    now[0] += milliseconds / 1000
    monitor.callback("stop", {"generation": generation, "collected": 17})


def test_gc_real_collection_accumulates_and_callback_lifecycle(monitor):
    before = list(gc.callbacks)
    monitor.start()
    monitor.start()
    assert gc.callbacks.count(monitor.callback) == 1
    previous_ms, previous_gen2 = monitor.snapshot()
    gc.collect(2)
    milliseconds, gen2 = monitor.snapshot()
    assert milliseconds > previous_ms
    assert gen2 >= previous_gen2 + 1
    monitor.stop()
    monitor.stop()
    assert gc.callbacks == before


def test_gc_pause_threshold_deferred_logging_and_rolling_minute(monitor, pause_output):
    now = [0.0]
    monitor.clock = lambda: now[0]
    pause(monitor, now, 199)
    monitor.flush()
    assert pause_output.getvalue() == ""
    pause(monitor, now, 200)
    assert pause_output.getvalue() == ""  # No logging inside a GC callback.
    monitor.flush()
    first_log = now[0]
    for _ in range(5):
        pause(monitor, now, 250)
        monitor.flush()
    rows = [json.loads(line) for line in pause_output.getvalue().splitlines()]
    assert len(rows) == 5
    assert rows[0] == {"gc_pause": 1, "gen": 2, "ms": 200.0, "collected": 17}
    now[0] = first_log + 59.999 - 0.25
    pause(monitor, now, 250)
    monitor.flush()
    assert len(pause_output.getvalue().splitlines()) == 5
    now[0] = first_log + 60.0 - 0.25
    pause(monitor, now, 250)
    monitor.flush()
    assert len(pause_output.getvalue().splitlines()) == 6
    assert monitor.snapshot()[1] == 9  # Suppression never suppresses counters.


@pytest.mark.parametrize("generation", [0, 1, 2])
def test_gc_counts_every_generation_and_logs_actual_generation(monitor, pause_output, generation):
    now = [0.0]
    monitor.clock = lambda: now[0]
    pause(monitor, now, 250, generation)
    assert monitor.snapshot() == (250.0, int(generation == 2))
    monitor.flush()
    assert json.loads(pause_output.getvalue()) == {
        "gc_pause": 1, "gen": generation, "ms": 250.0, "collected": 17,
    }


@pytest.mark.parametrize("enabled", ["0", "false", "OFF", " no "])
def test_gc_log_switch_preserves_collection_counters(monkeypatch, monitor, pause_output, enabled):
    module = importlib.import_module("app.runtime.gc_pause")
    monkeypatch.setenv("GC_PAUSE_LOG_ENABLED", enabled)
    instance = module.GcPauseMonitor(clock=lambda: now[0])
    now = [0.0]
    pause(instance, now, 250)
    instance.flush()
    assert not instance.enabled
    assert instance.snapshot() == (250.0, 1)
    assert pause_output.getvalue() == ""


@pytest.mark.parametrize("value,expected", [
    (None, 200.0), ("350", 350.0), ("bad", 200.0), ("0", 200.0),
    ("-1", 200.0), ("nan", 200.0), ("inf", 200.0),
])
def test_gc_log_configuration(monkeypatch, monitor, value, expected):
    module = importlib.import_module("app.runtime.gc_pause")
    if value is not None:
        monkeypatch.setenv("GC_PAUSE_LOG_THRESHOLD_MS", value)
    instance = module.GcPauseMonitor()
    assert instance.enabled
    assert instance.threshold_ms == expected
    now = [0.0]
    instance.clock = lambda: now[0]
    pause(instance, now, expected - 1)
    pause(instance, now, expected + 1)
    info = Mock()
    monkeypatch.setattr(module.logger, "info", info)
    instance.flush()
    assert info.call_count == 1
    assert module.logger.level == logging.INFO
    assert module.logger.handlers
    assert module.logger.propagate is False


def test_gc_callback_and_log_failures_are_isolated(monkeypatch, monitor, pause_output):
    module = importlib.import_module("app.runtime.gc_pause")
    monitor.clock = Mock(side_effect=RuntimeError("sensitive failure"))
    monitor.start()
    # Exercise the registered callback through CPython, as well as directly.
    gc.collect(2)
    monitor.callback("start", {"generation": 2})
    monitor.callback("stop", {"generation": 2})
    now = [0.0]
    monitor.clock = lambda: now[0]
    for info in ({}, {"generation": -1}, {"generation": 3}, {"generation": None}):
        monitor.callback("start", info)
        monitor.callback("stop", info)
    monitor.callback("stop", {"generation": 2})  # No successful start.
    assert monitor.snapshot() == (0.0, 0)
    broken = Mock(side_effect=RuntimeError("sensitive failure"))
    monkeypatch.setattr(module.logger, "info", broken)
    for _ in range(6):
        pause(monitor, now, 250)
        monitor.flush()
    assert broken.call_count == 5  # Failed writes still consume the rate budget.
    assert pause_output.getvalue() == ""


async def test_gc_monitor_run_registers_and_unregisters(monitor):
    task = asyncio.create_task(monitor.run())
    try:
        await asyncio.sleep(0)
        assert monitor.callback in gc.callbacks
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert monitor.callback not in gc.callbacks


@pytest.mark.parametrize("watchdog_enabled", [True, False])
async def test_main_gc_monitor_independent_of_watchdog(monkeypatch, monitor, watchdog_enabled):
    from app.core.config import Settings
    from app.main import create_app

    monkeypatch.setenv("LOOP_STALL_WATCHDOG_ENABLED", str(watchdog_enabled))
    app = create_app(settings=Settings(database_url="sqlite:///:memory:"),
                     start_background_workers=False)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
        instance = app.state.gc_pause_monitor
        assert instance.callback in gc.callbacks
        assert app.state.loop_stall_watchdog.gc_monitor is instance
        assert app.state.task_supervisor.statuses["gc-pause-log"].critical is False
    assert instance.callback not in gc.callbacks
