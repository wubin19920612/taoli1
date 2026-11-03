import asyncio
import json
from datetime import UTC, datetime
from itertools import pairwise
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import config
from app.exchanges import hyperliquid as hl_module
from app.exchanges import hyperliquid_bbo as bbo_module
from app.exchanges.hyperliquid import HyperliquidAdapter
from app.exchanges.hyperliquid_bbo import HyperliquidBboStream
from app.services.spread_engine import _has_executable_book

EXCHANGE_TIME = 1790092800123


def message(coin="BTC", *, bid="100", ask="102", bid_size="2", ask_size="3"):
    return json.dumps({
        "channel": "bbo",
        "data": {"coin": coin, "time": EXCHANGE_TIME, "bbo": [
            None if bid is None else {"px": bid, "sz": bid_size, "n": 1},
            None if ask is None else {"px": ask, "sz": ask_size, "n": 1},
        ]},
    })


def payload(*coins):
    return [
        {"universe": [{"name": coin} for coin in coins]},
        [{"midPx": "101", "markPx": "103", "oraclePx": "99",
          "funding": "0.00001", "dayNtlVlm": "12345"} for _ in coins],
    ]


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(bbo_module, "monotonic", lambda: now[0])
    return now


def test_bbo_parse_exact_coins_and_timestamp(clock):
    stream = HyperliquidBboStream()
    stream._connected = True
    stream.set_coins(["BTC", "xyz:TSLA", "io:ANTH"])
    for coin in ("BTC", "xyz:TSLA", "io:ANTH"):
        stream.handle_message(message(coin))
        assert stream.get_fresh(coin) == (100, 2, 102, 3, EXCHANGE_TIME, 1000)
    assert stream.get_fresh("TSLA") is None
    assert stream.stats()["fresh_count"] == 3
    assert stream.stats()["msgs_last_5m"] == 3


@pytest.mark.parametrize("kwargs", [
    {"bid": None}, {"ask": None}, {"bid": "103"}, {"bid": "102"},
    {"bid": "0"}, {"bid": "NaN"}, {"ask": "Infinity"},
    {"bid_size": "0"}, {"ask_size": "-1"}, {"bid": "bad"},
])
def test_bbo_invalid_update_replaces_previously_executable_book(kwargs):
    stream = HyperliquidBboStream()
    stream._connected = True
    stream.set_coins(["BTC"])
    stream.handle_message(message())
    assert stream.get_fresh("BTC") is not None
    stream.handle_message(message(**kwargs))
    assert stream.get_fresh("BTC") is None


@pytest.mark.parametrize("raw", [
    "{", "null", "[]", "1", b"\xff", '{"channel":"pong"}',
    '{"channel":"bbo","data":null}',
    '{"channel":"bbo","data":{"coin":[],"bbo":[]}}',
])
def test_bbo_bad_or_unrelated_messages_do_not_escape(raw):
    stream = HyperliquidBboStream()
    stream.set_coins(["BTC"])
    stream.handle_message(raw)
    assert stream.get_fresh("BTC") is None


@pytest.mark.parametrize("bad_time", [
    None, True, "bad", 0, -1, 10**30, float("inf"),
    946_684_799_999, 4_102_444_800_001, 253_402_300_799_999,
])
def test_bbo_invalid_exchange_time_cannot_be_executable(bad_time):
    stream = HyperliquidBboStream()
    stream._connected = True
    stream.set_coins(["BTC"])
    stream.handle_message(message())
    assert stream.get_fresh("BTC") is not None
    data = json.loads(message())
    data["data"]["time"] = bad_time
    stream.handle_message(json.dumps(data))
    assert stream.get_fresh("BTC") is None


@pytest.mark.parametrize("timestamp", [946_684_800_000, 4_102_444_800_000])
def test_bbo_exchange_time_range_boundaries_are_executable(timestamp):
    stream = HyperliquidBboStream()
    stream._connected = True
    stream.set_coins(["BTC"])
    data = json.loads(message())
    data["data"]["time"] = timestamp
    stream.handle_message(json.dumps(data))
    assert stream.get_fresh("BTC")[4] == timestamp


def test_bbo_liveness_boundary_removal_and_bounded_statistics(clock):
    stream = HyperliquidBboStream(max_coins=2, max_age_seconds=10)
    stream._connected = True
    stream.set_coins(["BTC", "BTC", "xyz:TSLA"])
    stream.handle_message(message())
    stream.handle_message(message("xyz:TSLA"))
    stream.handle_message(message("io:ANTH"))
    assert stream.get_fresh("io:ANTH") is None
    clock[0] += 10
    assert stream.get_fresh("BTC") is not None
    clock[0] += 0.001
    assert stream.get_fresh("BTC") is None
    stream.set_coins(["xyz:TSLA"])
    stream.set_coins(["BTC"])
    assert stream.get_fresh("BTC") is None
    clock[0] += 301
    assert stream.stats()["msgs_last_5m"] == 0


@pytest.mark.parametrize("reverse", [False, True])
def test_bbo_cap_uses_stable_order_and_retains_subscribed_coins(reverse):
    stream = HyperliquidBboStream(max_coins=2)
    coins = ["xyz:TSLA", "io:ANTH", "ETH", "BTC", "BTC", "@1", "BTC/USDC", None]
    stream.set_coins(reversed(coins) if reverse else iter(coins))
    assert stream._coins == {"BTC", "ETH"}
    stream._changed.clear()
    stream.set_coins(reversed(coins))
    assert not stream._changed.is_set()

    # Already sent subscriptions take priority even over alphabetically earlier additions.
    stream._subscribed = {"io:ANTH", "xyz:TSLA"}
    stream.set_coins(coins)
    assert stream._coins == {"io:ANTH", "xyz:TSLA"}
    stream._changed.clear()
    stream.set_coins(reversed(coins))
    assert not stream._changed.is_set()
    stream.set_coins(["xyz:TSLA", "ETH", "BTC"])
    assert stream._coins == {"xyz:TSLA", "BTC"}


async def test_hyperliquid_bbo_overlay_preserves_other_fields_and_avoids_rest(clock):
    stream = HyperliquidBboStream()
    stream._connected = True
    stream.set_coins(["BTC", "xyz:TSLA", "cash:TSLA", "io:ANTH"])
    for coin in ("BTC", "xyz:TSLA", "io:ANTH"):
        stream.handle_message(message(coin))
    adapter = HyperliquidAdapter(client=object(), bbo_stream=stream)
    adapter.post_json = AsyncMock(side_effect=[
        [{"name": "xyz"}, {"name": "cash"}, {"name": "io"}],
        payload("BTC"), [], payload("xyz:TSLA"), payload("cash:TSLA"), payload("io:ANTH"),
    ])
    adapter.fetch_order_book = AsyncMock(side_effect=AssertionError("unexpected REST book"))
    rows = await adapter.fetch_future_tickers()
    for row in rows:
        original = adapter._parse_perp_payload(payload(row.raw_symbol))[0]
        if row.raw_symbol == "cash:TSLA":
            assert row.quote_kind == "reference"
            continue
        assert (row.bid, row.bid_size, row.ask, row.ask_size) == (100, 2, 102, 3)
        assert row.book_timestamp == row.upstream_timestamp == datetime.fromtimestamp(
            EXCHANGE_TIME / 1000, UTC,
        )
        assert row.quote_time_source == "upstream"
        assert row.data_source == "Hyperliquid metaAndAssetCtxs + WS bbo"
        assert not row.is_estimated and row.estimated_fields == []
        assert _has_executable_book(row)
        for field in ("raw_symbol", "dex", "funding_rate_pct", "funding_interval_hours",
                      "volume_24h_usdt", "mark_price", "index_price", "reference_price",
                      "original_quote_currency", "contract_size_multiplier"):
            assert getattr(row, field) == getattr(original, field)
    adapter.fetch_order_book.assert_not_called()
    assert adapter.post_json.await_count == 6


@pytest.mark.parametrize("case", ["expired", "crossed", "null", "disabled"])
async def test_hyperliquid_bbo_invalid_or_disabled_keeps_reference(case, clock):
    stream = HyperliquidBboStream()
    stream._connected = True
    stream.set_coins(["BTC"])
    stream.handle_message(message(
        bid=None if case == "null" else "104" if case == "crossed" else "100",
    ))
    if case == "expired":
        clock[0] += 11
    adapter = HyperliquidAdapter(client=object(), bbo_stream=None if case == "disabled" else stream)
    adapter.post_json = AsyncMock(side_effect=[[], payload("BTC"), []])
    [row] = await adapter.fetch_future_tickers()
    assert row.quote_kind == "reference"
    assert row.is_estimated and row.estimated_fields == ["bid", "ask"]
    assert (row.bid, row.ask) == (101, 101)
    assert not _has_executable_book(row)


@pytest.mark.parametrize("age,active,connected,expected", [
    (60, True, True, "book"),
    (120, True, True, "book"),
    (120.001, True, True, "reference"),
    (10, False, True, "book"),
    (10.001, False, True, "reference"),
    (0, True, False, "reference"),
], ids=["quiet-60s", "quote-at-120s", "quote-over-120s", "liveness-at-10s",
        "all-silent-over-10s", "disconnected"])
async def test_hyperliquid_bbo_connection_and_quote_freshness(
    clock, age, active, connected, expected,
):
    stream = HyperliquidBboStream()
    stream._connected = True
    stream.set_coins(["BTC", "xyz:TSLA"])
    stream.handle_message(message())
    stream.handle_message(message("xyz:TSLA"))
    clock[0] += age
    if active:
        stream.handle_message(message("xyz:TSLA"))
    stream._connected = connected
    adapter = HyperliquidAdapter(client=object(), bbo_stream=stream)
    adapter.post_json = AsyncMock(side_effect=[
        [{"name": "xyz"}], payload("BTC"), [], payload("xyz:TSLA"),
    ])
    rows = {row.raw_symbol: row for row in await adapter.fetch_future_tickers()}
    assert rows["BTC"].quote_kind == expected
    assert _has_executable_book(rows["BTC"]) is (expected == "book")
    other_expected = "book" if connected and (active or age <= 10) else "reference"
    assert rows["xyz:TSLA"].quote_kind == other_expected
    assert stream.stats()["fresh_count"] == (expected == "book") + (other_expected == "book")
    # Another market's traffic never refreshes this quote's receive time.
    assert stream._quotes["BTC"][5] == 1000


@pytest.mark.parametrize("error", [OSError, OverflowError, ValueError])
@pytest.mark.parametrize("bad_coin", ["BTC", "io:ANTH"])
async def test_hyperliquid_bbo_timestamp_failure_isolated_to_reference_row(
    monkeypatch, clock, error, bad_coin,
):
    stream = HyperliquidBboStream()
    stream._connected = True
    coins = ["BTC", "io:ANTH", "xyz:TSLA"]
    stream.set_coins(coins)
    for coin in coins:
        stream.handle_message(message(coin))
    quote = stream._quotes[bad_coin]
    stream._quotes[bad_coin] = (*quote[:4], EXCHANGE_TIME + 1, quote[5])
    parse_time = hl_module.parse_datetime_ms

    def parse_with_failure(value):
        if value == EXCHANGE_TIME + 1:
            raise error("invalid timestamp")
        return parse_time(value)

    monkeypatch.setattr(hl_module, "parse_datetime_ms", parse_with_failure)
    adapter = HyperliquidAdapter(client=object(), bbo_stream=stream)
    adapter.fetch_order_book = AsyncMock(side_effect=AssertionError("unexpected REST book"))
    originals = adapter._parse_perp_payload(payload(*coins))
    rows = await adapter._attach_priority_order_books(originals)
    assert len(rows) == len(coins)
    for original, row in zip(originals, rows, strict=True):
        if row.raw_symbol == bad_coin:
            assert row == original
            assert row.quote_kind == "reference"
        else:
            assert row.quote_kind == "book"
            assert _has_executable_book(row)
    adapter.fetch_order_book.assert_not_called()


async def test_hyperliquid_bbo_stale_priority_falls_back_to_existing_l2book(clock):
    stream = HyperliquidBboStream()
    stream._connected = True
    stream.set_coins(["io:ANTH"])
    stream.handle_message(message("io:ANTH"))
    clock[0] += 11
    adapter = HyperliquidAdapter(client=object(), bbo_stream=stream)
    adapter.post_json = AsyncMock(side_effect=[[], payload("io:ANTH"), [], {
        "time": EXCHANGE_TIME,
        "levels": [[{"px": "98", "sz": "4"}], [{"px": "105", "sz": "5"}]],
    }])
    [row] = await adapter.fetch_future_tickers()
    assert (row.bid, row.ask) == (98, 105)
    assert row.quote_kind == "book" and "l2Book" in row.data_source
    assert adapter.post_json.call_args.args[1] == {"type": "l2Book", "coin": "io:ANTH"}


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.closed = False

    async def send(self, raw):
        self.sent.append((bbo_module.monotonic(), json.loads(raw)))

    async def recv(self):
        item = await self.incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True


async def eventually(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.005)


async def test_bbo_websocket_incremental_rate_limit_reconnect_and_shutdown(monkeypatch):
    first, second = FakeWebSocket(), FakeWebSocket()
    sockets = iter([first, second])
    connects = []

    def connect(url, **kwargs):
        connects.append(url)
        return next(sockets)

    monkeypatch.setattr(bbo_module, "websocket_connect", connect)
    stream = HyperliquidBboStream()
    stream.set_coins(["BTC", "xyz:TSLA"])
    stop = asyncio.Event()
    task = asyncio.create_task(stream.run(stop))
    try:
        await eventually(lambda: len(first.sent) == 2)
        assert connects == ["wss://api.hyperliquid.xyz/ws"]
        assert [m for _, m in first.sent] == [
            {"method": "subscribe", "subscription": {"type": "bbo", "coin": coin}}
            for coin in ("BTC", "xyz:TSLA")
        ]
        first.incoming.put_nowait(message())
        await eventually(lambda: stream.get_fresh("BTC") is not None)
        stream.set_coins(["xyz:TSLA", "io:ANTH"])
        await eventually(lambda: len(first.sent) == 4)
        assert first.sent[2][1] == {
            "method": "unsubscribe", "subscription": {"type": "bbo", "coin": "BTC"},
        }
        assert first.sent[3][1]["subscription"]["coin"] == "io:ANTH"
        for (before, _), (after, _) in pairwise(first.sent):
            assert after - before >= 0.05
        first.incoming.put_nowait(message("xyz:TSLA"))
        await eventually(lambda: stream.get_fresh("xyz:TSLA") is not None)
        first.incoming.put_nowait(ConnectionError("lost"))
        await eventually(lambda: first.closed)
        assert stream.get_fresh("xyz:TSLA") is None
        assert "xyz:TSLA" in stream._quotes  # Disconnected cache is never executable.
        await eventually(lambda: len(second.sent) == 2)
        assert stream._quotes == {}  # A new connection must discard the previous books.
        assert stream._last_message_at == float("-inf")
        assert {m["subscription"]["coin"] for _, m in second.sent} == {"xyz:TSLA", "io:ANTH"}
        assert all(m["method"] == "subscribe" for _, m in second.sent)
        second.incoming.put_nowait(message("io:ANTH"))
        await eventually(lambda: stream.get_fresh("io:ANTH") is not None)
        assert stream.get_fresh("xyz:TSLA") is None
        assert stream.stats()["connected"] is True
        assert stream.stats()["subscribed"] == 2
        assert stream.stats()["reconnects"] == 1
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
    assert second.closed and not stream.stats()["connected"]
    assert stream.stats()["subscribed"] == 0


@pytest.mark.parametrize("value,expected", [(None, True), ("0", False), ("false", False), ("true", True)])
def test_bbo_settings_environment(monkeypatch, value, expected):
    monkeypatch.setattr(config, "find_dotenv", lambda **kwargs: "")
    monkeypatch.delenv("HL_BBO_ENABLED", raising=False)
    if value is not None:
        monkeypatch.setenv("HL_BBO_ENABLED", value)
    monkeypatch.setenv("HL_BBO_MAX_AGE_SECONDS", "2.5")
    monkeypatch.setenv("HL_BBO_MAX_COINS", "12")
    settings = config.get_settings.__wrapped__()
    assert settings.hl_bbo_enabled is expected
    assert settings.hl_bbo_max_age_seconds == 2.5
    assert settings.hl_bbo_max_coins == 12


@pytest.mark.parametrize("values", [
    {"hl_bbo_max_age_seconds": 0}, {"hl_bbo_max_age_seconds": float("nan")},
    {"hl_bbo_max_age_seconds": float("inf")}, {"hl_bbo_max_coins": 0},
    {"hl_bbo_max_coins": -1}, {"hl_bbo_max_coins": 1.5},
])
def test_bbo_settings_reject_invalid_limits(values):
    with pytest.raises(ValueError):
        config.Settings(**values)


async def test_bbo_heartbeat_and_periodic_json_stats(monkeypatch):
    ws = FakeWebSocket()
    monkeypatch.setattr(bbo_module, "websocket_connect", lambda *a, **kw: ws)
    log = Mock()
    monkeypatch.setattr(bbo_module.logger, "info", log)
    stream = HyperliquidBboStream()
    assert stream.heartbeat_seconds < 60
    assert stream.stats_interval_seconds == 300
    stream.heartbeat_seconds = 0.1
    stream.stats_interval_seconds = 0.1
    stream.set_coins(["BTC"])
    stop = asyncio.Event()
    task = asyncio.create_task(stream.run(stop))
    try:
        await eventually(lambda: len(ws.sent) == 1)
        ws.incoming.put_nowait(message())
        await eventually(lambda: any(m == {"method": "ping"} for _, m in ws.sent))
        await eventually(lambda: log.called)
        stats = json.loads(log.call_args.args[0])
        assert stats == {"hl_bbo": 1, "connected": True, "subscribed": 1,
                         "fresh_count": 1, "msgs_last_5m": 1, "reconnects": 0}
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
    assert ws.closed


async def test_bbo_exponential_backoff_and_cancellation(monkeypatch):
    delays = []
    real_sleep = asyncio.sleep
    ready = asyncio.Event()

    def fail(*args, **kwargs):
        raise ConnectionError("offline fake")

    async def sleep(delay):
        if delay == 300:  # Statistics worker stays idle for this test.
            await asyncio.Event().wait()
        delays.append(delay)
        if len(delays) == 6:
            ready.set()
            await asyncio.Event().wait()
        await real_sleep(0)

    monkeypatch.setattr(bbo_module, "websocket_connect", fail)
    monkeypatch.setattr(bbo_module.asyncio, "sleep", sleep)
    stream = HyperliquidBboStream()
    stream.set_coins(["BTC"])
    task = asyncio.create_task(stream.run(asyncio.Event()))
    try:
        await asyncio.wait_for(ready.wait(), 2)
        assert delays == [1, 2, 5, 10, 30, 30]
        assert stream.stats()["reconnects"] == 5
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.stats()["connected"] is False


async def test_bbo_rate_limit_over_full_second_and_early_timer_wakeups(monkeypatch, clock):
    real_sleep = asyncio.sleep

    async def early_sleep(delay):
        clock[0] += max(delay / 2, 0.0001)
        await real_sleep(0)

    monkeypatch.setattr(bbo_module.asyncio, "sleep", early_sleep)
    stream = HyperliquidBboStream()
    stream.set_coins(f"COIN{i}" for i in range(25))
    ws = FakeWebSocket()
    sender = asyncio.create_task(stream._send_subscriptions(ws))
    try:
        async with asyncio.timeout(2):
            while len(ws.sent) < 25:
                await real_sleep(0)
        times = [t for t, _ in ws.sent]
        assert all(b - a >= 0.05 for a, b in pairwise(times))
        assert all(sum(t <= other < t + 1 for other in times) <= 20 for t in times)
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def test_hyperliquid_bbo_metadata_refresh_updates_coins_without_spot(monkeypatch):
    stream = HyperliquidBboStream()
    stream._connected = True
    set_coins = Mock(wraps=stream.set_coins)
    monkeypatch.setattr(stream, "set_coins", set_coins)
    adapter = HyperliquidAdapter(client=object(), bbo_stream=stream)
    adapter.post_json = AsyncMock(side_effect=[
        [], payload("BTC", "ETH"), [],
        [], payload("ETH", "xyz:TSLA"), [],
        [{"universe": []}, []],
    ])
    await adapter.fetch_future_tickers()
    stream.handle_message(message())
    assert stream.get_fresh("BTC") is not None
    await adapter.fetch_future_tickers()
    assert stream.get_fresh("BTC") is None
    stream.handle_message(message("xyz:TSLA"))
    assert stream.get_fresh("xyz:TSLA") is not None
    await adapter.fetch_spot_tickers()
    assert set_coins.call_count == 2


async def test_hyperliquid_bbo_failed_dex_retains_coins_until_successful_refresh(clock):
    stream = HyperliquidBboStream()
    stream._connected = True
    adapter = HyperliquidAdapter(client=object(), bbo_stream=stream)
    dex_names = [{"name": "xyz"}, {"name": "cash"}]
    responses = {
        "main": payload("BTC", "ETH"),
        "xyz": payload("xyz:TSLA", "xyz:OLD"),
        "cash": payload("cash:TSLA", "cash:OLD"),
    }

    async def post_json(url, request):
        if request["type"] == "perpDexs":
            if isinstance(dex_names, Exception):
                raise dex_names
            return dex_names
        if request["type"] == "predictedFundings":
            return []
        assert request["type"] == "metaAndAssetCtxs"
        result = responses[request.get("dex", "main")]
        if isinstance(result, Exception):
            raise result
        return result

    adapter.post_json = AsyncMock(side_effect=post_json)
    await adapter.fetch_future_tickers()
    assert stream._coins == {"BTC", "ETH", "xyz:TSLA", "xyz:OLD", "cash:TSLA", "cash:OLD"}
    stream.handle_message(message("xyz:TSLA"))
    stream.handle_message(message("cash:TSLA"))

    responses.update({
        "main": payload("ETH", "SOL"),
        "xyz": RuntimeError("DEX unavailable"),
        "cash": payload("cash:NEW"),
    })
    rows = await adapter.fetch_future_tickers()
    assert {row.raw_symbol for row in rows} == {"ETH", "SOL", "cash:NEW"}
    assert stream._coins == {"ETH", "SOL", "xyz:TSLA", "xyz:OLD", "cash:NEW"}
    assert stream.get_fresh("xyz:TSLA") is not None
    assert stream.get_fresh("cash:TSLA") is None

    dex_names = RuntimeError("discovery unavailable")
    await adapter.fetch_future_tickers()
    assert stream._coins == {"ETH", "SOL", "xyz:TSLA", "xyz:OLD", "cash:NEW"}
    dex_names = [{"name": "xyz"}, {"name": "cash"}]
    responses.update({"xyz": payload("xyz:NEW"), "cash": payload()})
    await adapter.fetch_future_tickers()
    assert stream._coins == {"ETH", "SOL", "xyz:NEW"}
    assert stream.get_fresh("xyz:TSLA") is None


@pytest.mark.parametrize("collector,workers,enabled", [
    (False, False, True), (False, True, True), (True, True, False), (True, True, True),
])
async def test_hyperliquid_bbo_lifespan_gating_binding_and_cancellation(
    monkeypatch, collector, workers, enabled,
):
    from app import main

    factories = {}
    start_task = main._start_background_task
    monkeypatch.setattr(main, "_start_background_task", lambda supervisor, factory, *, name:
                        factories.setdefault(name, factory))
    adapter = HyperliquidAdapter(client=Mock(aclose=AsyncMock()))
    monkeypatch.setattr(main, "default_exchange_adapters", lambda: [adapter])
    ws = FakeWebSocket()
    connect = Mock(return_value=ws)
    monkeypatch.setattr(bbo_module, "websocket_connect", connect)
    app = main.create_app(
        settings=config.Settings(database_url="sqlite:///:memory:", hl_bbo_enabled=enabled,
                                 hl_bbo_max_age_seconds=2.5, hl_bbo_max_coins=12),
        start_collector=collector, start_background_workers=workers,
    )
    async with app.router.lifespan_context(app):
        should_start = collector and workers and enabled
        assert ("hyperliquid-bbo" in factories) is should_start
        if should_start:
            stream = app.state.hyperliquid_bbo_stream
            assert adapter.bbo_stream is stream
            assert (stream.max_coins, stream.max_age_seconds) == (12, 2.5)
            connect.assert_not_called()  # Construction alone never opens a socket.
            stream.set_coins(["BTC"])
            start_task(app.state.task_supervisor, factories["hyperliquid-bbo"], name="hyperliquid-bbo")
            await eventually(lambda: bool(ws.sent))
        else:
            assert app.state.hyperliquid_bbo_stream is None
            assert adapter.bbo_stream is None
            connect.assert_not_called()
    if should_start:
        assert ws.closed
        assert app.state.hyperliquid_bbo_stream.stats()["connected"] is False


async def test_bbo_no_coins_or_already_stopped_never_connect(monkeypatch):
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(bbo_module, "websocket_connect", connect)
    stream = HyperliquidBboStream()
    stop = asyncio.Event()
    task = asyncio.create_task(stream.run(stop))
    await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, 2)
    stream.set_coins(["BTC"])
    await stream.run(stop)
    connect.assert_not_called()
