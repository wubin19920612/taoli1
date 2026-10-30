import asyncio
import builtins
import gc
import io
import json
import logging
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core import config
from app.runtime import memory_probe
from app.runtime.supervisor import TaskStatus, TaskSupervisor
from app.services import pair_spread_query
from app.services.snapshot_store import SnapshotStore


def forbidden(*args, **kwargs):
    raise AssertionError("must not inspect or copy objects")


def records(caplog):
    return [json.loads(record.getMessage()) for record in caplog.records
            if record.name == "memory_probe"]


def test_settings_defaults_and_environment(monkeypatch):
    monkeypatch.setattr(config, "find_dotenv", lambda **kwargs: "")
    for name in ("MEMORY_PROBE_ENABLED", "MEMORY_PROBE_INTERVAL_SECONDS",
                 "MEMORY_TRACEMALLOC_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    defaults = config.get_settings.__wrapped__()
    assert defaults.memory_probe_enabled is False
    assert defaults.memory_probe_interval_seconds == 10
    assert defaults.memory_tracemalloc_seconds == 0
    monkeypatch.setenv("MEMORY_PROBE_ENABLED", "true")
    monkeypatch.setenv("MEMORY_PROBE_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("MEMORY_TRACEMALLOC_SECONDS", "120")
    settings = config.get_settings.__wrapped__()
    assert settings.memory_probe_enabled is True
    assert settings.memory_probe_interval_seconds == 2.5
    assert settings.memory_tracemalloc_seconds == 120


@pytest.mark.parametrize("field,value", [
    ("memory_probe_interval_seconds", 0), ("memory_probe_interval_seconds", -1),
    ("memory_probe_interval_seconds", float("inf")),
    ("memory_probe_interval_seconds", float("nan")),
    ("memory_tracemalloc_seconds", -1), ("memory_tracemalloc_seconds", float("inf")),
    ("memory_tracemalloc_seconds", float("nan")),
])
def test_invalid_settings_are_rejected(field, value):
    with pytest.raises(ValueError):
        config.Settings(**{field: value})


def test_disabled_beat_keeps_original_path_without_proc_reads(monkeypatch):
    supervisor = TaskSupervisor()
    supervisor.statuses["worker"] = TaskStatus(critical=True)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(memory_probe, "read_process_status", forbidden)
    assert supervisor.beat.__func__ is TaskSupervisor.beat
    supervisor.beat("worker", True)
    supervisor.beat("worker", False)
    supervisor.beat("absent", True)
    status = supervisor.statuses["worker"]
    assert status.iterations == 2
    assert status.last_ok_at is not None
    assert status.last_rss_delta_mb is None


@pytest.mark.parametrize("enabled,trace_seconds,workers", [
    (False, 0, True), (True, 0, True), (False, 120, True), (True, 120, True),
    (True, 120, False),
])
async def test_lifespan_wires_only_enabled_noncritical_probes(
    monkeypatch, enabled, trace_seconds, workers,
):
    from app import main

    started = {}
    original_start = TaskSupervisor.start

    def start(self, name, factory, *, restart, critical):
        started[name] = (factory, restart, critical)
        if name == "event-loop-lag":
            return original_start(self, name, factory, restart=restart, critical=critical)

    monkeypatch.setattr(TaskSupervisor, "start", start)
    monkeypatch.setattr(main, "default_exchange_adapters", list)
    monkeypatch.setattr(memory_probe, "read_process_status", forbidden)
    app = main.create_app(settings=config.Settings(
        database_url="sqlite:///:memory:", memory_probe_enabled=enabled,
        memory_tracemalloc_seconds=trace_seconds,
    ), start_background_workers=workers)
    async with app.router.lifespan_context(app):
        assert ("memory-sampler" in started) == (enabled and workers)
        assert ("memory-tracemalloc" in started) == (trace_seconds > 0 and workers)
        for name in ("memory-sampler", "memory-tracemalloc"):
            if name in started:
                assert started[name][2] is False
        assert (app.state.task_supervisor.beat.__func__ is TaskSupervisor.beat) == (
            not (enabled and workers)
        )


def test_proc_parser_reads_only_numeric_fields(monkeypatch):
    monkeypatch.setattr(memory_probe.sys, "platform", "linux")
    status = "Name:\tprivate-input\nVmRSS:\t20480 kB\nRssAnon:\t10240 kB\nThreads:\t17\n"
    opened = Mock(side_effect=lambda *args, **kwargs: io.StringIO(status))
    monkeypatch.setattr(builtins, "open", opened)
    assert memory_probe.read_process_status() == {
        "rss_mb": 20.0, "rss_anon_mb": 10.0, "threads": 17,
    }
    assert opened.call_args.args[0] == "/proc/self/status"


@pytest.mark.parametrize("platform,content", [
    ("win32", None), ("linux", None), ("linux", "VmRSS: invalid kB\nThreads: bad\n"),
])
def test_proc_unavailable_or_invalid_is_null(monkeypatch, platform, content):
    monkeypatch.setattr(memory_probe.sys, "platform", platform)
    opened = Mock(side_effect=OSError("https://private.test/key")) if content is None else (
        Mock(side_effect=lambda *args, **kwargs: io.StringIO(content))
    )
    monkeypatch.setattr(builtins, "open", opened)
    assert memory_probe.read_process_status() == {
        "rss_mb": None, "rss_anon_mb": None, "threads": None,
    }
    if platform == "win32":
        opened.assert_not_called()


def test_store_sizes_use_only_lengths_and_match_getter_fallback():
    class LengthOnly:
        def __init__(self, length):
            self.length = length

        def __len__(self):
            return self.length

        __iter__ = forbidden

    store = SnapshotStore()
    store._markets = LengthOnly(12000)
    store._opportunities = LengthOnly(7000)
    assert store.sizes() == {"markets": 12000, "all_markets": 12000, "opportunities": 7000}
    store._all_markets = LengthOnly(0)
    assert store.sizes()["all_markets"] == 0
    store._all_markets = LengthOnly(15000)
    assert store.sizes()["all_markets"] == 15000


def test_sampler_json_gc_deltas_and_no_large_object_access(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="memory_probe")
    store = SnapshotStore()
    for name in ("get_markets", "get_all_markets", "get_opportunities"):
        monkeypatch.setattr(store, name, forbidden)
    monkeypatch.setattr(gc, "get_objects", forbidden)
    collections = [10, 20, 30]
    monkeypatch.setattr(gc, "get_stats", lambda: [{"collections": n} for n in collections])
    monkeypatch.setattr(gc, "get_count", lambda: (1, 2, 3))
    monkeypatch.setattr(memory_probe, "read_process_status", lambda: {
        "rss_mb": 20, "rss_anon_mb": 10, "threads": 17,
    })
    monkeypatch.setattr(pair_spread_query, "_REALTIME_PAIR_SPREAD_CACHE", {
        "secret-key-https://private.test": [(1, "private-market-data")],
    })
    probe = memory_probe.MemoryProbe(store, TaskSupervisor())
    collections[:] = [12, 21, 30]
    probe.sample()
    [sample] = records(caplog)
    assert set(sample) == {
        "memory_probe",
        "rss_mb", "rss_anon_mb", "threads", "gc_counts", "gc_collections",
        "snapshot_sizes", "realtime_caches", "active_tasks",
    }
    assert sample["gc_counts"] == [1, 2, 3]
    assert sample["gc_collections"] == [2, 1, 0]
    assert sample["snapshot_sizes"] == {"markets": 0, "all_markets": 0, "opportunities": 0}
    assert sample["realtime_caches"]["_REALTIME_PAIR_SPREAD_CACHE"] == {"keys": 1, "points": 1}
    assert sample["rss_anon_mb"] == 10
    assert "secret-key" not in caplog.text and "private-market-data" not in caplog.text
    assert "https://" not in caplog.text
    assert "\n" not in next(r.getMessage() for r in caplog.records if r.name == "memory_probe")
    probe.sample()
    assert records(caplog)[-1]["gc_collections"] == [0, 0, 0]


def test_uvicorn_default_logging_emits_probe_json_to_container_stream():
    script = """
import logging.config
from uvicorn.config import LOGGING_CONFIG
logging.config.dictConfig(LOGGING_CONFIG)
from app.runtime.memory_probe import MemoryProbe
from app.runtime.supervisor import TaskSupervisor
from app.services.snapshot_store import SnapshotStore
MemoryProbe(SnapshotStore(), TaskSupervisor()).sample()
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            timeout=20, check=True)
    lines = (result.stdout + result.stderr).strip().splitlines()
    assert len(lines) == 1
    assert "memory_probe" in lines[0]
    assert json.loads(lines[0])["snapshot_sizes"] == {
        "markets": 0, "all_markets": 0, "opportunities": 0,
    }


def test_default_app_import_does_not_load_or_start_probes(monkeypatch):
    monkeypatch.setenv("MEMORY_PROBE_ENABLED", "false")
    monkeypatch.setenv("MEMORY_TRACEMALLOC_SECONDS", "0")
    script = """
import sys
import app.main
assert 'app.runtime.memory_probe' not in sys.modules
assert not app.main.app.state.task_supervisor.tasks
if 'tracemalloc' in sys.modules:
    assert not sys.modules['tracemalloc'].is_tracing()
"""
    subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                   timeout=60, check=True)


def test_sampling_unreadable_proc_emits_nulls(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="memory_probe")
    monkeypatch.setattr(memory_probe.sys, "platform", "linux")
    monkeypatch.setattr(builtins, "open", Mock(side_effect=OSError("private-key")))
    memory_probe.MemoryProbe(SnapshotStore(), TaskSupervisor()).sample()
    sample = records(caplog)[0]
    assert all(sample[name] is None for name in ("rss_mb", "rss_anon_mb", "threads"))
    assert "private-key" not in caplog.text


def test_active_tasks_compare_each_sampling_boundary(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="memory_probe")
    now = datetime(2026, 10, 25, tzinfo=UTC)
    supervisor = TaskSupervisor(now_fn=lambda: now)
    for name in ("market-collector", "history-recorder", "idle"):
        supervisor.statuses[name] = TaskStatus(critical=False)
    supervisor.beat("idle", True)
    probe = memory_probe.MemoryProbe(SnapshotStore(), supervisor)
    probe.sample()
    assert records(caplog)[-1]["active_tasks"] == []
    now += timedelta(seconds=1)
    supervisor.beat("market-collector", True)
    supervisor.beat("history-recorder", False)
    probe.sample()
    assert records(caplog)[-1]["active_tasks"] == ["history-recorder", "market-collector"]
    probe.sample()
    assert records(caplog)[-1]["active_tasks"] == []


def test_task_deltas_are_per_task_bounded_and_missing_reads_reset_baseline(monkeypatch):
    supervisor = TaskSupervisor()
    for name in ("a", "b"):
        supervisor.statuses[name] = TaskStatus(critical=False)
    rss = iter([100, 101, 105, 98, 104, None, 120, 122])
    monkeypatch.setattr(memory_probe, "read_process_status", lambda: {"rss_anon_mb": next(rss)})
    supervisor.enable_memory_probe(window=2)
    for name in ("a", "b", "a", "b", "a"):
        supervisor.beat(name, True)
    assert supervisor.statuses["a"].last_rss_delta_mb == -1
    assert supervisor.statuses["b"].last_rss_delta_mb == -3
    assert supervisor.task_rss_deltas() == {
        "a": {"samples": 2, "max": 5, "p50": -1},
        "b": {"samples": 1, "max": -3, "p50": -3},
    }
    for _ in range(3):
        supervisor.beat("a", False)
    assert supervisor.statuses["a"].last_rss_delta_mb == 2
    assert supervisor.task_rss_deltas()["a"] == {"samples": 2, "max": 2, "p50": -1}
    supervisor.beat("unknown", True)  # Does not read proc or create a window.


def test_task_delta_logs_are_emitted_every_60_seconds(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="memory_probe")
    now = [0.0]
    supervisor = TaskSupervisor()
    supervisor.enable_memory_probe()
    probe = memory_probe.MemoryProbe(SnapshotStore(), supervisor, clock=lambda: now[0])
    for tick in (0, 59, 60, 61, 120):
        now[0] = tick
        probe.sample()
    assert len([row for row in records(caplog) if "task_rss_deltas" in row]) == 2


def test_task_summary_keeps_unknown_memory_as_null(monkeypatch):
    supervisor = TaskSupervisor()
    supervisor.statuses["worker"] = TaskStatus(critical=False)
    supervisor.enable_memory_probe()
    monkeypatch.setattr(memory_probe, "read_process_status", lambda: {"rss_anon_mb": None})
    supervisor.beat("worker", True)
    assert supervisor.task_rss_deltas() == {
        "worker": {"samples": 0, "max": None, "p50": None},
    }


async def test_delta_report_deadline_is_independent_of_sampling_interval(monkeypatch):
    now = [0.0]
    emitted = []
    stop = asyncio.Event()
    monkeypatch.setattr(memory_probe, "_log", lambda row: emitted.append((now[0], row)))

    async def wait_for(awaitable, *, timeout):
        awaitable.close()
        now[0] += timeout
        if now[0] >= 125:
            stop.set()
        raise TimeoutError

    monkeypatch.setattr(memory_probe.asyncio, "wait_for", wait_for)
    probe = memory_probe.MemoryProbe(SnapshotStore(), TaskSupervisor(),
                                     interval_seconds=120, clock=lambda: now[0])
    await probe.run(stop)
    assert [tick for tick, row in emitted if "task_rss_deltas" in row] == [60, 120]
    assert [tick for tick, row in emitted if "snapshot_sizes" in row] == [0, 120]


async def test_sampler_failure_logs_only_numeric_error_and_continues(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="memory_probe")
    stop = asyncio.Event()
    store = SnapshotStore()
    calls = []

    def sizes():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("secret-key https://private.test/market-data")
        stop.set()
        return {"markets": 0, "all_markets": 0, "opportunities": 0}

    monkeypatch.setattr(store, "sizes", sizes)
    supervisor = TaskSupervisor()
    probe = memory_probe.MemoryProbe(store, supervisor, interval_seconds=0.001)
    task = supervisor.start("memory-sampler", lambda: probe.run(stop), restart=True, critical=False)
    try:
        await asyncio.wait_for(task, 2)
        assert len(calls) == 2
        assert records(caplog)[0] == {"memory_probe": 1, "sample_errors": 1}
        assert "secret-key" not in caplog.text and "https://" not in caplog.text
        assert supervisor.statuses["memory-sampler"].iterations == 2
    finally:
        await supervisor.stop()


async def test_early_timer_wakeup_does_not_fabricate_sample_heartbeats(monkeypatch):
    now = [0.0]
    samples = []
    waits = []
    stop = asyncio.Event()
    store = SnapshotStore()

    def sizes():
        samples.append(now[0])
        if len(samples) == 2:
            stop.set()
        return {"markets": 0, "all_markets": 0, "opportunities": 0}

    async def wait_for(awaitable, *, timeout):
        awaitable.close()
        if stop.is_set():
            return True
        waits.append(timeout)
        now[0] += timeout / 2 if len(waits) == 1 else timeout
        raise TimeoutError

    monkeypatch.setattr(store, "sizes", sizes)
    monkeypatch.setattr(memory_probe.asyncio, "wait_for", wait_for)
    supervisor = TaskSupervisor()
    supervisor.statuses["memory-sampler"] = TaskStatus(critical=False)
    await memory_probe.MemoryProbe(store, supervisor, clock=lambda: now[0]).run(stop)
    assert samples == [0, 10]
    assert supervisor.statuses["memory-sampler"].iterations == 2


def test_enabled_probe_failure_preserves_business_heartbeat(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="memory_probe")
    supervisor = TaskSupervisor()
    supervisor.statuses["worker"] = TaskStatus(critical=False)
    supervisor.enable_memory_probe()
    monkeypatch.setattr(memory_probe, "read_process_status", Mock(
        side_effect=RuntimeError("secret https://private.test/key"),
    ))
    supervisor.beat("worker", True)
    assert supervisor.statuses["worker"].iterations == 1
    assert supervisor.statuses["worker"].last_ok_at is not None
    assert records(caplog) == [{"memory_probe": 1, "beat_errors": 1}]
    assert "secret" not in caplog.text and "https://" not in caplog.text


def fake_tracer(monkeypatch, snapshots):
    tracer = SimpleNamespace(
        is_tracing=Mock(return_value=False), start=Mock(), stop=Mock(),
        take_snapshot=Mock(side_effect=snapshots),
    )
    monkeypatch.setitem(sys.modules, "tracemalloc", tracer)
    return tracer


def trace_stat(index):
    return SimpleNamespace(
        traceback=[SimpleNamespace(filename=f"/app/backend/app/file{index}.py", lineno=index + 1)],
        size=2048, count=3, size_diff=-1024, count_diff=-1,
    )


async def test_tracemalloc_window_is_one_frame_bounded_and_logs_only_locations(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="memory_probe")
    now = [0.0]
    stats = [trace_stat(i) for i in range(30)]
    snapshots = [SimpleNamespace(statistics=Mock(return_value=stats),
                                 compare_to=Mock(return_value=stats)) for _ in range(3)]
    tracer = fake_tracer(monkeypatch, snapshots)

    async def sleep(seconds):
        now[0] += seconds

    window = memory_probe.TracemallocWindow(120, clock=lambda: now[0], sleep=sleep)
    await window.run()
    tracer.start.assert_called_once_with(1)
    tracer.stop.assert_called_once_with()
    assert tracer.take_snapshot.call_count == 3
    assert now[0] == 120
    assert len(records(caplog)) == 2
    for row in records(caplog):
        assert set(row) == {"memory_probe", "tracemalloc_top", "tracemalloc_diff"}
        assert len(row["tracemalloc_top"]) == len(row["tracemalloc_diff"]) == 25
        assert row["tracemalloc_top"][0] == {"location": "file0.py:1", "size_kb": 2, "count": 3}
        assert row["tracemalloc_diff"][0] == {"location": "file0.py:1", "size_kb": -1, "count": -1}
    snapshots[1].statistics.assert_called_once_with("lineno")
    snapshots[1].compare_to.assert_called_once_with(snapshots[0], "lineno")


@pytest.mark.parametrize("seconds", [0, 5])
async def test_tracemalloc_zero_or_short_window(monkeypatch, seconds):
    tracer = fake_tracer(monkeypatch, [SimpleNamespace()])
    now = [0.0]

    async def sleep(delay):
        now[0] += delay

    await memory_probe.TracemallocWindow(seconds, clock=lambda: now[0], sleep=sleep).run()
    assert tracer.start.call_count == tracer.stop.call_count == int(seconds > 0)
    assert now[0] == seconds


@pytest.mark.parametrize("failure", ["cancel", "snapshot"])
async def test_tracemalloc_stops_on_cancellation_and_failure(monkeypatch, caplog, failure):
    caplog.set_level(logging.INFO, logger="memory_probe")
    tracer = fake_tracer(monkeypatch, [SimpleNamespace()])
    if failure == "snapshot":
        tracer.take_snapshot.side_effect = RuntimeError("secret https://private.test/trace")

    async def sleep(_):
        raise asyncio.CancelledError

    window = memory_probe.TracemallocWindow(120, sleep=sleep)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await window.run()
    else:
        await window.run()
        assert records(caplog) == [{"memory_probe": 1, "tracemalloc_errors": 1}]
    tracer.stop.assert_called_once_with()
    assert "secret" not in caplog.text and "https://" not in caplog.text


async def test_existing_tracemalloc_session_is_not_stopped(monkeypatch):
    tracer = fake_tracer(monkeypatch, [])
    tracer.is_tracing.return_value = True
    await memory_probe.TracemallocWindow(120).run()
    tracer.start.assert_not_called()
    tracer.stop.assert_not_called()


async def test_tracemalloc_stop_failure_is_logged_without_exception_details(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="memory_probe")
    tracer = fake_tracer(monkeypatch, [SimpleNamespace()])
    tracer.stop.side_effect = RuntimeError("secret https://private.test/trace")
    now = [0.0]

    async def sleep(delay):
        now[0] += delay

    await memory_probe.TracemallocWindow(5, clock=lambda: now[0], sleep=sleep).run()
    tracer.stop.assert_called_once_with()
    assert records(caplog) == [{"memory_probe": 1, "tracemalloc_errors": 1}]
    assert "secret" not in caplog.text and "https://" not in caplog.text


@pytest.mark.benchmark
def test_sampling_cost_with_large_snapshot_is_below_five_ms(monkeypatch):
    monkeypatch.setattr(gc, "get_objects", forbidden)
    store = SnapshotStore()
    # One fixture allocation; the sampler must never traverse or copy these lists.
    store._markets = [None] * 12000
    store._all_markets = store._markets
    store._opportunities = [None] * 12000
    for name in ("get_markets", "get_all_markets", "get_opportunities"):
        monkeypatch.setattr(store, name, forbidden)
    probe = memory_probe.MemoryProbe(store, TaskSupervisor())
    elapsed = []
    for _ in range(30):
        started = time.perf_counter()
        probe.sample()
        elapsed.append(time.perf_counter() - started)
    assert max(elapsed) < 0.005
