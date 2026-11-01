import asyncio
import gc
import importlib
import json
from unittest.mock import Mock

import pytest

from app.runtime.supervisor import TaskSupervisor


@pytest.fixture
def tuning(monkeypatch):
    thresholds = gc.get_threshold()
    frozen = gc.get_freeze_count()
    for name in ("GC_TUNING_ENABLED", "GC_THRESHOLDS", "GC_FREEZE_AFTER_STARTUP",
                 "GC_FREEZE_DELAY_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    module = importlib.import_module("app.runtime.gc_tuning")
    monkeypatch.setattr(module, "_freeze_done", False, raising=False)
    releases = []

    def start(supervisor):
        release = module.start_gc_tuning(supervisor)
        releases.append(release)
        return release

    try:
        yield module, start
    finally:
        for release in reversed(releases):
            release()
        # Check cleanup before the emergency restoration for failed tests.
        try:
            assert gc.get_threshold() == thresholds
            assert gc.get_freeze_count() in (0, frozen)
        finally:
            gc.unfreeze()
            gc.set_threshold(*thresholds)
            gc.collect()  # Restore CPython's pre-existing immortal permanent pool.
            assert gc.get_freeze_count() == frozen


@pytest.mark.parametrize("raw,expected", [
    (None, (10000, 20, 10)), (" 12000, 30, 12 ", (12000, 30, 12)),
    ("bad", (700, 10, 10)), ("1,2", (700, 10, 10)),
    ("0,2,3", (700, 10, 10)), ("-1,2,3", (700, 10, 10)),
    ("1.5,2,3", (700, 10, 10)), ("1,2,3,4", (700, 10, 10)),
    ("999999999999999999999,2,3", (700, 10, 10)),
])
async def test_thresholds_and_warning(tuning, monkeypatch, raw, expected):
    module, start = tuning
    warning = Mock()
    monkeypatch.setattr(module.logger, "warning", warning)
    monkeypatch.setenv("GC_FREEZE_AFTER_STARTUP", "false")
    if raw is not None:
        monkeypatch.setenv("GC_THRESHOLDS", raw)
    supervisor = TaskSupervisor()
    original = gc.get_threshold()
    try:
        release = start(supervisor)
        assert gc.get_threshold() == expected
        assert warning.call_count == int(expected == (700, 10, 10))
        assert not supervisor.tasks
        release()
        release()
        assert gc.get_threshold() == original
    finally:
        await supervisor.stop()


@pytest.mark.parametrize("value", ["false", "0", " OFF ", "no"])
async def test_disabled_does_not_touch_gc(tuning, monkeypatch, value):
    _, start = tuning
    monkeypatch.setenv("GC_TUNING_ENABLED", value)
    supervisor = TaskSupervisor()
    with monkeypatch.context() as patch:
        for name in ("set_threshold", "freeze", "unfreeze", "collect", "get_threshold",
                     "get_freeze_count"):
            patch.setattr(gc, name, Mock(side_effect=AssertionError(name)))
        release = start(supervisor)
        release()
        assert not supervisor.tasks


@pytest.mark.parametrize("raw,expected,warnings", [
    (None, 60, 0), ("0", 0, 0), ("600", 600, 0), ("0.25", 0.25, 0),
    ("-1", 60, 1), ("601", 60, 1), ("nan", 60, 1), ("inf", 60, 1),
    ("bad", 60, 1),
])
async def test_delay_and_single_freeze(tuning, monkeypatch, raw, expected, warnings):
    module, start = tuning
    if raw is not None:
        monkeypatch.setenv("GC_FREEZE_DELAY_SECONDS", raw)
    gate = asyncio.Event()
    delays = []

    async def sleep(delay):
        delays.append(delay)
        await gate.wait()

    monkeypatch.setattr(module, "_sleep", sleep)
    warning, info = Mock(), Mock()
    monkeypatch.setattr(module.logger, "warning", warning)
    monkeypatch.setattr(module.logger, "info", info)
    collect, freeze = Mock(wraps=gc.collect), Mock(wraps=gc.freeze)
    monkeypatch.setattr(gc, "collect", collect)
    monkeypatch.setattr(gc, "freeze", freeze)
    supervisor = TaskSupervisor()
    original = gc.get_threshold()
    try:
        release = start(supervisor)
        duplicate = start(supervisor)
        await asyncio.sleep(0)
        assert delays == [expected]
        assert warning.call_count == warnings
        freeze.assert_not_called()
        collect.assert_not_called()
        assert supervisor.statuses["gc-freeze"].critical is False
        gate.set()
        await supervisor.tasks["gc-freeze"]
        collect.assert_called_once_with()
        freeze.assert_called_once_with()
        assert gc.get_freeze_count() > 0
        row = json.loads(info.call_args.args[0])
        assert row["gc_tuning"] == 1
        assert row["thresholds"] == [10000, 20, 10]
        assert row["frozen"] > 0 and row["collect_ms"] >= 0
        release()
        assert gc.get_threshold() == (10000, 20, 10)
        duplicate()
        assert gc.get_threshold() == original
        assert gc.get_freeze_count() == 0
    finally:
        await supervisor.stop()


@pytest.mark.parametrize("started", [False, True])
async def test_cancel_before_freeze_restores_state(tuning, monkeypatch, started):
    _, start = tuning
    freeze = Mock(wraps=gc.freeze)
    monkeypatch.setattr(gc, "freeze", freeze)
    supervisor = TaskSupervisor()
    original = gc.get_threshold()
    frozen = gc.get_freeze_count()
    try:
        release = start(supervisor)
        if started:
            await asyncio.sleep(0)
        release()
        await supervisor.stop()
        freeze.assert_not_called()
        assert gc.get_threshold() == original
        assert gc.get_freeze_count() == frozen
    finally:
        await supervisor.stop()


async def test_registration_failure_rolls_back(tuning, monkeypatch):
    _, start = tuning
    supervisor = TaskSupervisor()
    monkeypatch.setattr(supervisor, "start", Mock(side_effect=RuntimeError("registration")))
    original = gc.get_threshold()
    frozen = gc.get_freeze_count()
    with pytest.raises(RuntimeError, match="registration"):
        start(supervisor)
    assert gc.get_threshold() == original
    assert gc.get_freeze_count() == frozen


@pytest.mark.parametrize("exceptional", [False, True])
async def test_repeated_real_lifespan_restores_gc(tuning, monkeypatch, exceptional):
    from app.core.config import Settings
    from app.main import create_app

    monkeypatch.setenv("GC_FREEZE_DELAY_SECONDS", "0")
    app = create_app(settings=Settings(database_url="sqlite:///:memory:"),
                     start_collector=False, start_background_workers=False)
    original = gc.get_threshold()
    frozen = gc.get_freeze_count()
    freeze = Mock(wraps=gc.freeze)
    monkeypatch.setattr(gc, "freeze", freeze)
    for iteration in range(2):
        try:
            async with app.router.lifespan_context(app):
                assert gc.get_threshold() == (10000, 20, 10)
                if iteration == 0:
                    await app.state.task_supervisor.tasks["gc-freeze"]
                    assert gc.get_freeze_count() > frozen
                else:
                    assert "gc-freeze" not in app.state.task_supervisor.tasks
                freeze.assert_called_once_with()
                if exceptional:
                    raise RuntimeError("lifespan body")
        except RuntimeError as exc:
            if not exceptional or str(exc) != "lifespan body":
                raise
        assert gc.get_threshold() == original
        if iteration == 0:
            assert gc.get_freeze_count() == 0
        else:
            assert gc.get_freeze_count() <= frozen
