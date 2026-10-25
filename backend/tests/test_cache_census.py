import asyncio
import json
import sys
import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType, SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, PrivateAttr

from app.core import config
from app.runtime import cache_census as census
from app.runtime import memory_probe
from app.runtime.supervisor import TaskSupervisor
from app.services.snapshot_store import SnapshotStore


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


def test_shared_objects_own_and_unique_and_sorted():
    shared = [bytearray(10000)]
    a, b = [shared], [shared, bytearray(20000)]
    result = census.run_census([
        census.CensusTarget("first", lambda: a),
        census.CensusTarget("second", lambda: b),
    ])
    rows = {row["name"]: row for row in result["cache_census"]}
    assert rows["first"]["own_bytes"] == size(a)[0]
    assert rows["second"]["own_bytes"] == size(b)[0]
    assert rows["first"]["unique_bytes"] == size(a)[0]
    assert rows["second"]["unique_bytes"] == size(b)[0] - size(shared)[0]
    assert result["cache_census"][0]["name"] == "second"
    assert sum(row["unique_bytes"] for row in rows.values()) == size([a, b])[0] - sys.getsizeof([a, b])


def test_object_budget_is_global_and_wide_containers_are_lazy():
    large = [None] * 100_000
    rows = census.run_census([
        census.CensusTarget("one", lambda: large),
        census.CensusTarget("two", lambda: [bytearray(100)]),
    ], max_objects=5)["cache_census"]
    assert all(row["truncated"] for row in rows)
    assert sum(row["objects"] for row in rows) <= 5
    assert size([])[2] is False
    assert census.deep_size(large, max_objects=0, deadline=time.monotonic() + 1) == (0, 0, True)


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
    assert rows["normal"]["own_kb"] == rows["normal"]["own_bytes"] / 1024


def test_lru_only_counts_without_sizing_or_invocation():
    @lru_cache(maxsize=2)
    def cached(index):
        return bytearray(10000)

    cached(1)
    row = census.run_census([census.CensusTarget("lru", lambda: cached)])["cache_census"][0]
    assert row["entries"] == 1
    assert row["own_bytes"] is None and row["unique_bytes"] is None
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
    assert rows["partial"]["unique_bytes"] >= size(shared)[0]
    assert rows["healthy"]["own_bytes"] == size(shared)[0]
    assert rows["healthy"]["unique_bytes"] == 0


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
    settings = config.get_settings.__wrapped__()
    assert settings.memory_census_enabled is False
    assert settings.memory_census_interval_seconds == 300
    monkeypatch.setenv("MEMORY_CENSUS_ENABLED", "true")
    monkeypatch.setenv("MEMORY_CENSUS_INTERVAL_SECONDS", "2.5")
    settings = config.get_settings.__wrapped__()
    assert settings.memory_census_enabled is True
    assert settings.memory_census_interval_seconds == 2.5


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
    ), start_background_workers=workers)
    async with app.router.lifespan_context(app):
        assert len(built) == int(probe_enabled and census_enabled and workers)
        if probes:
            assert (probes[0].get("census_targets") is not None) == census_enabled


@pytest.mark.parametrize("enabled,broken", [(False, False), (True, False), (True, True)])
async def test_scheduling_independent_interval_and_error_isolation(monkeypatch, enabled, broken):
    now = [0.0]
    stop = asyncio.Event()
    samples, runs, logs = [], [], []

    def run(targets):
        runs.append(now[0])
        if broken:
            raise ValueError("sensitive-" + "failure")
        return {"cache_census": [], "census_ms": 1}

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
    )
    monkeypatch.setattr(probe, "sample", lambda: samples.append(now[0]))
    await probe.run(stop)
    assert samples == [0, 5]
    assert runs == ([2, 4, 6] if enabled else [])
    if broken:
        assert logs == [{"census_errors": 1}] * 3
    elif enabled:
        assert all("rss_anon_mb" in row for row in logs)
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
                "unique_bytes": sum(r["unique_bytes"] or 0 for r in runs[-1]["cache_census"]),
            }
            (tmp_path / "census_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
        finally:
            await providers.aclose()
            for adapter in adapters:
                await adapter.client.aclose()


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
