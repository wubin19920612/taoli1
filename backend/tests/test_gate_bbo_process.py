import asyncio
import itertools
import json
import queue
from time import monotonic, perf_counter

import pytest
from gate_bbo_harness import fixture_child, hung_child

from app.core.config import Settings
from app.exchanges.gate_bbo import MAX_BATCH, RECONNECT_DELAYS, GateBboWorker
from app.services import gate_bbo_runtime as module
from app.services.gate_bbo_runtime import GateBboRuntime
from app.services.shadow_runtime import ShadowRuntime
from app.services.snapshot_store import SnapshotStore


async def eventually(predicate, timeout=10):
    end = monotonic() + timeout
    while not predicate():
        assert monotonic() < end, "Gate child condition timed out"
        await asyncio.sleep(.01)


def runtime(monkeypatch, target=fixture_child, **config):
    monkeypatch.setattr(module, "child_entry", target)
    rt = GateBboRuntime(Settings(gate_bbo_enabled=True, **config))
    rt.set_subscriptions({"spot": ["BTC_USDT"], "future": ["BTC_USDT"]})
    return rt


async def test_real_spawn_batches_and_bounded_graceful_shutdown(monkeypatch):
    rt = runtime(monkeypatch)
    try:
        await rt.start()
        await eventually(lambda: rt.get_fresh("spot", "BTC_USDT") is not None
                         and rt.get_fresh("future", "BTC_USDT") is not None)
        assert rt._process._start_method == "spawn"
        assert 1 <= rt.batch_size <= MAX_BATCH
        assert rt.batches >= 1 and rt.status()["cpu_seconds"] >= 0
        assert rt.status()["rss_mb"] > 0
    finally:
        start = perf_counter()
        await rt.aclose()
        elapsed = perf_counter() - start
        print(f"gate_graceful_shutdown_seconds={elapsed:.6f}")
        assert elapsed < 2
    assert rt._process is None and rt._incoming is None
    assert rt.get_fresh("spot", "BTC_USDT") is None
    await rt.aclose()


async def test_crash_invalidates_and_restarts_after_backoff(monkeypatch):
    rt = runtime(monkeypatch)
    try:
        await rt.start()
        await eventually(lambda: rt.get_fresh("spot", "BTC_USDT") is not None)
        first_pid = rt._process.pid
        rt._process.terminate()
        await eventually(lambda: rt._restart_at is not None)
        assert rt.get_fresh("spot", "BTC_USDT") is None
        assert 0 < rt._restart_at - monotonic() <= 1
        await asyncio.sleep(.2)
        assert rt.restarts == 0
        await eventually(lambda: rt.restarts == 1 and rt.get_fresh("spot", "BTC_USDT") is not None)
        assert rt._process.pid != first_pid
    finally:
        await rt.aclose()


async def test_entire_restart_ladder_and_stale_epoch(monkeypatch):
    rt = GateBboRuntime(Settings(gate_bbo_enabled=True))
    monkeypatch.setattr(module, "monotonic", lambda: 100)
    for delay in [1, 2, 5, 10, 30, 30]:
        previous = rt._epoch
        await rt._failed("crash")
        assert rt._restart_at == 100 + delay
        rt._transport_failed(previous)
        assert rt._transport_error is False
    assert RECONNECT_DELAYS == (1, 2, 5, 10, 30)


async def test_rss_limit_restarts_instead_of_disabling(monkeypatch):
    rt = runtime(monkeypatch)
    try:
        await rt.start()
        await eventually(lambda: rt.get_fresh("spot", "BTC_USDT") is not None)
        monkeypatch.setattr(module, "rss_mb", lambda pid: 121)
        rt._rss_at = 0
        await eventually(lambda: rt.last_error == "rss_limit")
        assert rt.get_fresh("spot", "BTC_USDT") is None
        monkeypatch.setattr(module, "rss_mb", lambda pid: 20)
        await eventually(lambda: rt.restarts == 1 and rt.get_fresh("spot", "BTC_USDT") is not None)
    finally:
        await rt.aclose()


@pytest.mark.parametrize("ignore_terminate", [False, True])
async def test_hung_child_shutdown_is_bounded_and_loop_keeps_running(monkeypatch, ignore_terminate):
    rt = runtime(monkeypatch, hung_child)
    gaps = []
    async def heartbeat():
        previous = perf_counter()
        while True:
            await asyncio.sleep(.005)
            now = perf_counter()
            gaps.append(now - previous)
            previous = now
    beat = asyncio.create_task(heartbeat())
    try:
        await rt.start()
        await eventually(lambda: rt.batches > 0)
        if ignore_terminate:
            monkeypatch.setattr(rt._process, "terminate", lambda: None)
        start = perf_counter()
        await rt.aclose()
        elapsed = perf_counter() - start
        print(f"gate_forced_shutdown_ignore_terminate={ignore_terminate} seconds={elapsed:.6f}")
        assert elapsed < 2
        assert rt._process is None
        assert max(gaps) < .25
    finally:
        await rt.aclose()
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)


async def test_gate_and_shadow_close_in_parallel_within_six_seconds(tmp_path, monkeypatch):
    from test_shadow_process import hung_child as shadow_hung_child

    import app.services.shadow_runtime as shadow_module

    monkeypatch.setattr(shadow_module, "child_entry", shadow_hung_child)
    gate = runtime(monkeypatch, hung_child)
    shadow = ShadowRuntime(Settings(shadow_enabled=True,
        database_url=f"sqlite:///{tmp_path / 'shadow.db'}"), SnapshotStore())
    try:
        await gate.start()
        await shadow.initialize()
        await eventually(lambda: gate.batches > 0)
        started = perf_counter()
        await asyncio.gather(gate.aclose(), shadow.aclose())
        elapsed = perf_counter() - started
        print(f"gate_shadow_parallel_shutdown_seconds={elapsed:.6f}")
        assert elapsed < 6
        assert gate._process is shadow._process is None
    finally:
        await asyncio.gather(gate.aclose(), shadow.aclose())


def test_batch_limit_latest_value_and_backpressure_retains_dirty():
    worker = GateBboWorker()
    # Exercise the hard bound independently of today's subscription caps.
    worker.dirty = {("spot", f"{i}_USDT"): (i,) for i in range(MAX_BATCH + 1)}
    output = queue.Queue(maxsize=1)
    assert worker.publish(output)
    assert len(worker.dirty) == 1
    worker.dirty[("spot", "0_USDT")] = (999,)
    assert worker.publish(output) is False
    assert len(worker.dirty) == 2
    first = output.get_nowait()
    assert len(first["quotes"]) == MAX_BATCH
    assert first["quotes"][("spot", "0_USDT")] == (0,)
    assert worker.publish(output)
    assert output.get_nowait()["quotes"][("spot", "0_USDT")] == (999,)
    assert not worker.dirty


async def test_subscription_batches_and_pacing():
    worker = GateBboWorker()
    worker.set_subscriptions({"spot": [f"C{i}_USDT" for i in range(205)]})
    sends = []
    class Socket:
        async def send(self, raw):
            sends.append((monotonic(), json.loads(raw)))
    task = asyncio.create_task(worker._send("spot", Socket()))
    try:
        await eventually(lambda: len(sends) == 3)
        assert [len(packet["payload"]) for _, packet in sends] == [100, 100, 5]
        assert all(b[0] - a[0] >= .2 for a, b in itertools.pairwise(sends))
        worker.set_subscriptions({"spot": ["C0_USDT"]})
        await eventually(lambda: len(sends) == 6)
        assert all(packet["event"] == "unsubscribe" for _, packet in sends[3:])
        assert worker.states["spot"]["subscribed"] == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_disabled_runtime_logs_fallbacks_without_spawning(monkeypatch):
    rt = GateBboRuntime(Settings())
    logs = []
    monkeypatch.setattr(module.logger, "info", logs.append)
    rt.record_merge(False)
    rt._log_at = 0
    await rt._tick()
    status = json.loads(logs[0])["gate_bbo"]
    assert status["enabled"] is False and status["merge_fallbacks"] == 1
    assert status["pid"] is None and set(status["connections"]) == {"spot", "future"}


def test_subscription_refresh_uses_latest_store_every_600_seconds(monkeypatch):
    from test_gate_bbo import market
    store = SnapshotStore()
    rt = GateBboRuntime(Settings(), store)
    clock = [100]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    store.set_all_markets([market(), market(exchange="binance")])
    rt.refresh_subscriptions()
    assert rt._subscriptions["spot"] == ["BTC_USDT"]
    store.set_all_markets([market("ETH_USDT"), market("ETH_USDT", exchange="binance")])
    clock[0] = 699
    rt.refresh_subscriptions()
    assert rt._subscriptions["spot"] == ["BTC_USDT"]
    clock[0] = 700
    rt.refresh_subscriptions()
    assert rt._subscriptions["spot"] == ["ETH_USDT"]


@pytest.mark.parametrize("kind,prefix", [("spot", "spot"), ("future", "futures")])
async def test_application_ping_is_periodic(kind, prefix):
    worker = GateBboWorker()
    assert worker.heartbeat_seconds == 30
    worker.heartbeat_seconds = .03
    sent = []
    class Socket:
        async def send(self, raw):
            sent.append(json.loads(raw))
    task = asyncio.create_task(worker._send(kind, Socket()))
    try:
        await eventually(lambda: len(sent) >= 2)
        assert all(packet["channel"] == f"{prefix}.ping" for packet in sent)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("kind", ["spot", "future"])
async def test_socket_failures_follow_full_reconnect_ladder(monkeypatch, kind):
    from websockets.asyncio import client

    import app.exchanges.gate_bbo as child_module
    worker = GateBboWorker()
    worker.set_subscriptions({kind: ["BTC_USDT"]})
    delays = []
    class FailedConnection:
        async def __aenter__(self):
            raise OSError("offline fixture")
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(client, "connect", lambda *a, **kw: FailedConnection())
    async def sleep(delay):
        delays.append(delay)
        assert worker.states[kind]["connected"] is False
        if len(delays) == 6:
            raise asyncio.CancelledError
    monkeypatch.setattr(child_module.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await worker._connection(kind)
    assert delays == [1, 2, 5, 10, 30, 30]
    assert worker.states[kind]["reconnects"] == 5


async def test_reconnect_clears_books_and_sequence_before_any_new_quote(monkeypatch):
    from test_gate_bbo import wire
    from websockets.asyncio import client

    import app.exchanges.gate_bbo as child_module
    worker = GateBboWorker()
    worker.set_subscriptions({"spot": ["BTC_USDT"]})
    key = "spot", "BTC_USDT"
    class Socket:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(client, "connect", lambda *a, **kw: Socket())
    async def receive(kind, websocket):
        if worker.states[kind]["generation"] == 1:
            worker.handle_message(kind, wire())
            raise OSError("disconnect")
        assert key not in worker.quotes and key not in worker._versions
        assert worker.dirty[key] is None
        worker.handle_message(kind, wire(u=1))  # Sequence can restart on a new connection.
        assert worker.quotes[key][5:] == (1, 2)
        raise asyncio.CancelledError
    async def send(*args):
        await asyncio.Event().wait()
    async def sleep(delay):
        assert delay == 1
    monkeypatch.setattr(worker, "_receive", receive)
    monkeypatch.setattr(worker, "_send", send)
    monkeypatch.setattr(child_module.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await worker._connection("spot")
    assert worker.states["spot"]["connected"] is False


def test_reader_coalesces_to_one_callback_and_never_loses_quiet_market(monkeypatch):
    import threading

    from test_gate_bbo import ready_runtime
    rt = ready_runtime(monkeypatch)
    callbacks = []
    class Loop:
        def call_soon_threadsafe(self, *args):
            callbacks.append(args)
    packets = iter([
        {"quotes": {("spot", "BTC_USDT"): (1,), ("future", "BTC_USDT"): (2,)},
         "connections": {}, "cpu_seconds": 1},
        *[{"quotes": {("spot", "BTC_USDT"): (i,)}, "connections": {}, "cpu_seconds": 2}
          for i in range(3, 1003)],
    ])
    class Incoming:
        def get(self, timeout):
            try:
                return next(packets)
            except StopIteration:
                raise EOFError from None
    rt._read_results(Incoming(), threading.Event(), Loop(), rt._epoch)
    assert len(callbacks) == 2  # One drain, one EOF notification, never 1001 queued callbacks.
    assert len(rt._mail["quotes"]) == 2
    callbacks[0][0](*callbacks[0][1:])
    assert rt._quotes[("future", "BTC_USDT")] == (2,)
    assert rt._quotes[("spot", "BTC_USDT")] == (1002,)
    assert rt.batches == 1001


def test_subscription_removal_prunes_pending_mail_and_cache(monkeypatch):
    from test_gate_bbo import ready_runtime
    rt = ready_runtime(monkeypatch)
    rt._mail = {"quotes": dict(rt._quotes)}
    rt.set_subscriptions({"spot": [], "future": ["BTC_USDT"]})
    assert ("spot", "BTC_USDT") not in rt._mail["quotes"]
    assert ("spot", "BTC_USDT") not in rt._quotes
    assert rt.get_fresh("spot", "BTC_USDT") is None


async def test_lifespan_binds_config_and_closes_both_runtimes(monkeypatch):
    from unittest.mock import AsyncMock, Mock

    from app import main
    from app.exchanges.gate import GateAdapter
    starts, closes = [], []
    async def start(self):
        starts.append(self)
    async def close(self):
        closes.append(self)
    monkeypatch.setattr(module.GateBboRuntime, "start", start)
    monkeypatch.setattr(module.GateBboRuntime, "aclose", close)
    monkeypatch.setattr(main, "_start_background_task", lambda *a, **kw: None)
    adapter = GateAdapter(client=Mock(aclose=AsyncMock()))
    monkeypatch.setattr(main, "default_exchange_adapters", lambda: [adapter])
    app = main.create_app(settings=Settings(database_url="sqlite:///:memory:",
        gate_bbo_enabled=True, gate_batch_quotes_executable=True), start_collector=True)
    async with app.router.lifespan_context(app):
        rt = app.state.gate_bbo_runtime
        assert starts == [rt]
        assert adapter.bbo_runtime is rt and adapter.batch_quotes_executable is True
    assert closes == [rt]
