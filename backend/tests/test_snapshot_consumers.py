import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import config
from app.models.market import MarketSnapshot, MarketType
from app.models.settings import RiskSettings
from app.runtime.supervisor import TaskSupervisor
from app.services.collector import MarketCollector
from app.services.snapshot_consumers import LatestSnapshotSlot, PublishedSnapshot, SnapshotConsumer
from app.services.snapshot_store import SnapshotStore


NOW = datetime(2026, 10, 25, tzinfo=UTC)


def snapshot(generation):
    return PublishedSnapshot(generation, NOW, (), ())


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def make_collector(**kwargs):
    market = MarketSnapshot(
        exchange="fixed", symbol="BTCUSDT", base="BTC", raw_symbol="BTCUSDT",
        market_type=MarketType.SPOT, bid=100, ask=101, timestamp=NOW,
    )
    adapter = SimpleNamespace(
        name="fixed", fetch_spot_tickers=AsyncMock(return_value=[market]),
        fetch_future_tickers=AsyncMock(return_value=[]),
    )
    return MarketCollector([adapter], SnapshotStore(), now_fn=lambda: NOW, **kwargs)


async def test_slot_overwrites_waits_and_stops():
    slot, stop = LatestSnapshotSlot(), asyncio.Event()
    assert slot.stats() == {"latest_generation": None, "dropped": 0}
    for generation in (1, 2, 3):
        slot.put(snapshot(generation))
    current = await slot.get_newer(0, stop)
    assert current.generation == 3
    assert slot.stats() == {"latest_generation": 3, "dropped": 2}
    with pytest.raises(FrozenInstanceError):
        current.generation = 9
    waiting = asyncio.create_task(slot.get_newer(3, stop))
    await asyncio.sleep(0)
    assert not waiting.done()
    slot.put(snapshot(4))
    assert (await asyncio.wait_for(waiting, 2)).generation == 4
    assert slot.stats()["dropped"] == 2  # Delivered generations are not drops.
    waiting = asyncio.create_task(slot.get_newer(4, stop))
    await asyncio.sleep(0)
    stop.set()
    assert await asyncio.wait_for(waiting, 2) is None
    slot.put(snapshot(5))
    assert await slot.get_newer(4, stop) is None


async def test_cancelled_slot_wait_cleans_up_helper_tasks():
    before = asyncio.all_tasks()
    waiting = asyncio.create_task(LatestSnapshotSlot().get_newer(0, asyncio.Event()))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert asyncio.all_tasks() == before


async def test_consumer_failure_continues_reports_heartbeat_lag_and_bounded_durations(caplog):
    slot, stop = LatestSnapshotSlot(), asyncio.Event()
    entered, release = asyncio.Event(), asyncio.Event()
    clock = [0.0]
    calls = []

    async def handler(item):
        calls.append(item.generation)
        if item.generation == 1:
            clock[0] += 9
            raise RuntimeError("injected failure")
        entered.set()
        await release.wait()
        clock[0] += 2

    consumer = SnapshotConsumer("test-consumer", slot, handler, now_fn=lambda: clock[0], window=1)
    supervisor = TaskSupervisor()
    supervisor.start(consumer.name, lambda: consumer.run(stop), restart=True, critical=False)
    try:
        slot.put(snapshot(1))
        await until(lambda: consumer.stats()["failed"] == 1)
        assert supervisor.snapshot()[consumer.name]["last_ok_at"] is None
        assert "injected failure" in caplog.text
        slot.put(snapshot(2))
        await asyncio.wait_for(entered.wait(), 2)
        slot.put(snapshot(3))
        assert consumer.stats()["lag_generations"] == 2
        release.set()
        await until(lambda: consumer.stats()["processed_generation"] == 3)
        assert calls == [1, 2, 3]
        assert consumer.stats() == {
            "processed_generation": 3, "processed": 2, "failed": 1,
            "last_duration_seconds": 2, "duration_p95_seconds": 2, "lag_generations": 0,
        }
        status = supervisor.snapshot()[consumer.name]
        assert status["iterations"] == 3 and status["last_ok_at"] is not None
        stop.set()
        await asyncio.wait_for(supervisor.tasks[consumer.name], 2)
    finally:
        await supervisor.stop()


async def test_consumer_cancellation_is_not_a_failed_generation():
    slot, entered = LatestSnapshotSlot(), asyncio.Event()

    async def handler(_):
        entered.set()
        await asyncio.Event().wait()

    consumer = SnapshotConsumer("cancel", slot, handler)
    slot.put(snapshot(1))
    task = asyncio.create_task(consumer.run(asyncio.Event()))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert consumer.stats()["failed"] == consumer.stats()["processed"] == 0
    assert consumer.stats()["processed_generation"] == 0


@pytest.mark.parametrize("slow_dependency", ["history", "index"])
async def test_slow_dependencies_do_not_block_collection_or_each_other(slow_dependency):
    slots = [LatestSnapshotSlot(), LatestSnapshotSlot()]
    entered, release, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
    recorder = SimpleNamespace(record=AsyncMock())
    provider = SimpleNamespace(fetch_components=AsyncMock(return_value=[]))
    monitor = SimpleNamespace(observe_markets=AsyncMock(), process_snapshots=AsyncMock())

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return []

    if slow_dependency == "history":
        recorder.record.side_effect = blocked
    else:
        provider.fetch_components.side_effect = blocked
    collector = make_collector(snapshot_slots=slots, history_recorder=recorder,
                               index_component_provider=provider, index_component_monitor=monitor)
    consumers = [
        SnapshotConsumer("history-recorder", slots[0], collector.record_history),
        SnapshotConsumer("index-components", slots[1], lambda s: collector.process_index_components(s.markets)),
    ]
    tasks = [asyncio.create_task(c.run(stop)) for c in consumers]
    slow, fast = consumers if slow_dependency == "history" else consumers[::-1]
    try:
        await asyncio.wait_for(collector.collect_once(), 0.5)
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(2):
            await asyncio.wait_for(collector.collect_once(), 0.5)
        assert collector.store.generation == 3
        await until(lambda: fast.stats()["processed_generation"] == 3)
        assert slow.stats()["processed_generation"] == 0
        assert slow.slot.stats()["dropped"] == 1
        assert "history" not in collector.stage_timings.snapshot()["stages"]
        assert "index" not in collector.stage_timings.snapshot()["stages"]
        release.set()
        await until(lambda: slow.stats()["processed_generation"] == 3)
        assert slow.stats()["processed"] == 2  # In-flight 1 finishes, then only latest 3.
        if slow_dependency == "history":
            assert recorder.record.await_count == 2
        else:
            assert provider.fetch_components.await_count == 2
        assert all(call.kwargs["now"] == NOW for call in recorder.record.await_args_list)
    finally:
        stop.set()
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)


async def test_unstarted_consumer_only_records_latest_and_snapshot_lists_are_copied():
    slot, stop = LatestSnapshotSlot(), asyncio.Event()
    recorder = SimpleNamespace(record=AsyncMock())
    collector = make_collector(snapshot_slots=[slot], history_recorder=recorder)
    for _ in range(3):
        result = await collector.collect_once()
    item = await slot.get_newer(0, stop)
    assert item.generation == 3 and item.published_at == collector.store.published_at
    assert isinstance(item.markets, tuple) and isinstance(item.opportunities, tuple)
    assert item.markets[0] is result.markets[0]
    result.markets.clear()
    assert len(item.markets) == 1
    consumer = SnapshotConsumer("history-recorder", slot, collector.record_history)
    task = asyncio.create_task(consumer.run(stop))
    try:
        await until(lambda: consumer.stats()["processed"] == 1)
        recorder.record.assert_awaited_once_with([], now=NOW)
        assert slot.stats()["dropped"] == 2
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)


async def test_inline_preserves_history_arguments_and_index_order():
    calls = []
    recorder = SimpleNamespace(record=AsyncMock(side_effect=lambda *a, **kw: calls.append("history")))
    monitor = SimpleNamespace(observe_markets=AsyncMock(side_effect=lambda *a: calls.append("index")))
    collector = make_collector(history_recorder=recorder, index_component_monitor=monitor,
                               index_component_provider=SimpleNamespace(fetch_components=AsyncMock(return_value=[])))
    result = await collector.collect_once()
    recorder.record.assert_awaited_once_with(result.opportunities)
    assert calls == ["history", "index"]
    assert {"history", "index"} <= collector.stage_timings.rounds[-1].keys()


async def test_published_snapshot_contains_filtered_models_and_history_keeps_them_unchanged():
    slot = LatestSnapshotSlot()
    recorder = SimpleNamespace(record=AsyncMock())
    collector = make_collector(snapshot_slots=[slot], history_recorder=recorder,
                               risk_settings=RiskSettings(excluded_symbols=["BADUSDT"]))
    spot = collector.adapters[0].fetch_spot_tickers.return_value[0]
    future = spot.model_copy(update={"exchange": "other", "market_type": MarketType.FUTURE,
                                     "bid": 103, "ask": 104})
    excluded = spot.model_copy(update={"symbol": "BADUSDT", "base": "BAD", "raw_symbol": "BADUSDT"})
    collector.adapters[0].fetch_spot_tickers.return_value = [spot, future, excluded]
    result = await collector.collect_once()
    item = await slot.get_newer(0, asyncio.Event())
    assert item.opportunities and len(item.markets) == 2
    assert item.opportunities == tuple(result.opportunities)
    assert item.opportunities[0] is result.opportunities[0]
    before = [op.model_dump() for op in item.opportunities]
    result.opportunities.clear()
    await collector.record_history(item)
    recorder.record.assert_awaited_once_with(list(item.opportunities), now=item.published_at)
    assert [op.model_dump() for op in item.opportunities] == before


@pytest.mark.parametrize("background", [True, False])
@pytest.mark.parametrize("failure", ["timeout", "exception"])
async def test_risk_settings_fallback_and_recovery(background, failure, caplog):
    previous = RiskSettings(ignored_exchanges=["fixed"])
    current = RiskSettings()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        if calls == 2:
            if failure == "timeout":
                await asyncio.Event().wait()
            raise RuntimeError("settings unavailable")
        return previous if calls == 1 else current

    collector = make_collector(risk_settings_loader=loader, risk_settings_timeout_seconds=0.02,
                               snapshot_slots=[LatestSnapshotSlot()] if background else None)
    await collector.collect_once()
    result = await asyncio.wait_for(collector.collect_once(), 0.5)
    assert result.markets == [] and collector.risk_settings is previous
    assert collector.runtime_stats() == {"risk_settings_fallbacks": 1}
    assert any(record.levelname == "WARNING" for record in caplog.records)
    await collector.collect_once()
    assert collector.risk_settings is current
    assert collector.runtime_stats()["risk_settings_fallbacks"] == 1


@pytest.mark.parametrize("background", [True, False])
@pytest.mark.parametrize("failure", ["timeout", "exception"])
async def test_first_risk_settings_failure_propagates(background, failure):
    async def loader():
        if failure == "timeout":
            await asyncio.Event().wait()
        raise RuntimeError("first read failed")

    collector = make_collector(risk_settings_loader=loader, risk_settings_timeout_seconds=0.02,
                               snapshot_slots=[LatestSnapshotSlot()] if background else None)
    with pytest.raises(TimeoutError if failure == "timeout" else RuntimeError):
        await asyncio.wait_for(collector.collect_once(), 0.5)
    assert collector.store.generation == 0
    assert collector.runtime_stats()["risk_settings_fallbacks"] == 0


async def test_no_active_adapters_still_publish_to_background_slots():
    slot = LatestSnapshotSlot()
    collector = MarketCollector([], SnapshotStore(), snapshot_slots=[slot])
    await collector.collect_once()
    assert slot.stats()["latest_generation"] == collector.store.generation == 1


@pytest.mark.parametrize("mode", ["background", "inline"])
def test_config_environment_and_validation(monkeypatch, mode):
    monkeypatch.setattr(config, "find_dotenv", lambda **kw: "")
    monkeypatch.setenv("COLLECTOR_POST_PUBLISH_MODE", mode)
    monkeypatch.setenv("COLLECTOR_RISK_SETTINGS_TIMEOUT_SECONDS", "0.25")
    settings = config.get_settings.__wrapped__()
    assert settings.collector_post_publish_mode == mode
    assert settings.collector_risk_settings_timeout_seconds == 0.25
    assert config.Settings().collector_post_publish_mode == "background"
    monkeypatch.setenv("COLLECTOR_POST_PUBLISH_MODE", "typo")
    with pytest.raises(ValueError):
        config.get_settings.__wrapped__()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_config_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        config.Settings(collector_risk_settings_timeout_seconds=timeout)


@pytest.mark.parametrize("mode,start_collector", [("background", True), ("inline", True), ("background", False)])
async def test_lifespan_wires_only_enabled_consumers(monkeypatch, mode, start_collector):
    from app import main

    factories = {}
    start_task = main._start_background_task
    monkeypatch.setattr(main, "_start_background_task", lambda supervisor, factory, *, name: factories.setdefault(name, factory))
    monkeypatch.setattr(main, "default_exchange_adapters", lambda: [])
    monkeypatch.setattr(main, "default_announcement_provider", lambda *a: SimpleNamespace(aclose=AsyncMock()))
    app = main.create_app(settings=config.Settings(database_url="sqlite:///:memory:",
                          collector_post_publish_mode=mode, collector_risk_settings_timeout_seconds=0.25),
                          start_collector=start_collector, start_background_workers=start_collector)
    async with app.router.lifespan_context(app):
        consumers = getattr(app.state, "snapshot_consumers", {})
        expected = {"history-recorder", "index-components"} if start_collector and mode == "background" else set()
        assert consumers.keys() == expected
        assert factories.keys() & {"history-recorder", "index-components"} == expected
        if start_collector:
            collector = app.state.market_collector
            assert collector.risk_settings_timeout_seconds == 0.25
            if mode == "inline":
                assert collector.snapshot_slots is None
            else:
                assert list(collector.snapshot_slots) == [consumers[name].slot for name in ("history-recorder", "index-components")]
                collector.history_recorder.record = AsyncMock()
                collector.process_index_components = AsyncMock()
                for name in expected:
                    start_task(app.state.task_supervisor, factories[name], name=name)
                    assert not app.state.task_supervisor.statuses[name].critical
                await collector.collect_once()
                await until(lambda: all(c.stats()["processed_generation"] == 1 for c in consumers.values()))
                collector.history_recorder.record.assert_awaited_once_with([], now=collector.store.published_at)
                collector.process_index_components.assert_awaited_once_with(())
