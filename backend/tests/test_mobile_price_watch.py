import asyncio
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from test_mobile_api import mobile_app  # noqa: F401
from test_mobile_pins import login

from app.core.config import Settings
from app.db.database import connect_database
from app.db.repositories import MobilePriceWatchRepository
from app.main import create_app
from app.models.market import MarketSnapshot
from app.models.mobile import PriceWatchUpdate, PushKinds
from app.services.fcm import SendResult
from app.services.mobile_price_watch import MobilePriceWatch, PriceWatchRevisionConflict

PATH = "/api/mobile/price-watch"
START = datetime(2026, 1, 1, tzinfo=UTC)


def book(price, stamp=START, *, exchange="binance", symbol="BTC", **fields):
    values = dict(symbol=symbol + "USDT", base=symbol, quote="USDT", raw_symbol=symbol + "USDT",
                  market_type="spot", exchange=exchange, bid=price, ask=price, timestamp=stamp)
    return MarketSnapshot(**(values | fields))


def update(*alerts, symbols=("BTC",), revision=0):
    return PriceWatchUpdate(symbols=list(symbols), alerts=list(alerts), revision=revision)


@pytest.fixture
async def watch(mobile_app):
    app, _ = mobile_app
    service = app.state.mobile_price_watch
    service.clock = lambda: START
    service.push = SimpleNamespace(price_watch_devices=AsyncMock(return_value=[]), queue_price_watch=Mock())
    return service


async def test_spot_priority_exact_market_midpoint_and_staleness(watch, monkeypatch):
    monkeypatch.setattr(watch.store, "get_all_markets", Mock(side_effect=AssertionError("full market copy")))
    venues = ["binance", "okx", "bybit", "bitget", "gate"]
    books = [book(100 + i, exchange=ex, ask=102 + i) for i, ex in enumerate(venues)]
    ignored = [book(100, market_type="future"), book(123, raw_symbol="WBTCUSDT"),
               book(80, raw_symbol="BTCUSDC", quote="USDC"), book(90, exchange="hyperliquid")]
    for i, ex in enumerate(venues):
        watch.store.set_all_markets(ignored + list(reversed(books[i:])))
        q, missing = (await watch.quotes(["BTC", "ETH"])).quotes
        assert (q.exchange, q.market_type, q.price, q.bid, q.ask) == (ex, "spot", 101+i, 100+i, 102+i)
        assert not q.stale and q.age_seconds == 0
        assert missing.price is None and missing.error == "暂无行情"
    watch.store.set_all_markets([book(50, START - timedelta(days=1)), books[1]])
    q, = (await watch.quotes(["BTC"])).quotes
    assert q.exchange == "binance" and q.stale and q.age_seconds == 86400


async def sample(watch, seconds, price, *, stale=False):
    now = START + timedelta(seconds=seconds)
    watch.clock = lambda: now
    watch.store.set_all_markets([book(price, START - timedelta(days=1) if stale else now)])
    await watch.tick()


async def test_history_boundaries_restart_and_removal(watch):
    await watch.save(update())
    for seconds, price in [(0, 100), (5, 200), (10, 110), (2700, 120), (3300, 150), (3600, 180)]:
        await sample(watch, seconds, price)
    q, = (await watch.quotes()).quotes
    assert (q.change_5m_pct, q.change_15m_pct, q.change_1h_pct) == (20, 50, 80)
    assert len(watch.history.points["BTC"]) == 5
    resumed = MobilePriceWatch(watch.repo, watch.store, watch.load_risk, watch.push, watch.clock)
    assert (await resumed.quotes()).quotes[0].change_1h_pct is None
    await sample(watch, 8000, 300)
    assert (await watch.quotes()).quotes[0].change_1h_pct is None
    assert len(watch.history.points["BTC"]) == 1
    await watch.save(update(symbols=[], revision=1))
    assert not watch.history.points


@pytest.mark.parametrize("kind,inside,outside", [("above", 99.91, 99.90), ("below", 100.09, 100.10)])
async def test_price_state_machine_history_and_push(watch, mobile_app, kind, inside, outside):
    await watch.save(update(dict(symbol="btc", kind=kind, price=100, rearm=True)))
    await sample(watch, 0, 100, stale=True)
    assert not watch.history.points["BTC"]
    assert (await watch.get()).alerts[0].state == "armed"
    await sample(watch, 5, 100)
    alert, = (await watch.get()).alerts
    assert alert.state == "rearm_wait" and alert.enabled and alert.last_trigger_price == 100
    assert alert.last_triggered_at == START + timedelta(seconds=5)
    for sec, value, stale, state in [(10, inside, False, "rearm_wait"), (15, outside, True, "rearm_wait"),
                                     (20, outside, False, "armed"), (25, 100, False, "rearm_wait")]:
        await sample(watch, sec, value, stale=stale)
        assert (await watch.get()).alerts[0].state == state
    assert watch.push.queue_price_watch.call_count == 2
    assert (await watch.get()).revision == 4
    events = await mobile_app[0].state.mobile_alert_repo.list(0, 10)
    assert len(events) == 2
    assert {e["kind"] for e in events} == {"price_watch"}
    assert {e["level"] for e in events} == {"urgent"}
    assert events[0]["body"] == "现价 100 · 币安现货" and events[0]["symbol"] == "BTC"


@pytest.mark.parametrize("direction,price,fires", [
    ("up", 100.1, True), ("up", 99.9, False), ("down", 99.9, True), ("down", 100.1, False),
    ("both", 99.9, True), ("both", 100.1, True),
])
async def test_change_threshold_direction_and_cooldown(watch, direction, price, fires):
    await watch.save(update(dict(symbol="BTC", kind="change", change_pct=0.1, window_minutes=1,
                                 direction=direction, rearm=True)))
    await sample(watch, 0, 100)
    await sample(watch, 59, price, stale=True)
    assert watch.push.queue_price_watch.call_count == 0
    await sample(watch, 60, price)
    assert watch.push.queue_price_watch.call_count == int(fires)
    alert, = (await watch.get()).alerts
    if fires:
        assert alert.state == "cooldown" and alert.cooldown_until == START + timedelta(seconds=120)
        await sample(watch, 119, price * 1.1)
        assert watch.push.queue_price_watch.call_count == 1
        await sample(watch, 120, price)
        alert, = (await watch.get()).alerts
        assert alert.state == "armed" and alert.cooldown_until is None
    else:
        assert alert.state == "armed"


async def test_http_merge_reset_reenable_and_readonly_permissions(mobile_app, watch):
    app, client = mobile_app
    assert (await client.get(PATH)).status_code == 401
    await login(client, "mobile_readonly", int(time.time() // 30))
    assert (await client.get(PATH)).json() == {"symbols": [], "alerts": [], "revision": 0}
    body = {"revision": 0, "symbols": ["btc", "ETH", "BTC"], "alerts": [
        {"symbol": "btc", "kind": "above", "price": 100, "state": "cooldown", "last_trigger_price": 999},
    ]}
    response = await client.post(PATH, json=body)
    assert response.status_code == 200, response.text
    config = response.json()
    alert, = config["alerts"]
    assert config["symbols"] == ["BTC", "ETH"]
    assert alert["state"] == "armed" and alert["last_trigger_price"] is None
    ticks = iter(range(5, 20))
    app.state.price_watch_limits["save"]._clock = lambda: time.monotonic() + next(ticks) * 2
    await sample(watch, 0, 101)
    assert config["revision"] == 1
    stale = await client.post(PATH, json=config | {"symbols": ["BTC", "ETH", "SOL"]})
    assert stale.status_code == 409
    assert stale.json() == {"detail": "提醒已在别处更新，请刷新后再保存"}
    await sample(watch, 5, 101)
    assert watch.push.queue_price_watch.call_count == 1
    config = (await client.get(PATH)).json()
    assert config["revision"] == 2 and config["symbols"] == ["BTC", "ETH"]
    alert, = config["alerts"]
    assert alert["state"] == "triggered" and not alert["enabled"]
    alert.update(state="armed", last_trigger_price=1, last_triggered_at=None)
    config = (await client.post(PATH, json=config)).json()
    saved = config["alerts"][0]
    assert saved["state"] == "triggered" and saved["last_trigger_price"] == 101
    assert config["revision"] == 3
    saved["enabled"] = True
    config = (await client.post(PATH, json=config)).json()
    saved = config["alerts"][0]
    assert saved["state"] == "armed" and saved["enabled"] and saved["last_trigger_price"] == 101
    assert config["revision"] == 4
    saved["price"] = 150
    saved = (await client.post(PATH, json=config)).json()["alerts"][0]
    assert saved["state"] == "armed" and saved["last_triggered_at"] is None
    quotes = (await client.get(PATH + "/quotes")).json()["quotes"]
    assert [q["symbol"] for q in quotes] == ["BTC", "ETH"]
    for url in [PATH + "/quotes", PATH + "/", "/api/settings/risk"]:
        assert (await client.post(url, json={})).status_code == 403
    assert (await client.get("/api/settings/risk")).status_code == 403


async def test_invalid_inputs(mobile_app):
    _, client = mobile_app
    await login(client, "mobile_readonly", int(time.time() // 30))
    rule = dict(id=str(uuid4()), symbol="BTC", kind="above", price=100)
    change = dict(symbol="BTC", kind="change", change_pct=1, window_minutes=5)
    for symbols, alerts in [
        ([str(i) for i in range(21)], []), (["非法"], []), (["A"*21], []), (["BTC"], [rule]*51),
        (["BTC"], [rule, rule]), (["ETH"], [rule]), (["BTC"], [rule | {"id": "bad"}]),
        (["BTC"], [rule | {"price": 0}]), (["BTC"], [rule | {"kind": "bad"}]),
        (["BTC"], [rule | {"price": None}]), (["BTC"], [change | {"change_pct": 0.09}]),
        (["BTC"], [change | {"change_pct": 51}]), (["BTC"], [change | {"window_minutes": 61}]),
        (["BTC"], [change | {"window_minutes": 0}]), (["BTC"], [change | {"direction": "bad"}]),
    ]:
        response = await client.post(PATH, json={"symbols": symbols, "alerts": alerts, "revision": 0})
        assert response.status_code == 422
        assert "无效" in response.json()["detail"]
    for symbols in [",".join(str(i) for i in range(21)), "", "USD/T"]:
        assert (await client.get(PATH + "/quotes", params={"symbols": symbols})).status_code == 422
    for fields in [{}, {"revision": -1}, {"revision": True}, {"revision": "0"}]:
        assert (await client.post(PATH, json={"symbols": [], "alerts": []} | fields)).status_code == 422


async def test_independent_limits_and_sanitized_failure(mobile_app, watch, monkeypatch):
    app, client = mobile_app
    await login(client, "mobile_readonly", int(time.time() // 30))
    body = {"symbols": [], "alerts": [], "revision": 0}
    assert (await client.post(PATH, json=body)).status_code == 200
    assert (await client.post(PATH, json=body)).status_code == 429
    started, release = asyncio.Event(), asyncio.Event()
    async def blocked(symbols):
        started.set()
        await release.wait()
        raise RuntimeError("private details")
    monkeypatch.setattr(watch, "quotes", blocked)
    seconds = [0]
    app.state.price_watch_limits["quotes"]._clock = lambda: seconds[0]
    task = asyncio.create_task(client.get(PATH + "/quotes"))
    await started.wait()
    seconds[0] = 2
    assert (await client.get(PATH + "/quotes")).status_code == 429
    release.set()
    response = await task
    assert response.status_code == 503 and response.json() == {"detail": "币价数据暂不可用"}
    assert (await client.get(PATH + "/quotes")).status_code == 503
    assert (await client.get(PATH + "/quotes")).status_code == 429
    for method in ["get", "save"]:
        monkeypatch.setattr(watch, method, AsyncMock(side_effect=RuntimeError("private details")))
    app.state.price_watch_limits["save"]._clock = lambda: time.monotonic() + 5
    for response in [await client.get(PATH), await client.post(PATH, json=body)]:
        assert response.status_code == 503 and response.json() == {"detail": "币价数据暂不可用"}


async def test_interleaved_worker_and_save_retry(tmp_path, watch, mobile_app, monkeypatch):
    # Force a save on the API connection between the worker's read and write.
    rule = dict(id=uuid4(), symbol="BTC", kind="below", price=123)
    await watch.save(update(rule))
    db = await connect_database(str(tmp_path / "radar.db"))
    try:
        api = MobilePriceWatch(MobilePriceWatchRepository(db), watch.store, watch.load_risk, watch.push)
        replace = watch.repo._write
        async def interleave(raw, payload, events):
            monkeypatch.setattr(watch.repo, "_write", replace)
            await api.save(update(rule, symbols=["ETH", "BTC"], revision=1))
            return await replace(raw, payload, events)
        monkeypatch.setattr(watch.repo, "_write", interleave)
        await sample(watch, 0, 120)
        saved = await api.get()
        assert saved.symbols == ["ETH", "BTC"] and saved.alerts[0].state == "triggered"
        assert watch.push.queue_price_watch.call_count == 1
        assert saved.revision == 3
        assert len(await mobile_app[0].state.mobile_alert_repo.list(0, 10)) == 1
    finally:
        await db.close()


async def test_save_conflicts_after_concurrent_one_shot_trigger(tmp_path, watch, monkeypatch):
    rule = dict(id=uuid4(), symbol="BTC", kind="above", price=100, enabled=True)
    await watch.save(update(rule))
    db = await connect_database(str(tmp_path / "radar.db"))
    try:
        api = MobilePriceWatch(MobilePriceWatchRepository(db), watch.store, watch.load_risk, watch.push)
        replace = api.repo._write

        async def trigger_during_post(raw, payload, events):
            monkeypatch.setattr(api.repo, "_write", replace)
            await sample(watch, 0, 125)
            return await replace(raw, payload, events)

        monkeypatch.setattr(api.repo, "_write", trigger_during_post)
        with pytest.raises(PriceWatchRevisionConflict):
            await api.save(update(rule, symbols=["BTC", "ETH"], revision=1))
        saved = await api.get()
        alert, = saved.alerts
        assert alert.state == "triggered" and not alert.enabled and alert.last_trigger_price == 125
        assert await watch.get() == saved
        assert saved.symbols == ["BTC"] and saved.revision == 2
        await sample(watch, 5, 125)
        assert watch.push.queue_price_watch.call_count == 1
    finally:
        await db.close()


async def test_save_entering_during_local_tick_preserves_trigger(watch, monkeypatch):
    rule = dict(id=uuid4(), symbol="BTC", kind="above", price=100, enabled=True)
    await watch.save(update(rule))
    checking, post_arrived = asyncio.Event(), asyncio.Event()
    load_risk = watch.load_risk

    async def pause_check():
        checking.set()
        await post_arrived.wait()
        return await load_risk()

    async def concurrent_post():
        await checking.wait()
        post_arrived.set()
        with pytest.raises(PriceWatchRevisionConflict):
            await watch.save(update(rule, symbols=["ETH", "BTC"], revision=1))

    monkeypatch.setattr(watch, "load_risk", pause_check)
    await asyncio.gather(sample(watch, 0, 101), concurrent_post())
    result = await watch.get()
    alert, = result.alerts
    assert result.symbols == ["BTC"] and alert.state == "triggered" and not alert.enabled
    assert result.revision == 2 and watch.push.queue_price_watch.call_count == 1


async def test_event_failure_rolls_back_trigger_and_can_retry(watch, tmp_path):
    await watch.save(update(dict(symbol="BTC", kind="above", price=10)))
    await watch.repo.db.execute("""
        CREATE TEMP TRIGGER fail_event BEFORE INSERT ON mobile_alert_events
        BEGIN SELECT RAISE(ABORT, 'unavailable'); END
    """)
    with pytest.raises(sqlite3.IntegrityError):
        await sample(watch, 0, 11)
    assert (await watch.get()).alerts[0].state == "armed"
    watch.push.queue_price_watch.assert_not_called()
    other = await connect_database(str(tmp_path / "radar.db"))
    try:
        await other.execute("PRAGMA busy_timeout=0")
        await other.execute("INSERT INTO app_settings(key, payload) VALUES ('other_setting', '{}')")
        await other.commit()
    finally:
        await other.close()
    await watch.repo.db.execute("DROP TRIGGER fail_event")
    await sample(watch, 5, 12)
    assert (await watch.get()).alerts[0].last_trigger_price == 12
    watch.push.queue_price_watch.assert_called_once()


@pytest.mark.parametrize("in_memory", [False, True], ids=["file", "memory"])
async def test_price_watch_rollback_preserves_pending_business_write(tmp_path, in_memory):
    path = ":memory:" if in_memory else str(tmp_path / "radar.db")
    app = create_app(settings=Settings(dashboard_auth_disabled=True, database_url=f"sqlite:///{path}"),
                     start_background_workers=False)
    async with app.router.lifespan_context(app):
        watch = app.state.mobile_price_watch
        main_db = app.state.alert_event_repo.db
        assert watch.repo.db is not main_db
        await watch.repo.db.execute("PRAGMA busy_timeout=0")
        await main_db.execute("""
            INSERT INTO mobile_alert_events(created_at, title, body, symbol, kind, level)
            VALUES (0, 'pending', 'pending', 'BTC', 'price_watch', 'urgent')
        """)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            await watch.save(update())
        assert main_db.in_transaction and not watch.repo.db.in_transaction
        await main_db.commit()
        assert (await watch.repo.db.execute_fetchall("SELECT title FROM mobile_alert_events"))[0][0] == "pending"
        assert (await watch.save(update())).revision == 1
    with pytest.raises(ValueError, match="no active connection"):
        await watch.repo.db.execute("SELECT 1")


def registered_devices(push):
    kinds = dict.fromkeys(["near_open", "near_close", "fill", "leg_alert", "big_spread", "stale"], False)
    assert PushKinds(**kinds).price_watch is True
    devices = [dict(token=str(i), kinds=preferences, last_error=None, session_hash="s", updated_at=0)
               for i, preferences in enumerate([{}, {"price_watch": True}, {"price_watch": False}])]
    push.repo = AsyncMock()
    push.repo.scan.return_value = devices
    push.repo.get.side_effect = lambda token, session: devices[int(token)]
    push.fcm = SimpleNamespace(enabled=True, send=AsyncMock(return_value=SendResult(True, "")))


async def test_push_device_preferences_and_legacy_default(mobile_app):
    push = mobile_app[0].state.mobile_push
    registered_devices(push)
    committed = asyncio.get_running_loop().create_future()
    push.queue_price_watch(await push.price_watch_devices(), [(uuid4(), "BTC 上穿 100,000", "现价 100,012 · 币安现货")], committed)
    sender = asyncio.create_task(push._send_price_watch())
    try:
        await asyncio.sleep(0)
        push.fcm.send.assert_not_awaited()
        committed.set_result(True)
        await push._price_watch_queue.join()
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
    calls = push.fcm.send.await_args_list
    assert [call.args[0] for call in calls] == ["0", "1"]
    assert all(call.args[1].kind == "price_watch" and call.args[1].channel == "urgent" for call in calls)


async def test_full_queue_rolls_back_whole_tick_then_sends_once(watch, mobile_app):
    app = mobile_app[0]
    push = watch.push = app.state.mobile_push
    registered_devices(push)
    push._price_watch_queue = asyncio.Queue(maxsize=1)
    abandoned = asyncio.get_running_loop().create_future()
    abandoned.set_result(False)
    push.queue_price_watch(await push.price_watch_devices(), [(uuid4(), "已回滚", "")], abandoned)
    rules = [dict(symbol="BTC", kind="above", price=p) for p in (10, 11)]
    saved = await watch.save(update(*rules))
    await asyncio.wait_for(sample(watch, 0, 12), timeout=2)
    assert await watch.get() == saved
    assert all(alert.state == "armed" for alert in saved.alerts)
    assert await app.state.mobile_alert_repo.list(0, 10) == []
    sender = asyncio.create_task(push._send_price_watch())
    try:
        await push._price_watch_queue.join()
        push.fcm.send.assert_not_awaited()
        await sample(watch, 5, 12)
        await sample(watch, 10, 12)
        await push._price_watch_queue.join()
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
    assert (await watch.get()).revision == 2
    assert len(await app.state.mobile_alert_repo.list(0, 10)) == 2
    deliveries = [(call.args[0], call.args[1].identity) for call in push.fcm.send.await_args_list]
    assert deliveries == [(device, str(alert.id)) for alert in saved.alerts for device in ("0", "1")]


async def test_commit_failure_discards_queued_push(watch, mobile_app, monkeypatch):
    app = mobile_app[0]
    push = watch.push = app.state.mobile_push
    registered_devices(push)
    saved = await watch.save(update(dict(symbol="BTC", kind="above", price=10)))
    with monkeypatch.context() as patch:
        patch.setattr(watch.repo.db, "commit", AsyncMock(side_effect=sqlite3.OperationalError("commit failed")))
        with pytest.raises(sqlite3.OperationalError, match="commit failed"):
            await sample(watch, 0, 11)
    assert await watch.get() == saved
    assert await app.state.mobile_alert_repo.list(0, 10) == []
    sender = asyncio.create_task(push._send_price_watch())
    try:
        await push._price_watch_queue.join()
        push.fcm.send.assert_not_awaited()
        await sample(watch, 5, 11)
        await push._price_watch_queue.join()
        assert push.fcm.send.await_count == 2
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def test_cancel_during_commit_still_sends_committed_push_once(watch, mobile_app, monkeypatch):
    app = mobile_app[0]
    push = watch.push = app.state.mobile_push
    registered_devices(push)
    await watch.save(update(dict(symbol="BTC", kind="above", price=10)))
    entered, release = asyncio.Event(), asyncio.Event()
    real_commit = watch.repo.db.commit

    async def slow_commit():
        entered.set()
        await release.wait()
        await real_commit()

    sender = asyncio.create_task(push._send_price_watch())
    try:
        with monkeypatch.context() as patch:
            patch.setattr(watch.repo.db, "commit", slow_commit)
            tick = asyncio.create_task(sample(watch, 0, 11))
            await asyncio.wait_for(entered.wait(), timeout=2)
            tick.cancel()  # e.g. shutdown cancels the monitor task mid-commit
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await tick
            await asyncio.sleep(0.05)
        await asyncio.wait_for(push._price_watch_queue.join(), timeout=2)
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
    assert (await watch.get()).alerts[0].state == "triggered"
    assert len(await app.state.mobile_alert_repo.list(0, 10)) == 1
    assert push.fcm.send.await_count == 2


async def test_no_short_window_change_after_forty_minute_gap(watch):
    await watch.save(update(dict(symbol="BTC", kind="change", change_pct=1, window_minutes=1)))
    await sample(watch, 0, 100)
    await sample(watch, 2400, 120)
    now = START + timedelta(minutes=40)
    assert watch.history.change("BTC", 120, now, 1) is None
    quote, = (await watch.quotes()).quotes
    assert (quote.change_5m_pct, quote.change_15m_pct, quote.change_1h_pct) == (None, None, None)
    assert (await watch.get()).alerts[0].state == "armed"
    watch.push.queue_price_watch.assert_not_called()


@pytest.mark.parametrize("minutes", [1, 5, 15, 60])
async def test_window_start_accepts_exactly_thirty_seconds_of_drift(watch, minutes):
    await watch.save(update())
    await sample(watch, 0, 100)
    await sample(watch, minutes * 60 + 30, 110)
    assert watch.history.change("BTC", 110, watch.clock(), minutes) == 10
    await sample(watch, minutes * 60 + 31, 110)
    assert watch.history.change("BTC", 110, watch.clock(), minutes) is None


async def test_cooldown_expires_during_stale_quotes(watch):
    await watch.save(update(dict(symbol="BTC", kind="change", change_pct=1, window_minutes=1, rearm=True)))
    await sample(watch, 0, 100)
    await sample(watch, 60, 110)
    await sample(watch, 119, 120, stale=True)
    config = await watch.get()
    assert config.alerts[0].state == "cooldown" and config.revision == 2
    await sample(watch, 120, 120, stale=True)
    config = await watch.get()
    assert config.alerts[0].state == "armed" and config.alerts[0].cooldown_until is None
    assert config.revision == 3 and watch.push.queue_price_watch.call_count == 1


async def test_legacy_config_starts_at_revision_zero(watch):
    await watch.repo.db.execute("INSERT INTO app_settings(key, payload) VALUES ('mobile_price_watch', ?)",
                                ('{"symbols":["BTC"],"alerts":[]}',))
    await watch.repo.db.commit()
    await sample(watch, 0, 100)
    legacy = await watch.get()
    assert legacy.symbols == ["BTC"] and legacy.revision == 0
    assert (await watch.save(update(symbols=("ETH",), revision=legacy.revision))).revision == 1
