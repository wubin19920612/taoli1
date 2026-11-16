import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from test_big_spread_alert import item, loop_app

from app import main
from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.models.settings import AlertMessageTemplateSettings, RiskSettings
from app.services.alert_delivery_store import AlertDeliveryState, AlertDeliveryStore
from app.services.big_spread_alert import BigSpreadAlertEngine


def test_big_spread_symbol_cooldown_setting_default_and_bounds():
    assert RiskSettings.model_validate({}).big_spread_alert_symbol_cooldown_seconds == 1800
    for seconds in (0, 604800):
        settings = RiskSettings(big_spread_alert_symbol_cooldown_seconds=seconds)
        assert settings.big_spread_alert_symbol_cooldown_seconds == seconds
    for seconds in (-1, 604801, 1.5, float("inf")):
        with pytest.raises(ValidationError):
            RiskSettings(big_spread_alert_symbol_cooldown_seconds=seconds)


def evaluate(engine, now, *candidates, **settings):
    return engine.evaluate(
        [candidate.model_copy(update={"last_seen_at": now}) for candidate in candidates],
        [], now=now, risk_settings=RiskSettings(big_spread_alert_hits=1, **settings),
    )


def test_big_spread_same_normalized_symbol_routes_share_cooldown():
    now = datetime.now(UTC)
    engine = BigSpreadAlertEngine()
    first = item(id="route-a", symbol=" imx-usdt ")
    second = item(id="route-b", symbol="IMX/USDT", sell_exchange="okx")
    match, = evaluate(engine, now, first, second)
    engine.record_delivery_result(match, "sent", now)
    assert not evaluate(engine, now + timedelta(seconds=600), first, second)
    assert len(evaluate(engine, now + timedelta(seconds=1800), second)) == 1


@pytest.mark.parametrize("spread,allowed", [(5.999, False), (6 - 5e-10, True), (6, True), (6.01, True)])
def test_big_spread_symbol_double_spread_with_epsilon(spread, allowed):
    now = datetime.now(UTC)
    engine = BigSpreadAlertEngine()
    match, = evaluate(engine, now, item(3))
    engine.record_delivery_result(match, "sent", now)
    assert bool(evaluate(engine, now + timedelta(seconds=1), item(spread, id="route-b"))) is allowed


def test_big_spread_symbol_baseline_updates_only_after_sent_and_requires_route_gate():
    now = datetime.now(UTC)
    engine = BigSpreadAlertEngine()
    match, = evaluate(engine, now, item(3))
    engine.record_delivery_result(match, "sent", now)
    doubled, = evaluate(engine, now + timedelta(seconds=1), item(6, id="route-b"))
    engine.record_delivery_result(doubled, "sent", now + timedelta(seconds=1))
    assert not evaluate(engine, now + timedelta(seconds=2), item(9, id="route-b"))
    assert not evaluate(engine, now + timedelta(seconds=2), item(6, id="route-c"))
    failed, = evaluate(engine, now + timedelta(seconds=3), item(12, id="route-c"))
    engine.record_delivery_result(failed, "failed", now + timedelta(seconds=3))
    assert evaluate(engine, now + timedelta(seconds=4), item(12, id="route-d"))
    # The symbol cooldown can expire while the longer route cooldown still applies.
    assert not evaluate(engine, now + timedelta(seconds=1801), item(6, id="route-b"),
                        big_spread_alert_cooldown_seconds=3600)
    assert evaluate(engine, now + timedelta(seconds=1801), item(9, id="route-b"),
                    big_spread_alert_cooldown_seconds=3600)


@pytest.mark.parametrize("status", ["failed", "muted"])
def test_big_spread_unsent_does_not_start_symbol_cooldown(status):
    now = datetime.now(UTC)
    engine = BigSpreadAlertEngine()
    match, = evaluate(engine, now, item())
    engine.record_delivery_result(match, status, now)
    assert evaluate(engine, now + timedelta(seconds=1), item(id="route-b"))


def test_big_spread_zero_symbol_cooldown_preserves_route_escalation_and_other_routes():
    now = datetime.now(UTC)
    engine = BigSpreadAlertEngine()
    settings = {"big_spread_alert_symbol_cooldown_seconds": 0}
    matches = evaluate(engine, now, item(id="a"), item(id="b"), **settings)
    assert len(matches) == 2
    for match in matches:
        engine.record_delivery_result(match, "sent", now)
    assert not evaluate(engine, now + timedelta(seconds=1), item(4.49, id="a"), **settings)
    assert evaluate(engine, now + timedelta(seconds=1), item(4.5, id="a"), **settings)
    assert evaluate(engine, now + timedelta(seconds=1), item(3, id="c"), **settings)


def test_big_spread_different_symbols_are_independent():
    now = datetime.now(UTC)
    engine = BigSpreadAlertEngine()
    matches = evaluate(engine, now, item(), item(id="other", symbol="ETHUSDT"))
    assert len(matches) == 2
    engine.record_delivery_result(matches[0], "sent", now)
    assert len(evaluate(engine, now, item(), item(id="other", symbol="ETHUSDT"))) == 1


def test_big_spread_symbol_state_bounded_and_expires_even_with_long_route_cooldown(monkeypatch):
    monkeypatch.setattr("app.services.big_spread_alert.MAX_BIG_SPREAD_KEYS", 2)
    now = datetime.now(UTC)
    engine = BigSpreadAlertEngine()
    for index in range(2):
        match, = evaluate(engine, now, item(id=str(index), symbol=f"COIN{index}USDT"),
                          big_spread_alert_cooldown_seconds=604800)
        engine.record_delivery_result(match, "sent", now)
    assert len(engine._symbol_last_sent) == len(engine._symbol_last_spread) == 2
    assert not evaluate(engine, now, item(id="new", symbol="NEWUSDT"))
    evaluate(engine, now + timedelta(seconds=1800), big_spread_alert_cooldown_seconds=604800)
    assert not engine._symbol_last_sent and not engine._symbol_last_spread
    assert len(engine._last_sent) == 2


@pytest.mark.asyncio
async def test_big_spread_symbol_cooldown_persists_and_restores_without_baseline(tmp_path):
    app, stop = loop_app(item(id="route-a"))
    db = await connect_database(str(tmp_path / "delivery.db"))
    try:
        await initialize_schema(db)
        store = app.state.alert_delivery_store = AlertDeliveryStore(db)
        await asyncio.wait_for(main._run_alert_loop(app, .001, stop), timeout=2)
        states = await store.load_all()
        restarted = BigSpreadAlertEngine()
        now = max(state.last_sent_at for state in states if state.last_sent_at is not None)
        assert restarted.restore(states, now) == []
        # An unseen route must also wait; its huge spread cannot bypass a missing baseline.
        assert not evaluate(restarted, now + timedelta(seconds=1799), item(30, id="route-b"))
        assert evaluate(restarted, now + timedelta(seconds=1800), item(30, id="route-b"))
    finally:
        await db.close()


@pytest.mark.parametrize("pending", [False, True])
def test_big_spread_restored_symbol_cooldown_has_no_escalation_baseline(pending):
    now = datetime.now(UTC)
    engine = BigSpreadAlertEngine()
    state = AlertDeliveryState(key="big_spread:symbol:IMXUSDT",
                               last_sent_at=None if pending else now,
                               pending_since=now if pending else None)
    assert engine.restore([state], now) == ([state.key] if pending else [])
    assert not evaluate(engine, now + timedelta(seconds=1799), item(30))
    assert evaluate(engine, now + timedelta(seconds=1800), item(30))


@pytest.mark.asyncio
@pytest.mark.parametrize("live,send_fails,warn", [(True, False, True), (False, False, False), (True, True, False)])
async def test_big_spread_uncreated_card_warning_only_on_sent_events(live, send_fails, warn):
    candidate = item()
    app, _ = loop_app(candidate)
    app.state.feishu_live_send_enabled = live
    if send_fails:
        app.state.feishu_notifier.send_alert = AsyncMock(side_effect=RuntimeError("offline"))
    match, = evaluate(app.state.big_spread_alert_engine, datetime.now(UTC), candidate)
    await main._process_alert_match(
        app, match, RiskSettings(), AlertMessageTemplateSettings(), app.state.alert_delivery_store,
        app.state.alert_event_repo, asyncio.Lock(), Settings(),
    )
    event, = app.state.alert_event_repo.events
    assert event.status == ("failed" if send_fails else "sent" if live else "muted")
    assert event.message.startswith("⚠️ 【大价差】") is warn
    if warn:
        assert app.state.feishu_notifier.sent_texts == [event.message]
