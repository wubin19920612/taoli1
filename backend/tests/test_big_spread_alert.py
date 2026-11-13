import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app import main
from app.core.config import Settings
from app.models.alert import AlertRule
from app.models.market_identity import PairCapabilities, PairValidationResult
from app.models.orderbook import DepthValidationResult
from app.models.settings import AlertMessageTemplateSettings, AstroCardSettings, RiskSettings
from app.services.alert_delivery_store import AlertDeliveryState, AlertDeliveryStore
from app.services.alert_engine import AlertEngine
from app.services.alert_messages import build_big_spread_message
from app.services.alert_reject import AlertRejectLogger, logger as reject_logger
from app.services.astro_alerts import AstroAlertService
from app.services.astro_card_attempts import route_key
from app.services.big_spread_alert import BigSpreadAlertEngine
from app.services.snapshot_store import SnapshotStore
from test_alert_loop import (
    FakeEventRepo, FakeFeishuNotifier, FakeOrderBookValidator,
    FakeRuleRepo, FakeSettingsRepo, opportunity,
)
from test_astro_alerts import FakeAstroClient


def item(spread=3.0, now=None, **updates):
    return opportunity().model_copy(update={
        "symbol": "IMXUSDT", "sell_exchange": "gate", "open_spread_pct": spread,
        "sell_bid": 100 * (1 + spread / 100), "sell_ask": 100 * (1 + spread / 100) + .1,
        "fee_adjusted_open_pct": spread - .2, "last_seen_at": now or datetime.now(UTC),
        **updates,
    })


def test_defaults_old_data_and_validation():
    settings = RiskSettings.model_validate({"min_volume_24h_usdt": 10})
    assert (settings.big_spread_alert_enabled, settings.big_spread_alert_pct,
            settings.big_spread_alert_hits, settings.big_spread_alert_cooldown_seconds,
            settings.big_spread_alert_auto_create) == (True, 3, 2, 600, True)
    for updates in ({"big_spread_alert_hits": 0}, {"big_spread_alert_pct": float("nan")},
                    {"big_spread_alert_cooldown_seconds": -1}):
        with pytest.raises(ValidationError):
            RiskSettings(**updates)


def test_two_hits_reset_missing_round_and_ignores_decay_and_labels():
    engine = BigSpreadAlertEngine()
    now = datetime.now(UTC)
    def evaluate(spread, **kwargs):
        return engine.evaluate([item(spread, now, **kwargs)], [], now=now)
    assert not evaluate(3)
    assert not evaluate(2.99)
    assert not evaluate(16)
    labels = ["TRANSIENT_SIGNAL", "THIN_ORDER_BOOK", "EDGE_AFTER_SLIPPAGE_TOO_SMALL", "HUGE_SPREAD_VERIFY"]
    matches = evaluate(3, risk_labels=labels)
    assert len(matches) == 1  # >60% decay does not gate fast alerts.
    message = build_big_spread_message(matches[0].rule, matches[0].opportunity)
    for label in labels:
        assert label in message
    for text in ("【大价差提醒】", "信号不稳定", "盘口深度薄", "有效收益偏小", "≥10% 价差，请人工核实",
                 "买入侧盘口：bid 99 / ask 100", "卖出侧盘口：bid 103", "24h", "平仓"):
        assert text in message
    assert not engine.evaluate([], [], now=now)
    assert not evaluate(3)


def test_cooldown_escalates_against_last_successful_send_and_retries():
    engine = BigSpreadAlertEngine()
    now = datetime.now(UTC)
    settings = RiskSettings(big_spread_alert_hits=1)
    def evaluate(spread, seconds):
        current = now + timedelta(seconds=seconds)
        return engine.evaluate([item(spread, current)], [], now=current, risk_settings=settings)
    match = evaluate(3, 0)[0]
    engine.record_delivery_result(match, "sent", now)
    assert not evaluate(4.49, 1)
    match = evaluate(4.5, 2)[0]
    engine.record_delivery_result(match, "sent", now + timedelta(seconds=2))
    assert not evaluate(4.5, 3)
    assert not evaluate(6.74, 4)
    assert evaluate(6.75, 5)
    assert not evaluate(3, 601)
    match = evaluate(3, 602)[0]
    engine.record_delivery_result(match, "failed", now + timedelta(seconds=602))
    assert not evaluate(3, 603)
    assert evaluate(3, 662)


@pytest.mark.parametrize("updates,settings,rules", [
    ({}, {"big_spread_alert_enabled": False}, []),
    ({}, {"excluded_symbols": ["IMX-USDT"]}, []),
    ({}, {"ignored_exchanges": ["GATE"]}, []),
    ({}, {}, [AlertRule(name="exclude", exclude_exchanges=["gate"])]),
    ({"type": "SS"}, {}, []),
    ({}, {}, [AlertRule(name="type", types=["SF"])]),
    ({"buy_volume_24h_usdt": None, "sell_volume_24h_usdt": None}, {}, []),
    ({"buy_volume_24h_usdt": 99_999}, {}, []),
    ({"identity_enforced": True}, {}, []),
    ({"last_seen_at": datetime(2000, 1, 1, tzinfo=UTC)}, {}, []),
])
def test_eligibility_gates(updates, settings, rules):
    engine = BigSpreadAlertEngine()
    assert not engine.evaluate([item(**updates)], rules,
                               risk_settings=RiskSettings(big_spread_alert_hits=1, **settings))


def test_enabled_rule_union_min_volume_strict_age_and_identity():
    now = datetime.now(UTC)
    engine = BigSpreadAlertEngine()
    settings = RiskSettings(big_spread_alert_hits=1)
    rules = [AlertRule(name="a", types=["FF"], min_volume_24h_usdt=200, max_data_age_seconds=10),
             AlertRule(name="b", types=["SS"], min_volume_24h_usdt=100, max_data_age_seconds=30),
             AlertRule(name="off", enabled=False, exclude_exchanges=["gate"], max_data_age_seconds=1)]
    validation = PairValidationResult(validated_at=now, expires_at=now + timedelta(seconds=10),
                                      capabilities=PairCapabilities(strategy_alert=True))
    candidate = item(now=now, buy_volume_24h_usdt=100, identity_enforced=True, validation=validation)
    assert engine.evaluate([candidate], rules, now=now, risk_settings=settings)
    assert not engine.evaluate([candidate], rules, now=now + timedelta(seconds=11), risk_settings=settings)
    candidate = candidate.model_copy(update={"identity_enforced": False})
    assert not engine.evaluate([candidate], rules, now=now + timedelta(seconds=11), risk_settings=settings)


def test_bounded_state_and_restored_cooldown(monkeypatch):
    monkeypatch.setattr("app.services.big_spread_alert.MAX_BIG_SPREAD_KEYS", 2)
    engine = BigSpreadAlertEngine()
    now = datetime.now(UTC)
    settings = RiskSettings(big_spread_alert_hits=1)
    for index in range(4):
        match = engine.evaluate([item(id=str(index), now=now)], [], now=now, risk_settings=settings)[0]
        engine.record_delivery_result(match, "sent", now)
    assert len(engine._seen) == len(engine._last_sent) == len(engine._last_spread) == 2
    engine.evaluate([], [], now=now + timedelta(hours=2))
    assert not engine._seen and not engine._last_sent
    engine.restore([AlertDeliveryState(key="big_spread:opp-1", last_sent_at=now)], now)
    assert not engine.evaluate([item(16, now)], [], now=now, risk_settings=settings)
    assert engine.evaluate([item(16, now + timedelta(seconds=600))], [],
                           now=now + timedelta(seconds=600), risk_settings=settings)


def capture_rejects(monkeypatch):
    rows = []
    monkeypatch.setattr(reject_logger, "info", lambda value: rows.append(json.loads(value)))
    return rows


def test_reject_log_rate_limit_and_detailed_reasons(monkeypatch):
    rows = capture_rejects(monkeypatch)
    log = AlertRejectLogger()
    engine = AlertEngine()
    now = datetime.now(UTC)
    rule = AlertRule(name="guize1", types=["FF"], min_open_spread_pct=10,
                     favorable_funding_open_spread_pct=None, min_fee_adjusted_open_pct=8,
                     min_volume_24h_usdt=20_000_000, max_data_age_seconds=1, consecutive_hits=6)
    candidate = item(5, now - timedelta(seconds=2), risk_labels=["TRANSIENT_SIGNAL"], identity_enforced=True)
    for seconds in (0, 1, 29, 30):
        log.log_round([candidate], [rule], RiskSettings(), engine, [], now + timedelta(seconds=seconds))
    assert len(rows) == 2
    detail = rows[0]["reasons"][0]
    assert set(detail["reasons"]) >= {"identity", "open_spread", "fee_adjusted_open", "volume",
                                        "data_age", "excluded_risk_labels", "consecutive_hits"}
    assert detail["hits"] == 0 and detail["required_hits"] == 6
    assert detail["decay"]["peak"] == detail["decay"]["latest"] == 5
    assert rows[0]["buy_ask"] == 100 and rows[0]["sell_bid"] == 105
    assert "buy_quote_time" in rows[0] and "sell_quote_time" in rows[0]
    assert reject_logger.propagate is False and reject_logger.handlers


def test_reject_log_captures_actual_decay_window(monkeypatch):
    rows = capture_rejects(monkeypatch)
    now = datetime.now(UTC)
    engine = AlertEngine()
    rule = AlertRule(name="guize1", consecutive_hits=6)
    for spread in (16, 16, 16, 16, 16, 5):
        candidate = item(spread, now)
        matches = engine.evaluate([candidate], [rule], now=now)
        assert not matches
    AlertRejectLogger().log_round([candidate], [rule], RiskSettings(), engine, matches, now)
    detail = rows[0]["reasons"][0]
    assert detail["hits"] == detail["required_hits"] == 6
    assert detail["decay"] == {"passed": False, "peak": 16, "latest": 5, "max_decay_pct": 60}
    assert detail["reasons"] == ["spread_decay"]


def test_reject_log_capacity_does_not_bypass_throttle(monkeypatch):
    rows = capture_rejects(monkeypatch)
    monkeypatch.setattr("app.services.alert_reject.MAX_REJECT_KEYS", 2)
    now = datetime.now(UTC)
    log = AlertRejectLogger()
    items = [item(id=str(index), now=now) for index in range(3)]
    for seconds in (0, 1, 30):
        log.log_round(items, [], RiskSettings(), AlertEngine(), [], now + timedelta(seconds=seconds))
    assert len(rows) == 4
    assert len(log._logged) == 2


def test_message_preserves_hyperliquid_market_and_units():
    engine = BigSpreadAlertEngine()
    match = engine.evaluate([item(buy_exchange="hyperliquid", buy_raw_symbol="xyz:IMX",
                                   buy_dex="xyz", buy_price_multiplier=10, buy_contract_size_multiplier=100)],
                            [], risk_settings=RiskSettings(big_spread_alert_hits=1))[0]
    message = build_big_spread_message(match.rule, match.opportunity)
    for text in ("xyz:IMX", "DEX xyz", "价格倍率 10", "1张=100", "手续费", "费后开仓估算"):
        assert text in message


def test_imx_constructed_replay_second_round_alert_and_guize1_rejects(monkeypatch):
    rows = capture_rejects(monkeypatch)
    now = datetime(2026, 11, 13, 11, 9, 22, tzinfo=timezone(timedelta(hours=8)))
    normal, fast, log = AlertEngine(), BigSpreadAlertEngine(), AlertRejectLogger()
    rule = AlertRule(id="guize1", name="guize1", types=["FF"], min_open_spread_pct=1,
                     min_fee_adjusted_open_pct=.7, consecutive_hits=6)
    fired = []
    for index, (seconds, spread) in enumerate(zip((0, 10, 20, 30, 40, 50, 60, 61), (5, 6, 16, 12, 8, 5, 7, 6))):
        current = now + timedelta(seconds=seconds)
        candidate = item(spread, current, risk_labels=["TRANSIENT_SIGNAL", "THIN_ORDER_BOOK"],
                         buy_timestamp=current, sell_timestamp=current)
        matches = normal.evaluate([candidate], [rule], now=current)
        assert not matches
        log.log_round([candidate], [rule], RiskSettings(), normal, matches, current)
        for match in fast.evaluate([candidate], [rule], now=current):
            fired.append(index + 1)
            fast.record_delivery_result(match, "sent", current)
    assert fired == [2, 3]  # 16% is also >50% above the last sent 6%.
    assert len(rows) == 3
    assert all("excluded_risk_labels" in row["reasons"][0]["reasons"] for row in rows)


def loop_app(candidate, rules=None, risk=None):
    app, stop = FastAPI(), asyncio.Event()
    app.state.snapshot_store = SnapshotStore()
    app.state.snapshot_store.set_opportunities([candidate])
    app.state.alert_rule_repo = FakeRuleRepo(rules or [])
    app.state.alert_event_repo = FakeEventRepo(stop)
    app.state.alert_delivery_store = AsyncMock(spec=AlertDeliveryStore)
    app.state.settings_repo = FakeSettingsRepo(risk_settings=risk or RiskSettings(big_spread_alert_hits=1))
    app.state.alert_engine = AlertEngine()
    app.state.big_spread_alert_engine = BigSpreadAlertEngine()
    app.state.feishu_notifier = FakeFeishuNotifier()
    return app, stop


@pytest.mark.asyncio
@pytest.mark.parametrize("normal_result", ["sent", "failed", "muted", "event_write_failed"])
async def test_loop_deduplicates_only_actual_normal_sends(normal_result):
    candidate = item()
    rule = AlertRule(name="ordinary", consecutive_hits=1)
    app, stop = loop_app(candidate, [rule])
    if normal_result == "failed":
        async def send(rule, *args, **kwargs):
            if rule.id != "big_spread":
                raise RuntimeError("test delivery failure")
            app.state.feishu_notifier.sent_texts.append(kwargs["prebuilt_text"])
        app.state.feishu_notifier.send_alert = send
    if normal_result == "muted":
        app.state.settings_repo.risk_settings.max_open_spread_decay_pct = 0
        latest = candidate.model_copy(update={"fee_adjusted_open_pct": -100})
        original = app.state.snapshot_store.get_opportunities
        app.state.snapshot_store.get_opportunities = Mock(side_effect=[original(), [latest], [latest]])
    if normal_result == "event_write_failed":
        async def fail_event(event):
            stop.set()
            raise RuntimeError("test event failure")
        app.state.alert_event_repo.create = fail_event
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    assert len(app.state.feishu_notifier.sent_texts) == 1
    if normal_result in {"sent", "event_write_failed"}:
        assert not app.state.feishu_notifier.sent_texts[0].startswith("【大价差提醒】")
    else:
        assert app.state.feishu_notifier.sent_texts[0].startswith("【大价差提醒】")


@pytest.mark.asyncio
@pytest.mark.parametrize("depth_passes", [True, False])
async def test_fast_alert_real_card_path_forces_closed_and_preserves_depth(depth_passes, astro_attempts_for_services):
    candidate = item()
    app, stop = loop_app(candidate)
    client = FakeAstroClient()
    service = AstroAlertService(client, Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
                                card_settings=AstroCardSettings(open_enabled=True), add_restart_delay_seconds=0)
    app.state.astro_alert_service = service
    validator = FakeOrderBookValidator(DepthValidationResult(
        passed=depth_passes, target_notional_usdt=1000, blockers=[] if depth_passes else ["盘口深度不足"],
        buy_filled_usdt=1000, sell_filled_usdt=1000, buy_vwap=100, sell_vwap=103,
        quoted_open_pct=3, executable_open_pct=3, effective_executable_edge_pct=2.8,
        slippage_loss_pct=0, warnings=[],
    ))
    app.state.orderbook_validator = validator
    app.state.settings_repo.alert_template = AlertMessageTemplateSettings(format="compact", include_risk=False,
                                                                        suppress_when_card_conditions_fail=True)
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    assert len(validator.calls) == 1
    assert validator.calls[0][2].open_enabled is False
    assert service.card_settings.open_enabled is True  # No mutation of shared settings.
    assert len(app.state.feishu_notifier.sent_texts) == 1
    if depth_passes:
        assert client.added, app.state.feishu_notifier.sent_texts[0]
        for pair in client.added:
            assert pair["status"] is False and pair["disableOpen"] is True
            attempt = await service.attempts.get(route_key(pair))
            assert attempt["state"] == "created" and not attempt["open_enabled"]
            assert attempt["source"] == "alert"
        assert "已建卡（未开仓，需手动开启）" in app.state.feishu_notifier.sent_texts[0]
    else:
        assert not client.added
        assert "订单簿校验未通过" in app.state.feishu_notifier.sent_texts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("global_enabled,fast_enabled", [(False, True), (True, False)])
async def test_card_requires_both_switches(global_enabled, fast_enabled, astro_attempts_for_services):
    app, stop = loop_app(item(), risk=RiskSettings(big_spread_alert_hits=1, big_spread_alert_auto_create=fast_enabled))
    client = FakeAstroClient()
    app.state.astro_alert_service = AstroAlertService(client, Settings(astro_alert_auto_create=global_enabled,
                                                                   astro_dry_run_only=False))
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    assert not client.added and not client.list_calls
    assert len(app.state.feishu_notifier.sent_texts) == 1


@pytest.mark.asyncio
async def test_filtered_risk_labels_still_reach_fast_channel_and_diagnostics(monkeypatch):
    rows = capture_rejects(monkeypatch)
    app, stop = loop_app(item(risk_labels=["TRANSIENT_SIGNAL"]), [AlertRule(name="guize1")])
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    assert app.state.alert_event_repo.events[0].rule_id == "big_spread"
    assert "TRANSIENT_SIGNAL（信号不稳定）" in app.state.feishu_notifier.sent_texts[0]
    assert rows[0]["reasons"][0]["excluded_labels"] == ["TRANSIENT_SIGNAL"]


@pytest.mark.asyncio
async def test_fast_latest_identity_failure_is_audited_without_sending():
    candidate = item()
    app, stop = loop_app(candidate)
    app.state.snapshot_store.get_opportunities = Mock(side_effect=[
        [candidate], [candidate.model_copy(update={"identity_enforced": True})],
    ])
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    assert not app.state.feishu_notifier.sent_texts
    assert app.state.alert_event_repo.events[0].status == "muted"
    assert "身份条件" in app.state.alert_event_repo.events[0].message


@pytest.mark.asyncio
async def test_history_keeps_big_spread_message_without_ordinary_rule_lookup():
    from app.api.routes_alerts import _resolve_event_message
    from app.models.alert import AlertEvent
    event = AlertEvent(rule_id="big_spread", opportunity_id="x", symbol="IMXUSDT", status="sent",
                       message="【大价差提醒】原始风险与卡片结果", created_at=datetime.now(UTC))
    assert await _resolve_event_message(None, event) == event.message
