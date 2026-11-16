import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app import main
from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.models.alert import AlertRule
from app.models.astro import AstroAlertActionResult
from app.models.settings import AlertMessageTemplateSettings, AstroCardSettings, RiskSettings
from app.services.alert_delivery_store import AlertDeliveryStore
from app.services.alert_engine import AlertMatch
from app.services.big_spread_alert import delivery_rule
from test_big_spread_alert import item, loop_app


def phone_settings(**updates):
    return RiskSettings(**{
        "big_spread_alert_hits": 1,
        "big_spread_phone_enabled": True,
        "big_spread_phone_start_hour": 22,
        "big_spread_phone_end_hour": 6,
        **updates,
    })


def freeze_time(monkeypatch, instant):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    monkeypatch.setattr(main, "datetime", FrozenDateTime)


def test_big_spread_phone_defaults_for_old_settings():
    settings = RiskSettings.model_validate({})
    assert (settings.big_spread_phone_enabled, settings.big_spread_phone_start_hour,
            settings.big_spread_phone_end_hour, settings.big_spread_phone_min_spread_pct,
            settings.big_spread_phone_max_per_night) == (False, 0, 7, 5.0, 3)


@pytest.mark.parametrize("field,valid,invalid", [
    ("big_spread_phone_start_hour", [0, 23], [-1, 24, 1.5]),
    ("big_spread_phone_end_hour", [0, 23], [-1, 24, 1.5]),
    ("big_spread_phone_min_spread_pct", [0.000001, 100], [0, -1, 100.1, float("nan"), float("inf")]),
    ("big_spread_phone_max_per_night", [0, 20], [-1, 21, 1.5]),
])
def test_big_spread_phone_setting_bounds(field, valid, invalid):
    for value in valid:
        assert getattr(RiskSettings(**{field: value}), field) == value
    for value in invalid:
        with pytest.raises(ValidationError):
            RiskSettings(**{field: value})


@pytest.mark.parametrize("start,end,utc,night", [
    (0, 7, "2026-11-15T16:00:00+00:00", "2026-11-16"),
    (0, 7, "2026-11-15T22:59:59+00:00", "2026-11-16"),
    (0, 7, "2026-11-15T23:00:00+00:00", None),
    (9, 17, "2026-11-16T01:00:00+00:00", "2026-11-16"),
    (9, 17, "2026-11-16T09:00:00+00:00", None),
    (22, 6, "2026-11-16T13:59:59+00:00", None),
    (22, 6, "2026-11-16T14:00:00+00:00", "2026-11-16"),
    (22, 6, "2026-11-16T16:00:00+00:00", "2026-11-16"),
    (22, 6, "2026-11-16T21:59:59+00:00", "2026-11-16"),
    (22, 6, "2026-11-16T22:00:00+00:00", None),
    (0, 0, "2026-11-16T15:00:00+00:00", "2026-11-16"),
    (7, 7, "2026-11-16T22:59:59+00:00", "2026-11-16"),
    (7, 7, "2026-11-16T23:00:00+00:00", "2026-11-17"),
])
def test_big_spread_phone_beijing_window_and_night(start, end, utc, night):
    settings = phone_settings(big_spread_phone_start_hour=start, big_spread_phone_end_hour=end)
    assert main._big_spread_phone_night(settings, datetime.fromisoformat(utc)) == night


@pytest.fixture
async def phone_app(tmp_path, monkeypatch):
    now = datetime(2026, 11, 16, 15, tzinfo=UTC)  # 23:00 Beijing.
    freeze_time(monkeypatch, now)
    settings = phone_settings()
    app, stop = loop_app(item(5, now), risk=settings)
    app.state.feishu_notifier.send_phone_urgent_text = AsyncMock()
    app.state.settings = Settings(alert_match_concurrency=4)
    db = await connect_database(str(tmp_path / "phone.db"))
    await initialize_schema(db)
    app.state.alert_delivery_store = AlertDeliveryStore(db)
    write_lock = asyncio.Lock()

    async def process(candidate=None, rule=None):
        candidate = candidate or item(5, now)
        existing = app.state.snapshot_store.get_opportunities()
        app.state.snapshot_store.set_opportunities([
            value for value in existing if value.id != candidate.id
        ] + [candidate])
        return await main._process_alert_match(
            app, AlertMatch(rule or delivery_rule([], settings), candidate, []), settings,
            AlertMessageTemplateSettings(), app.state.alert_delivery_store,
            app.state.alert_event_repo, write_lock, app.state.settings,
        )

    try:
        yield app, settings, now, process, stop
    finally:
        await db.close()


@pytest.mark.parametrize("spread,expected", [(4.999999999, 0), (5, 1), (5.01, 1)])
async def test_big_spread_phone_threshold_and_short_existing_message(phone_app, spread, expected):
    app, _, now, process, _ = phone_app
    assert await process(item(spread, now, buy_exchange="hyperliquid", buy_dex="xyz",
                              buy_raw_symbol="xyz:IMX"))
    phone = app.state.feishu_notifier.send_phone_urgent_text
    assert phone.await_count == expected
    event, = app.state.alert_event_repo.events
    assert event.status == "sent"
    assert app.state.feishu_notifier.sent_texts == [event.message]
    if expected:
        lines = phone.await_args.args[0].splitlines()
        original = event.message.splitlines()
        assert lines == [original[0], original[1], original[-1]]
        assert "HL xyz" in lines[1] and "xyz:IMX" in lines[1]
        assert "开 5.00% / 平 0.50%" in lines[0] if spread == 5 else "开 5.01%" in lines[0]
        assert lines[-1].startswith("卡片：")


@pytest.mark.parametrize("reason", ["disabled", "zero_limit", "outside", "muted", "text_failed", "ordinary"])
async def test_big_spread_phone_only_after_eligible_sent_fast_alert(phone_app, reason):
    app, settings, now, process, _ = phone_app
    rule = None
    if reason == "disabled":
        settings.big_spread_phone_enabled = False
    elif reason == "zero_limit":
        settings.big_spread_phone_max_per_night = 0
    elif reason == "outside":
        settings.big_spread_phone_start_hour = 0
        settings.big_spread_phone_end_hour = 7
    elif reason == "muted":
        app.state.feishu_live_send_enabled = False
    elif reason == "text_failed":
        app.state.feishu_notifier.send_alert = AsyncMock(side_effect=RuntimeError("offline"))
    else:
        rule = AlertRule(id="ordinary", name="ordinary")
    assert await process(item(5, now), rule)
    app.state.feishu_notifier.send_phone_urgent_text.assert_not_awaited()
    states = await app.state.alert_delivery_store.load_all()
    assert not any(state.key.startswith("big_spread:phone:") for state in states)


async def test_big_spread_phone_concurrent_routes_share_symbol_and_nightly_limit(phone_app):
    app, settings, now, process, _ = phone_app
    settings.big_spread_phone_max_per_night = 2
    phone = app.state.feishu_notifier.send_phone_urgent_text

    async def slow_send(_text):
        await asyncio.sleep(0.01)

    phone.side_effect = slow_send
    candidates = [item(5, now, id=str(index), symbol=symbol) for index, symbol in enumerate([
        "IMX-USDT", " imx/usdt ", "ETHUSDT", "BTCUSDT", "SOLUSDT",
    ])]
    assert all(await asyncio.gather(*(process(candidate) for candidate in candidates)))
    assert phone.await_count == 2
    assert len(app.state.feishu_notifier.sent_texts) == 5
    states = await app.state.alert_delivery_store.load_all()
    assert {state.key for state in states if state.key.startswith("big_spread:phone:")} == {
        "big_spread:phone:2026-11-16:IMXUSDT", "big_spread:phone:2026-11-16:ETHUSDT",
    }


async def test_big_spread_phone_failure_preserves_text_card_loop_and_quota(phone_app, caplog):
    app, settings, now, _, stop = phone_app
    settings.big_spread_phone_max_per_night = 1
    service = Mock()
    service.card_settings = AstroCardSettings()
    service.handle_big_spread_alert = AsyncMock(return_value=AstroAlertActionResult(
        enabled=True, status="created", action="create", message="建卡完成",
    ))
    app.state.astro_alert_service = service
    phone = app.state.feishu_notifier.send_phone_urgent_text
    phone.side_effect = [RuntimeError("offline\nprivate detail"), None]
    app.state.snapshot_store.set_opportunities([item(5, now), item(5, now, id="eth", symbol="ETHUSDT")])
    with caplog.at_level(logging.WARNING, logger="app.main"):
        await asyncio.wait_for(main._run_alert_loop(app, .001, stop), timeout=2)
    assert phone.await_count == service.handle_big_spread_alert.await_count == 2
    assert len(app.state.feishu_notifier.sent_texts) == 2
    events = app.state.alert_event_repo.events
    assert all(event.status == "sent" and "已建卡" in event.message for event in events)
    failed = [event for event in events if event.message.endswith("；电话加急失败")]
    assert len(failed) == 1
    assert failed[0].message.removesuffix("；电话加急失败") in app.state.feishu_notifier.sent_texts
    warnings = [record for record in caplog.records if "phone" in record.message.lower()]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING and warnings[0].exc_info is None
    assert "\n" not in warnings[0].message and "private detail" not in warnings[0].message
    states = await app.state.alert_delivery_store.load_all()
    assert len([state for state in states if state.key.startswith("big_spread:phone:")]) == 1


async def test_big_spread_phone_failed_symbol_can_retry_same_night(phone_app):
    app, _, now, process, _ = phone_app
    phone = app.state.feishu_notifier.send_phone_urgent_text
    phone.side_effect = [RuntimeError("offline"), None]
    assert await process()
    assert await process(item(6, now, id="retry"))
    assert phone.await_count == 2
    states = await app.state.alert_delivery_store.load_all()
    assert len([state for state in states if state.key.startswith("big_spread:phone:")]) == 1


async def test_big_spread_phone_restart_keeps_count_and_symbol_until_next_night(phone_app, monkeypatch):
    app, settings, now, process, _ = phone_app
    settings.big_spread_phone_max_per_night = 2
    assert await process()
    old_db = app.state.alert_delivery_store.db
    async with old_db.execute("PRAGMA database_list") as cursor:
        path = (await cursor.fetchone())["file"]
    db = await connect_database(path)
    try:
        restarted, _ = loop_app(item(6, now), risk=settings)
        restarted.state.alert_delivery_store = AlertDeliveryStore(db)
        restarted.state.settings = Settings()
        restarted.state.feishu_notifier.send_phone_urgent_text = AsyncMock()
        await main._restore_alert_delivery_state(restarted)
        assert not any(key.startswith("big_spread:phone:")
                       for key in restarted.state.big_spread_alert_engine._last_sent)
        # Replace all process-local state, including any phone lock/cache.
        app.state = restarted.state
        assert await process(item(6, now, id="same", symbol="imx/usdt"))
        assert await process(item(6, now, id="eth", symbol="ETHUSDT"))
        assert await process(item(6, now, id="btc", symbol="BTCUSDT"))
        phone = app.state.feishu_notifier.send_phone_urgent_text
        assert phone.await_count == 1
        # Midnight belongs to the same 22->6 night.
        later = now + timedelta(hours=2)
        freeze_time(monkeypatch, later)
        assert await process(item(6, later, id="after-midnight", symbol="SOLUSDT"))
        assert phone.await_count == 1
        tomorrow = now + timedelta(days=1)
        freeze_time(monkeypatch, tomorrow)
        assert await process(item(6, tomorrow, id="tomorrow"))
        assert phone.await_count == 2
    finally:
        await db.close()


async def test_big_spread_phone_storage_failure_is_isolated(phone_app, caplog):
    app, _, _, process, _ = phone_app
    app.state.alert_delivery_store.load_all = AsyncMock(side_effect=RuntimeError("db unavailable"))
    assert await process()
    event, = app.state.alert_event_repo.events
    assert event.status == "sent" and event.message.endswith("；电话加急失败")
    app.state.feishu_notifier.send_phone_urgent_text.assert_not_awaited()
    assert caplog.records[-1].exc_info is None
