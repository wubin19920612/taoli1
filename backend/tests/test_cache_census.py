import asyncio
import json
import sys
import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType, SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, PrivateAttr, create_model

from app.core import config
from app.runtime import cache_census as census
from app.runtime import memory_probe
from app.runtime.supervisor import TaskSupervisor
from app.services.snapshot_store import SnapshotStore


@pytest.fixture(autouse=True)
def unavailable_cgroup_by_default(monkeypatch):
    # Keep offline tests independent of the host/container's memory pressure.
    monkeypatch.setattr(census, "_read_cgroup_file", lambda path: "max", raising=False)


def size(value):
    return census.deep_size(value, max_objects=100_000, deadline=time.monotonic() + 5)


def test_nested_models_slots_and_cycles():
    class Model(BaseModel):
        model_config = ConfigDict(extra="allow")
        items: list
        _private: object = PrivateAttr(default=None)

    @dataclass(slots=True)
    class Row:
        payload: object

    value = Model(items=[Row(deque([bytearray(5000)]))], extra=[bytearray(6000)])
    before = size(value)[0]
    value._private = bytearray(7000)
    assert size(value)[0] >= before + 7000
    assert before > 11000
    assert size(MappingProxyType({1: value}))[0] > size(value)[0]
    cycle = []
    cycle.append(cycle)
    assert size(cycle) == (sys.getsizeof(cycle), 1, False)
    nested = []
    for _ in range(3000):
        nested = [nested]
    assert size(nested)[1:] == (3001, False)


def test_one_shared_set_attributes_unique_to_first_target_and_sorts(monkeypatch):
    shared = [bytearray(10000)]
    a, b = [shared], [shared, bytearray(1000)]
    expected_first = size(a)[0] / 1024
    expected_second = (size(b)[0] - size(shared)[0]) / 1024
    expected_objects = size([a, b])[1] - 1
    sets = []

    class TrackingSet(set):
        def __init__(self):
            super().__init__()
            sets.append(self)

    monkeypatch.setattr(census, "set", TrackingSet, raising=False)
    result = census.run_census([
        census.CensusTarget("first", lambda: a),
        census.CensusTarget("second", lambda: b),
        census.CensusTarget("shared_again", lambda: shared),
    ])
    assert len(sets) == 1
    assert not sets[0]  # Released before returning to the probe.
    rows = {row["name"]: row for row in result["cache_census"]}
    assert rows["first"]["unique_kb"] == expected_first
    assert rows["second"]["unique_kb"] == expected_second
    assert rows["shared_again"]["unique_kb"] == 0
    assert rows["shared_again"]["objects"] == 0
    assert result["cache_census"][0]["name"] == "first"
    assert result["visited_objects"] == expected_objects == sum(row["objects"] for row in rows.values())
    assert all(set(row) == {"name", "entries", "unique_kb", "objects", "truncated"}
               for row in rows.values())


def test_object_budget_counts_unique_objects_globally():
    large = [bytearray(1) for _ in range(1000)]
    result = census.run_census([
        census.CensusTarget("one", lambda: large),
        census.CensusTarget("two", lambda: [bytearray(100)]),
    ], max_objects=5)
    rows = result["cache_census"]
    assert all(row["truncated"] for row in rows)
    assert result["visited_objects"] == sum(row["objects"] for row in rows) == 5
    assert size([])[2] is False
    assert census.deep_size(large, max_objects=0, deadline=time.monotonic() + 1) == (0, 0, True)


def test_million_references_do_not_spend_unique_budget(monkeypatch):
    monkeypatch.setattr(census.time, "monotonic", lambda: 0.0)
    shared = bytearray(100)
    repeated = [shared] * 1_000_000
    later = [bytearray(200)]
    # Exercise both the normal sample path and a full million-visit traversal.
    for threshold in (2000, 1_000_000):
        monkeypatch.setattr(census, "_SAMPLE_THRESHOLD", threshold, raising=False)
        result = census.run_census([
            census.CensusTarget("repeated", lambda: repeated),
            census.CensusTarget("later", lambda: later),
        ], max_objects=5)
        rows = {row["name"]: row for row in result["cache_census"]}
        assert all(not row["truncated"] for row in rows.values())
        assert rows["later"]["unique_kb"] > 0
        assert result["visited_objects"] == 4
        assert result["visits"] == (503 if threshold == 2000 else 1_000_003)


@pytest.mark.parametrize("kind", [dict, list, tuple, set, deque])
@pytest.mark.parametrize("count", [2000, 2001, 10003])
def test_sampling_boundary_stride_and_exact_container_size(kind, count):
    values = [bytearray(100) for _ in range(count)] if kind in (list, tuple, deque) else list(range(1000, count + 1000))
    value = kind(enumerate(values)) if kind is dict else kind(values)
    result = census.run_census([census.CensusTarget("container", lambda: value)])
    row = result["cache_census"][0]
    assert not row["truncated"]
    if count <= 2000:
        assert "sampled" not in row and "sample_size" not in row
        assert row["unique_kb"] * 1024 == size(value)[0]
    else:
        assert row["sampled"] is True and row["sample_size"] == 500
        assert result["visited_objects"] == (1001 if kind is dict else 501)
        children = sum(sys.getsizeof(v) for v in values)
        if kind is dict:
            children += sum(sys.getsizeof(k) for k in value)
        assert row["unique_kb"] * 1024 == pytest.approx(sys.getsizeof(value) + children)


def test_sampled_elements_join_shared_set_and_nested_samples_are_reported():
    values = [bytearray(100 + i) for i in range(3000)]
    first = [values]
    result = census.run_census([
        census.CensusTarget("nested", lambda: first),
        census.CensusTarget("selected_again", lambda: [values[0], values[6]]),
        census.CensusTarget("not_selected", lambda: values[1]),
    ])
    rows = {row["name"]: row for row in result["cache_census"]}
    assert rows["nested"]["sampled"] is True
    assert rows["nested"]["sample_size"] == 500
    expected = sys.getsizeof(first) + sys.getsizeof(values)
    expected += 6 * sum(sys.getsizeof(values[i]) for i in range(0, 3000, 6))
    assert rows["nested"]["unique_kb"] * 1024 == expected
    assert rows["selected_again"]["unique_kb"] * 1024 == sys.getsizeof([None, None])
    assert rows["not_selected"]["unique_kb"] * 1024 == sys.getsizeof(values[1])


def test_nested_sampling_factors_multiply():
    inner = [bytearray(100) for _ in range(3000)]
    outer = [inner] + [None] * 2999
    row = census.run_census([census.CensusTarget("nested", lambda: outer)])["cache_census"][0]
    expected = sys.getsizeof(outer) + 6 * sys.getsizeof(inner)
    expected += 36 * 500 * sys.getsizeof(inner[0]) + 6 * sys.getsizeof(None)
    assert not row["truncated"]
    assert row["unique_kb"] * 1024 == expected
    assert row["sampled"] is True and row["sample_size"] == 1000


def _snapshot_fixture(count):
    model = create_model("CensusSnapshot", **{f"field_{i}": (float, ...) for i in range(60)})
    return [model(**{f"field_{j}": float(i * 60 + j) for j in range(60)}) for i in range(count)]


def test_sampled_pydantic_estimate_within_fifteen_percent_of_full_size(monkeypatch, tmp_path):
    models = _snapshot_fixture(10_000)
    estimated, objects, truncated = size(models)
    assert not truncated and objects < 100_000
    monkeypatch.setattr(census, "_SAMPLE_THRESHOLD", sys.maxsize, raising=False)
    full, _, truncated = census.deep_size(models, max_objects=3_000_000, deadline=time.monotonic() + 30)
    assert not truncated
    error = abs(estimated - full) / full
    assert error < 0.15
    (tmp_path / "sampling_accuracy.json").write_text(json.dumps({
        "estimated_bytes": estimated, "full_bytes": full, "relative_error": error,
    }), encoding="utf-8")


def test_target_timeout_does_not_expire_later_target(monkeypatch):
    now = [0.0]
    original = census.sys.getsizeof

    class Slow:
        pass

    slow = Slow()

    def sizeof(value):
        if value is slow:
            now[0] += 0.51
        return original(value)

    monkeypatch.setattr(census.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(census.sys, "getsizeof", sizeof)
    result = census.run_census([
        census.CensusTarget("slow", lambda: [slow]),
        census.CensusTarget("later", lambda: [bytearray(100)]),
    ])
    rows = {row["name"]: row for row in result["cache_census"]}
    assert rows["slow"]["truncated"] is True
    assert rows["slow"]["unique_kb"] > 0
    assert rows["later"]["truncated"] is False
    assert rows["later"]["unique_kb"] > 0


def test_getter_time_is_part_of_target_budget(monkeypatch):
    now = [0.0]

    def slow_getter():
        now[0] += 0.51
        return [bytearray(100)]

    monkeypatch.setattr(census.time, "monotonic", lambda: now[0])
    result = census.run_census([
        census.CensusTarget("slow", slow_getter),
        census.CensusTarget("later", lambda: [bytearray(100)]),
    ])
    rows = {row["name"]: row for row in result["cache_census"]}
    assert rows["slow"]["truncated"] is True
    assert rows["later"]["truncated"] is False
    assert rows["later"]["unique_kb"] > 0


def test_deadline_shared_by_all_targets(monkeypatch):
    ticks = iter(i * 0.01 for i in range(1000))
    monkeypatch.setattr(census.time, "monotonic", lambda: next(ticks))
    called = []
    result = census.run_census([
        census.CensusTarget("one", lambda: list(range(100))),
        census.CensusTarget("two", lambda: called.append(True)),
    ], budget_seconds=0.03)
    assert all(row["truncated"] for row in result["cache_census"])
    assert not called
    assert result["census_ms"] < 200


def test_missing_errors_and_log_privacy(caplog):
    secret = "sensitive-" + "payload"

    def broken():
        raise ValueError(secret)

    result = census.run_census([
        census.CensusTarget("missing", lambda: None),
        census.CensusTarget("broken", broken),
        census.CensusTarget("normal", lambda: {secret: [secret]}),
    ])
    rows = {row["name"]: row for row in result["cache_census"]}
    assert rows["missing"]["missing"] is True
    assert result["census_errors"] == 1
    assert rows["normal"]["entries"] == 1
    memory_probe._log(result)
    assert secret not in caplog.text
    assert secret not in json.dumps(result)
    assert rows["normal"]["unique_kb"] == size({secret: [secret]})[0] / 1024


def test_lru_only_counts_without_sizing_or_invocation():
    @lru_cache(maxsize=2)
    def cached(index):
        return bytearray(10000)

    cached(1)
    row = census.run_census([census.CensusTarget("lru", lambda: cached)])["cache_census"][0]
    assert row["entries"] == 1
    assert row["unique_kb"] is None
    assert cached.cache_info().hits == 0


def test_partial_sizing_failure_keeps_unique_attribution_and_other_targets():
    class Broken:
        def __sizeof__(self):
            raise ValueError("sensitive-" + "failure")

    shared = [bytearray(4096)]
    result = census.run_census([
        census.CensusTarget("partial", lambda: [shared, Broken()]),
        census.CensusTarget("healthy", lambda: shared),
    ])
    rows = {r["name"]: r for r in result["cache_census"]}
    assert result["census_errors"] == 1
    assert rows["partial"]["unique_kb"] >= size(shared)[0] / 1024
    assert rows["healthy"]["objects"] == 0
    assert rows["healthy"]["unique_kb"] == 0


def test_ephemeral_getter_roots_stay_alive_until_census_finishes():
    import weakref

    @dataclass
    class Payload:
        data: object

    refs = []

    def getter():
        assert all(ref() is not None for ref in refs)
        value = Payload(bytearray(100))
        refs.append(weakref.ref(value))
        return value

    result = census.run_census([census.CensusTarget(str(i), getter) for i in range(5)])
    assert not result.get("census_errors")
    assert all(ref() is None for ref in refs)


def test_inherited_private_slots_without_properties():
    class Parent:
        __slots__ = ("__payload",)

        def __init__(self):
            self.__payload = bytearray(5000)

        @property
        def forbidden(self):
            raise AssertionError

    class Child(Parent):
        __slots__ = ("other",)

    value = Child()
    value.other = bytearray(6000)
    assert size(value)[0] > 11000
    assert census.deep_size(value, max_objects=100, deadline=time.monotonic() - 1) == (0, 0, True)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_census_interval_rejects_invalid(value):
    with pytest.raises(ValueError):
        config.Settings(memory_census_interval_seconds=value)


def test_settings_default_and_environment(monkeypatch):
    monkeypatch.setattr(config, "find_dotenv", lambda **kwargs: "")
    monkeypatch.delenv("MEMORY_CENSUS_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_CENSUS_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("MEMORY_CENSUS_MAX_OBJECTS", raising=False)
    settings = config.get_settings.__wrapped__()
    assert settings.memory_census_enabled is False
    assert settings.memory_census_interval_seconds == 300
    assert settings.memory_census_max_objects == 600_000
    assert config.Settings().memory_census_max_objects == 600_000
    assert census.run_census.__kwdefaults__["max_objects"] == 600_000
    monkeypatch.setenv("MEMORY_CENSUS_ENABLED", "true")
    monkeypatch.setenv("MEMORY_CENSUS_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("MEMORY_CENSUS_MAX_OBJECTS", "12345")
    settings = config.get_settings.__wrapped__()
    assert settings.memory_census_enabled is True
    assert settings.memory_census_interval_seconds == 2.5
    assert settings.memory_census_max_objects == 12345


@pytest.mark.parametrize("value", [0, -1, 1.5, 1.0, True, "10", None, float("inf"), float("nan")])
def test_census_max_objects_requires_positive_integer(value):
    with pytest.raises(ValueError, match="memory_census_max_objects"):
        config.Settings(memory_census_max_objects=value)


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "inf", "nan", "true", ""])
def test_census_max_objects_invalid_environment(monkeypatch, value):
    monkeypatch.setattr(config, "find_dotenv", lambda **kwargs: "")
    monkeypatch.setenv("MEMORY_CENSUS_MAX_OBJECTS", value)
    with pytest.raises(ValueError):
        config.get_settings.__wrapped__()


@pytest.mark.parametrize("probe_enabled,census_enabled,workers", [
    (False, False, True), (False, True, True), (True, False, True),
    (True, True, False), (True, True, True),
])
async def test_lifespan_builds_targets_only_when_enabled(monkeypatch, probe_enabled, census_enabled, workers):
    from app import main

    built = []
    probes = []
    monkeypatch.setattr(census, "build_census_targets", lambda state: built.append(state) or [])
    monkeypatch.setattr(TaskSupervisor, "start", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "default_exchange_adapters", list)
    monkeypatch.setattr(memory_probe, "MemoryProbe", lambda *args, **kwargs: probes.append(kwargs))
    app = main.create_app(settings=config.Settings(
        database_url="sqlite:///:memory:", memory_probe_enabled=probe_enabled,
        memory_census_enabled=census_enabled,
        memory_census_max_objects=12345,
    ), start_background_workers=workers)
    async with app.router.lifespan_context(app):
        assert len(built) == int(probe_enabled and census_enabled and workers)
        if probes:
            assert (probes[0].get("census_targets") is not None) == census_enabled
            assert probes[0]["census_max_objects"] == 12345


@pytest.mark.parametrize("enabled,broken", [(False, False), (True, False), (True, True)])
async def test_scheduling_independent_interval_and_error_isolation(monkeypatch, enabled, broken):
    now = [0.0]
    stop = asyncio.Event()
    samples, runs, logs = [], [], []

    def run(targets, *, max_objects):
        assert max_objects == 12345
        runs.append(now[0])
        if broken:
            raise ValueError("sensitive-" + "failure")
        return {"cache_census": [], "census_ms": 1, "visited_objects": 0}

    async def wait_for(awaitable, timeout):
        awaitable.close()
        now[0] += timeout
        if now[0] >= 7:
            stop.set()
        raise TimeoutError

    monkeypatch.setattr(census, "run_census", run)
    monkeypatch.setattr(memory_probe.asyncio, "wait_for", wait_for)
    monkeypatch.setattr(memory_probe, "_log", logs.append)
    probe = memory_probe.MemoryProbe(
        SnapshotStore(), TaskSupervisor(), interval_seconds=5, clock=lambda: now[0],
        census_targets=[] if enabled else None, census_interval_seconds=2,
        census_max_objects=12345,
    )
    monkeypatch.setattr(probe, "sample", lambda: samples.append(now[0]))
    await probe.run(stop)
    assert samples == [0, 5]
    assert runs == ([2, 4, 6] if enabled else [])
    if broken:
        assert logs == [{"census_errors": 1}] * 3
    elif enabled:
        assert all("rss_anon_mb" in row for row in logs)
        assert all("census_rss_delta_mb" in row and row["visited_objects"] == 0 for row in logs)
    else:
        assert logs == []


def test_registry_contains_all_static_paths_and_reads_replacements():
    state = SimpleNamespace(snapshot_store=SnapshotStore())
    targets = census.build_census_targets(state)
    names = {target.name for target in targets}
    assert len(names) == len(targets)
    for path in census.SERVICE_PATHS:
        assert path in names
    assert {"config.get_settings", "market_sessions.us_stock_session_close"} <= names
    chosen = next(t for t in targets if t.name == "snapshot_store._markets")
    state.snapshot_store._markets = [bytearray(400)]
    assert chosen.getter() is state.snapshot_store._markets
    assert all(t.getter() is None for t in targets if t.name.startswith("market_validation."))
    owners = [t.name.partition(".")[0] for t in targets]
    last_snapshot = max(i for i, owner in enumerate(owners) if owner == "snapshot_store")
    collector_indices = [i for i, owner in enumerate(owners) if owner == "market_collector"]
    assert last_snapshot < min(collector_indices)
    assert collector_indices == list(range(last_snapshot + 1, max(collector_indices) + 1))


@pytest.mark.parametrize("max_objects", [600_000, 1_000_000])
@pytest.mark.parametrize("margin,skipped", [(-1, True), (0, False), (1, False)])
def test_cgroup_headroom_gate_before_any_getter(monkeypatch, max_objects, margin, skipped):
    limit = 1024 ** 3
    headroom = max_objects * 80 + 64 * 1024 ** 2 + margin
    values = {"memory.max": str(limit), "memory.current": str(limit - headroom)}
    reads, calls = [], []

    def read(path):
        reads.append(path)
        return values[path.rsplit("/", 1)[-1]]

    monkeypatch.setattr(census, "_read_cgroup_file", read)
    if skipped:
        def forbidden_set():
            pytest.fail("low-headroom census must not allocate a visited set")

        monkeypatch.setattr(census, "set", forbidden_set, raising=False)
    result = census.run_census([
        census.CensusTarget("target", lambda: calls.append(True) or [bytearray(100)]),
    ], max_objects=max_objects)
    assert reads == ["/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"]
    if skipped:
        assert not calls
        assert result == {"census_skipped": "low_headroom", "headroom_mb": headroom / 1024 ** 2,
                          "visited_objects": 0, "visits": 0}
    else:
        assert calls == [True]
        assert result["visited_objects"] == 2
        assert "census_skipped" not in result


@pytest.mark.parametrize("limit,current", [
    (OSError(), "0"), (str(1024 ** 3), OSError()), (UnicodeError(), "0"),
    ("max\n", "0"), (str(1024 ** 3), "max"), ("invalid", "0"), (str(1024 ** 3), "invalid"),
    ("-1", "0"), ("0", "0"), (str(1024 ** 3), "-1"),
])
def test_unavailable_or_unlimited_cgroup_still_runs(monkeypatch, limit, current):
    def read(path):
        value = limit if path.endswith("memory.max") else current
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(census, "_read_cgroup_file", read)
    result = census.run_census([census.CensusTarget("target", lambda: [bytearray(100)])])
    assert "census_skipped" not in result
    assert result["visited_objects"] == 2


@pytest.mark.parametrize("before,after,expected", [(700.0, 712.5, 12.5), (700.0, 699.0, -1.0),
                                                   (None, None, None), (700.0, None, None)])
@pytest.mark.parametrize("low_headroom", [False, True])
async def test_probe_census_logs_rss_delta(monkeypatch, caplog, before, after, expected, low_headroom):
    now = [0.0]
    stop = asyncio.Event()
    statuses = iter([before, after])

    if low_headroom:
        monkeypatch.setattr(census, "_read_cgroup_file", lambda path: (
            str(1024 ** 3) if path.endswith("memory.max") else str(960 * 1024 ** 2)
        ))

    async def wait_for(awaitable, timeout):
        awaitable.close()
        if now[0] >= 2:
            stop.set()
        now[0] += timeout
        raise TimeoutError

    monkeypatch.setattr(memory_probe.asyncio, "wait_for", wait_for)
    monkeypatch.setattr(memory_probe, "read_process_status", lambda: {"rss_anon_mb": next(statuses)})
    probe = memory_probe.MemoryProbe(
        SnapshotStore(), TaskSupervisor(), clock=lambda: now[0], census_targets=[],
        census_interval_seconds=2,
    )
    monkeypatch.setattr(probe, "sample", lambda: None)
    await probe.run(stop)
    logs = [json.loads(record.getMessage()) for record in caplog.records
            if record.name == "memory_probe"]
    assert len(logs) == 1
    assert logs[0]["rss_anon_mb"] == after
    assert logs[0]["census_rss_delta_mb"] == expected
    assert logs[0]["visited_objects"] == 0
    assert logs[0]["memory_probe"] == 1
    if low_headroom:
        assert logs[0]["census_skipped"] == "low_headroom"
        assert logs[0]["headroom_mb"] == 64
    else:
        assert "census_skipped" not in logs[0]


async def test_coroutine_snapshot_previous_generation_is_registered():
    from app.services.snapshot_consumers import LatestSnapshotSlot, PublishedSnapshot, SnapshotConsumer
    from datetime import UTC, datetime

    slot = LatestSnapshotSlot()
    entered = asyncio.Event()
    release = asyncio.Event()
    stop = asyncio.Event()

    async def handler(snapshot):
        entered.set()
        await release.wait()

    consumer = SnapshotConsumer("history-recorder", slot, handler)
    first = PublishedSnapshot(1, datetime.now(UTC), (), ())
    second = PublishedSnapshot(2, datetime.now(UTC), (), ())
    slot.put(first)
    task = asyncio.create_task(consumer.run(stop))
    try:
        await entered.wait()
        slot.put(second)
        state = SimpleNamespace(
            snapshot_consumers={"history-recorder": consumer},
            task_supervisor=SimpleNamespace(tasks={"history-recorder": task}),
        )
        targets = {t.name: t for t in census.build_census_targets(state)}
        assert targets["history_snapshot.slot"].getter() is second
        assert targets["history_snapshot.active"].getter() is first
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_inspecting_active_snapshot_does_not_pin_previous_generation():
    import gc
    import weakref
    from datetime import UTC, datetime
    from app.services.snapshot_consumers import LatestSnapshotSlot, PublishedSnapshot, SnapshotConsumer

    slot = LatestSnapshotSlot()
    processed = asyncio.Event()
    stop = asyncio.Event()

    async def handler(snapshot):
        processed.set()

    consumer = SnapshotConsumer("history-recorder", slot, handler)
    snapshot = PublishedSnapshot(1, datetime.now(UTC), (), ())
    previous = weakref.ref(snapshot)
    slot.put(snapshot)
    del snapshot
    task = asyncio.create_task(consumer.run(stop))
    try:
        await processed.wait()
        state = SimpleNamespace(task_supervisor=SimpleNamespace(tasks={"history-recorder": task}))
        assert census._task_local(state, "history-recorder", "run", "snapshot") is previous()
        processed.clear()
        slot.put(PublishedSnapshot(2, datetime.now(UTC), (), ()))
        await processed.wait()
        gc.collect()
        assert previous() is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_actual_service_registry_and_offline_benchmark(monkeypatch, tmp_path):
    from app import main
    from app.services.announcements import default_announcement_provider
    from app.services.collector import default_exchange_adapters

    monkeypatch.setattr(TaskSupervisor, "start", lambda *args, **kwargs: None)
    app = main.create_app(settings=config.Settings(database_url="sqlite:///:memory:"),
                          start_background_workers=False)
    async with app.router.lifespan_context(app):
        # Real constructors, no workers and no network. Populate the optional
        # collector ownership branch without starting its business loop.
        adapters = default_exchange_adapters()
        providers = default_announcement_provider(app.state.announcement_repo)
        app.state.market_collector = SimpleNamespace(adapters=adapters)
        app.state.announcement_provider = providers
        try:
            targets = census.build_census_targets(app.state)
            by_name = {t.name: t for t in targets}
            required = {
                "account_position_service._cache", "account_connection_service._provider_cache",
                "trade_availability_service._cache", "astro_hl_gc_catalog._cache",
                "instrument_market_cap_service._search_cache", "instrument_market_cap_service._market_cache",
                "market_validation.registry.bindings", "announcement_research._cache",
                "history_recorder.rows", "announcement_enrichment.batch",
                "index_component_monitor._latest_prices", "squeeze_route_monitor._queue._queue",
            }
            assert required <= by_name.keys()
            for attribute in ("_REALTIME_PAIR_SPREAD_CACHE", "_REALTIME_PAIR_FUNDING_CACHE",
                              "_REALTIME_PAIR_OPEN_INTEREST_CACHE", "_REALTIME_SYMBOL_SPREAD_CACHE"):
                assert f"pair_spread_query.{attribute}" in by_name
            for path in ("market_collector.adapters", "trade_availability_service.adapters"):
                owners = census._path(app.state, path)
                owners = list(owners.values()) if isinstance(owners, dict) else owners
                for index, owner in enumerate(owners):
                    for attr in ("_markets", "_cached", "_rtoken_spot_symbols", "market_errors", "_collateral_names"):
                        target = by_name[f"{path}.{index}.{attr}"]
                        assert target.getter() is getattr(owner, attr, None)
            for index, provider in enumerate(providers.providers):
                for attr in ("_localization._cache", "_localization._missing_until",
                             "last_source_errors", "last_source_articles"):
                    assert by_name[f"announcement_provider.providers.{index}.{attr}"].getter() is census._path(provider, attr)
            baseline = census.run_census(targets)
            assert not baseline.get("census_errors")
            # Domain-shaped model graph, shared across two store containers.
            class Payload(BaseModel):
                observations: list[float]
            app.state.snapshot_store._markets = [Payload(observations=[float(i)] * 8) for i in range(12000)]
            app.state.snapshot_store._all_markets = list(app.state.snapshot_store._markets)
            runs = [census.run_census(targets) for _ in range(3)]
            assert all(not result.get("census_errors") for result in runs)
            assert all(not row["truncated"] for result in runs for row in result["cache_census"])
            metrics = {
                "targets": len(targets), "empty_census_ms": baseline["census_ms"],
                "models": 12000, "census_ms": [r["census_ms"] for r in runs],
                "unique_bytes": sum(r["unique_kb"] or 0 for r in runs[-1]["cache_census"]) * 1024,
            }
            (tmp_path / "census_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
        finally:
            await providers.aclose()
            for adapter in adapters:
                await adapter.client.aclose()


def test_production_scale_registered_targets_benchmark(monkeypatch, tmp_path):
    models = _snapshot_fixture(12_000)
    store = SnapshotStore()
    store._markets = models[:9410]
    store._all_markets = models[:9500]
    store._opportunities = models[7400:]  # 4600 entries, sharing two other lists.
    # 300k rows * (dict + distinct str + float + int) = 1.2m JSON objects,
    # plus a small number of containers and shared field names.
    payload = {"groups": {
        f"group_{group}": [{"name": f"row_{group}_{index}",
                             "price": float(group * 30_000 + index),
                             "count": 1000 + group * 30_000 + index}
                            for index in range(30_000)]
        for group in range(10)
    }}
    state = SimpleNamespace(snapshot_store=store,
                            market_collector=SimpleNamespace(_exchange_snapshots=payload))
    selected = {"snapshot_store._markets", "snapshot_store._all_markets",
                "snapshot_store._opportunities", "market_collector._exchange_snapshots"}
    targets = [target for target in census.build_census_targets(state) if target.name in selected]
    assert len(targets) == 4
    bookkeeping = []

    class TrackingSet(set):
        def clear(self):
            container = sys.getsizeof(self)
            identities = sum(sys.getsizeof(identity) for identity in self)
            bookkeeping.append({"objects": len(self), "set_bytes": container,
                                "id_bytes": identities, "total_bytes": container + identities})
            super().clear()

    monkeypatch.setattr(census, "set", TrackingSet, raising=False)
    runs = [census.run_census(targets) for _ in range(3)]
    for result in runs:
        assert not result.get("census_errors")
        assert all(row["unique_kb"] > 0 and not row["truncated"] for row in result["cache_census"])
        assert result["census_ms"] < 1500
        assert result["visited_objects"] <= 600_000
    assert all(row["objects"] == result["visited_objects"] for row, result in zip(bookkeeping, runs))
    (tmp_path / "production_scale_metrics.json").write_text(json.dumps({
        "python": sys.version, "snapshot_entries": [9410, 9500, 4600],
        "distinct_models": len(models), "model_fields": 60,
        "json_rows": 300_000, "json_objects_approx": 1_200_000,
        "runs": runs, "bookkeeping": bookkeeping,
    }), encoding="utf-8")


async def test_enrichment_round_allocation_experiment(monkeypatch, tmp_path):
    """Local-only tracemalloc experiment over the unchanged production loop."""
    import gc
    import tracemalloc
    import httpx
    from app.models.announcement import AnnouncementSettings
    from app.services import announcements

    count = 40
    feed = json.dumps({"data": {"catalogs": [{
        "catalogId": 1, "catalogName": "New Listing", "articles": [{
            "id": i, "code": f"fixture-{i}", "title": f"New listing notice {i}",
            "releaseDate": 1792886400000, "unused": str(i) + "x" * 8192,
        } for i in range(count)],
    }]}}).encode()
    detail = json.dumps({"data": {"body": {"text": "schedule details " * 256}}}).encode()
    metrics = {"providers": 3, "rows_per_provider": count,
               "feed_bytes": len(feed), "detail_bytes": len(detail),
               "decoded_feed_bytes": size(json.loads(feed))[0], "rounds": []}

    def response(request):
        return httpx.Response(200, content=detail if "/detail/" in request.url.path else feed)

    client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    providers = [announcements.BinanceAnnouncementProvider(client=client) for _ in range(3)]

    class Repository:
        async def has_any(self):
            return True

        async def latest_published_at(self, **kwargs):
            return None

        async def create_if_new(self, candidate):
            return None  # Simulate existing durable rows without a fake memory DB.

    monitor = announcements.AnnouncementMonitor(Repository())
    group = announcements.MultiAnnouncementProvider(providers)
    settings = AnnouncementSettings(enabled=True, record_exchanges=[providers[0].exchange])

    async def settings_loader():
        return settings

    stop = asyncio.Event()
    original_wait = asyncio.wait_for
    baseline = [0]
    task = None

    async def wait_for(awaitable, timeout):
        if timeout != 600:
            return await original_wait(awaitable, timeout)
        awaitable.close()
        # The actual loop is now at its inter-round sleep; no fake retains rows.
        gc.collect()
        current, peak = tracemalloc.get_traced_memory()
        metrics["rounds"].append({
            "peak_delta_bytes": peak - baseline[0], "retained_delta_bytes": current - baseline[0],
            "current_bytes": current,
        })
        baseline[0] = current
        tracemalloc.reset_peak()
        if len(metrics["rounds"]) >= 2:
            stop.set()
            return True
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    gc.collect()
    tracemalloc.start(1)
    try:
        baseline[0] = tracemalloc.get_traced_memory()[0]
        task = asyncio.create_task(announcements.run_announcement_enrichment_loop(
            group, monitor, settings_loader, stop, startup_delay_seconds=0,
        ))
        await task
        gc.collect()
        metrics["after_loop_bytes"] = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
        await client.aclose()
    assert len(metrics["rounds"]) == 2
    assert all(row["peak_delta_bytes"] > row["retained_delta_bytes"] for row in metrics["rounds"])
    assert metrics["after_loop_bytes"] < metrics["rounds"][-1]["current_bytes"]
    (tmp_path / "enrichment_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
