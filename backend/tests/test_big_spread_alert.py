import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
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
from app.services.opportunity_trade_availability import OpportunityTradeAvailabilityReport
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


@pytest.mark.parametrize("field,low,high", [
    ("big_spread_alert_cooldown_seconds", 0, 604800),
    ("big_spread_alert_pct", 0.1, 100),
    ("big_spread_alert_hits", 1, 20),
])
def test_setting_bounds(field, low, high):
    for value in (low, high):
        assert getattr(RiskSettings(**{field: value}), field) == value
    for value in (low - 0.01, high + 1, float("inf")):
        with pytest.raises(ValidationError):
            RiskSettings(**{field: value})


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
    for text in ("【大价差】", "信号不稳定", "盘口深度薄", "有效收益偏小", "异常大价差",
                 "Binance 合约 买 100 → Gate 合约 卖 103", "24h", "平 0.50%"):
        assert text in message
    assert "请人工核实" not in message  # The numeric threshold controls the warning.
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


def test_bounded_state_and_restored_cooldown(monkeypatch, caplog):
    monkeypatch.setattr("app.services.big_spread_alert.MAX_BIG_SPREAD_KEYS", 2)
    engine = BigSpreadAlertEngine()
    now = datetime.now(UTC)
    settings = RiskSettings(big_spread_alert_hits=1)
    for index in range(2):
        match = engine.evaluate([item(id=str(index), now=now)], [], now=now, risk_settings=settings)[0]
        engine.record_delivery_result(match, "sent", now)
    for index in range(2, 4):
        assert not engine.evaluate([item(id=str(index), now=now)], [], now=now, risk_settings=settings)
    assert len(engine._seen) == len(engine._last_sent) == len(engine._last_spread) == 2
    assert len([record for record in caplog.records if record.levelname == "WARNING"]) == 1
    assert not engine.evaluate([item(id="0", now=now)], [], now=now, risk_settings=settings)
    engine.evaluate([], [], now=now + timedelta(hours=2))
    assert not engine._seen and not engine._last_sent
    engine.restore([AlertDeliveryState(key="big_spread:opp-1", last_sent_at=now)], now)
    assert not engine.evaluate([item(16, now)], [], now=now, risk_settings=settings)
    assert engine.evaluate([item(16, now + timedelta(seconds=600))], [],
                           now=now + timedelta(seconds=600), risk_settings=settings)


def test_capacity_evicts_only_inactive_keys_after_cooldown(monkeypatch):
    monkeypatch.setattr("app.services.big_spread_alert.MAX_BIG_SPREAD_KEYS", 2)
    engine = BigSpreadAlertEngine()
    now = datetime.now(UTC)
    settings = RiskSettings(big_spread_alert_hits=1, big_spread_alert_cooldown_seconds=10)
    for index in range(2):
        match = engine.evaluate([item(id=str(index), now=now)], [], now=now, risk_settings=settings)[0]
        engine.record_delivery_result(match, "sent", now)
    current = now + timedelta(seconds=10)
    # The active key comes after the new key: admission must know the entire round.
    matches = engine.evaluate([item(id="new", now=current), item(id="0", now=current)], [],
                              now=current, risk_settings=settings)
    assert {match.opportunity.id for match in matches} == {"new", "0"}
    assert set(engine._seen) == {"big_spread:new", "big_spread:0"}
    assert "big_spread:1" not in engine._last_sent
    assert "big_spread:1" not in engine._last_spread
    # Even after cooldown, active keys cannot be evicted to admit another key.
    matches = engine.evaluate([item(id="third", now=current), item(id="new", now=current),
                               item(id="0", now=current)], [], now=current, risk_settings=settings)
    assert {match.opportunity.id for match in matches} == {"new", "0"}
    assert set(engine._seen) == {"big_spread:new", "big_spread:0"}


def test_restored_capacity_keeps_seven_day_cooldowns(monkeypatch):
    monkeypatch.setattr("app.services.big_spread_alert.MAX_BIG_SPREAD_KEYS", 1)
    now = datetime.now(UTC)
    engine = BigSpreadAlertEngine()
    states = [AlertDeliveryState(key=f"big_spread:{index}", last_sent_at=now - timedelta(days=2))
              for index in range(2)]
    engine.restore(states, now)
    settings = RiskSettings(big_spread_alert_hits=1, big_spread_alert_cooldown_seconds=604800)
    assert not engine.evaluate([item(id=str(index), now=now) for index in range(3)], [],
                               now=now, risk_settings=settings)
    assert len(engine._last_sent) == 2
    assert len(engine._seen) == 2
    # Keep cooldown records past the old one-hour TTL when settings extend cooldown.
    current = now + timedelta(hours=2)
    engine.evaluate([], [], now=current, risk_settings=settings)
    assert len(engine._last_sent) == 2


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
    assert len([row for row in rows if "opportunity_id" in row]) == 4
    assert [row["suppressed_count"] for row in rows if "suppressed_count" in row] == [1, 1, 1]
    assert len(log._logged) == 2


@pytest.mark.parametrize("count", [20, 21, 45])
def test_reject_log_round_line_limit_priority_summary_and_throttle(monkeypatch, count):
    rows = capture_rejects(monkeypatch)
    log = AlertRejectLogger()
    now = datetime.now(UTC)
    items = [item(3 + index, id=str(index), now=now) for index in range(count)]
    log.log_round(items, [], RiskSettings(), AlertEngine(), [], now)
    assert len(rows) == 20
    details = [row for row in rows if "opportunity_id" in row]
    detail_count = 20 if count == 20 else 19
    assert [row["opportunity_id"] for row in details] == [
        str(index) for index in range(count - 1, count - detail_count - 1, -1)
    ]
    if count > 20:
        assert rows[-1]["suppressed_count"] == count - detail_count
    first_ids = {row["opportunity_id"] for row in details}
    rows.clear()
    log.log_round(items, [], RiskSettings(), AlertEngine(), [], now + timedelta(seconds=29))
    assert len(rows) <= 20
    assert first_ids.isdisjoint({row["opportunity_id"] for row in rows if "opportunity_id" in row})
    rows.clear()
    log.log_round(items, [], RiskSettings(), AlertEngine(), [], now + timedelta(seconds=30))
    assert 19 <= len(rows) <= 20
    assert rows[0]["opportunity_id"] == str(count - 1)


def test_message_preserves_hyperliquid_market_and_units():
    engine = BigSpreadAlertEngine()
    match = engine.evaluate([item(buy_exchange="hyperliquid", buy_raw_symbol="xyz:IMX",
                                   buy_dex="xyz", buy_price_multiplier=10, buy_contract_size_multiplier=100)],
                            [], risk_settings=RiskSettings(big_spread_alert_hits=1))[0]
    message = build_big_spread_message(match.rule, match.opportunity)
    for text in ("HL xyz 合约", "xyz:IMX", "价格倍率 10", "1张=100"):
        assert text in message
    assert len(message.splitlines()) == 5


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
@pytest.mark.parametrize("stage", ["evaluate", "delivery"])
async def test_fast_channel_exception_does_not_block_normal_alerts(monkeypatch, caplog, stage):
    normal = item(id="normal", symbol="NORMALUSDT")
    fast = item(id="fast", symbol="FASTUSDT", risk_labels=["TRANSIENT_SIGNAL"])
    rule = AlertRule(name="ordinary", consecutive_hits=1)
    app, stop = loop_app(normal, [rule])
    app.state.snapshot_store.set_opportunities([fast, normal])
    if stage == "evaluate":
        app.state.big_spread_alert_engine.evaluate = Mock(side_effect=RuntimeError("fast evaluate failed"))
    else:
        original = main._process_alert_match

        async def process(app, match, *args):
            if match.rule.id == "big_spread":
                raise RuntimeError("fast delivery failed")
            return await original(app, match, *args)

        monkeypatch.setattr(main, "_process_alert_match", process)
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    assert len(app.state.feishu_notifier.sent_texts) == 1
    assert app.state.alert_event_repo.events[0].rule_id == rule.id
    assert any("big spread" in record.message and record.exc_info for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_state,expected", [
    ({"status": True, "disableOpen": False}, "开"),
    ({"status": True, "disableOpen": True}, "关"),
    ({"status": False, "disableOpen": False}, "关"),
    ({"status": False, "disableOpen": True}, "关"),
    ({}, "未知"),
    ({"status": True}, "未知"),
    ({"disableOpen": False}, "未知"),
    ({"status": "false", "disableOpen": "false"}, "未知"),
])
async def test_existing_card_state_is_reported_without_remote_changes(remote_state, expected,
                                                                      astro_attempts_for_services):
    app, stop = loop_app(item())
    pair = {"name": "IMX", "type": "FF", "buyEx": "binance", "sellEx": "gate", **remote_state}
    original = dict(pair)
    client = FakeAstroClient([pair])
    app.state.astro_alert_service = AstroAlertService(
        client, Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        card_settings=AstroCardSettings(open_enabled=True, card_variant="non_gc"),
        add_restart_delay_seconds=0,
    )
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    assert client.list_calls == 1
    assert not client.added and not client.updated
    assert pair == original
    assert len(app.state.feishu_notifier.sent_texts) == 1
    message = app.state.feishu_notifier.sent_texts[0]
    assert message.splitlines()[-1] == f"卡片：已有卡片 IMX，当前开仓：{expected}"
    assert "Astro:" not in message


@pytest.mark.asyncio
async def test_existing_card_state_is_reported_when_missing_variant_is_created(astro_attempts_for_services):
    app, stop = loop_app(item())
    pair = {"name": "IMX", "type": "FF", "buyEx": "binance", "sellEx": "gate",
            "status": True, "disableOpen": False}
    original = dict(pair)
    client = FakeAstroClient([pair])
    app.state.astro_alert_service = AstroAlertService(
        client, Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    assert pair == original and not client.updated
    assert len(client.added) == 1
    assert client.added[0]["buyEx"] == "gc-binance"
    assert client.added[0]["status"] is False and client.added[0]["disableOpen"] is True
    message = app.state.feishu_notifier.sent_texts[0]
    assert message.splitlines()[-1] == "卡片：已建卡（未开仓，需手动开启）；已有卡片 IMX，当前开仓：开"
    assert "Astro:" not in message


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
        assert not app.state.feishu_notifier.sent_texts[0].startswith("【大价差】")
    else:
        assert app.state.feishu_notifier.sent_texts[0].startswith("【大价差】")


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
    assert "风险：信号不稳定" in app.state.feishu_notifier.sent_texts[0]
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
    assert "卡片：未创建（最新信号校验未通过）" in app.state.alert_event_repo.events[0].message


@pytest.mark.asyncio
async def test_history_keeps_big_spread_message_without_ordinary_rule_lookup():
    from app.api.routes_alerts import _resolve_event_message
    from app.models.alert import AlertEvent
    event = AlertEvent(rule_id="big_spread", opportunity_id="x", symbol="IMXUSDT", status="sent",
                       message="【大价差提醒】原始风险与卡片结果", created_at=datetime.now(UTC))
    assert await _resolve_event_message(None, event) == event.message


def compact_example():
    return item(
        3.01, symbol="BARDUSDT", close_spread_pct=3.11,
        buy_exchange="okx", sell_exchange="bybit",
        buy_ask=.1282, sell_bid=.1321,
        funding_rate_buy_pct=-.4834, funding_rate_sell_pct=-2,
        buy_funding_interval_hours=1, sell_funding_interval_hours=4,
        buy_volume_24h_usdt=136_000_000, sell_volume_24h_usdt=112_000_000,
        risk_labels=["FUNDING_AGAINST"],
    )


def test_compact_message_full_example():
    message = build_big_spread_message(
        AlertRule(id="big_spread", name="大价差提醒"), compact_example(),
        card_summary="已有卡片 BARD，当前开仓：关",
    )
    assert message == (
        "【大价差】BARD  开 3.01% / 平 3.11%\n"
        "OKX 合约 买 0.1282 → Bybit 合约 卖 0.1321\n"
        "资金费：买 -0.48%/1h · 卖 -2.00%/4h（持仓净 -0.017%/h）\n"
        "24h 成交额：1.36 亿 / 1.12 亿 U\n"
        "风险：资金费率逆风\n"
        "卡片：已有卡片 BARD，当前开仓：关"
    )


@pytest.mark.parametrize("spread,threshold,warn", [
    (9.999, 10, False), (10, 10, True), (10.01, 10, True),
    (3, 3, True), (10, 12, False),
])
def test_compact_huge_spread_warning_uses_configured_numeric_threshold(spread, threshold, warn):
    message = build_big_spread_message(AlertRule(name="fast"), item(spread), huge_spread_pct=threshold)
    assert ("⚠请人工核实" in message.splitlines()[0]) is warn


@pytest.mark.parametrize("updates,expected", [
    ({"funding_rate_buy_pct": None}, "资金费：买 - · 卖 -2.00%/4h（持仓净 -）"),
    ({"funding_rate_sell_pct": None}, "资金费：买 -0.48%/1h · 卖 -（持仓净 -）"),
    ({"buy_funding_interval_hours": None}, "资金费：买 - · 卖 -2.00%/4h（持仓净 -）"),
    ({"sell_funding_interval_hours": 0}, "资金费：买 -0.48%/1h · 卖 -（持仓净 -）"),
    ({"funding_rate_buy_pct": float('nan')}, "资金费：买 - · 卖 -2.00%/4h（持仓净 -）"),
    ({"funding_rate_buy_pct": .08, "funding_rate_sell_pct": .16,
      "buy_funding_interval_hours": 4, "sell_funding_interval_hours": 4},
     "资金费：买 0.08%/4h · 卖 0.16%/4h（持仓净 +0.020%/h）"),
    ({"funding_rate_buy_pct": None, "funding_rate_sell_pct": None},
     "资金费：买 - · 卖 -（持仓净 -）"),
    ({"buy_market_type": "spot"}, "资金费：买 - · 卖 -2.00%/4h（持仓净 -）"),
])
def test_compact_current_funding_missing_data_and_signs(updates, expected):
    candidate = compact_example().model_copy(update={
        "funding_next_rate_buy_pct": 999, "funding_next_rate_sell_pct": 999,
        **updates,
    })
    message = build_big_spread_message(AlertRule(name="fast"), candidate)
    assert message.splitlines()[2] == expected


@pytest.mark.parametrize("volume,expected", [
    (9999, "9999"), (10_000, "1 万"), (1_234_567, "123.46 万"),
    (100_000_000, "1 亿"), (136_000_000, "1.36 亿"),
    (0, "0"), (None, "-"), (float('nan'), "-"),
])
def test_compact_volume_units(volume, expected):
    candidate = item(buy_volume_24h_usdt=volume, sell_volume_24h_usdt=volume)
    assert build_big_spread_message(AlertRule(name="fast"), candidate).splitlines()[3] == (
        f"24h 成交额：{expected} / {expected} U"
    )


@pytest.mark.parametrize("price,expected", [
    (.1282, "0.1282"), (.0000000123456, "0.0000000123456"),
    (60345.67, "60345.7"), (100, "100"),
])
def test_compact_uses_executable_prices_with_significant_digits(price, expected):
    candidate = item(buy_ask=price, sell_bid=price, buy_bid=999, sell_ask=888)
    assert build_big_spread_message(AlertRule(name="fast"), candidate).splitlines()[1] == (
        f"Binance 合约 买 {expected} → Gate 合约 卖 {expected}"
    )


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("dex,raw,label", [
    ("xyz", "xyz:IMX", "HL xyz 合约（xyz:IMX）"),
    (None, "xyz:IMX", "HL xyz 合约（xyz:IMX）"),
    ("main", "IMX", "HL 合约（IMX）"),
])
def test_compact_hl_preserves_dex_and_raw_market(side, dex, raw, label):
    candidate = item(**{f"{side}_exchange": "hyperliquid", f"{side}_dex": dex,
                        f"{side}_raw_symbol": raw})
    assert label in build_big_spread_message(AlertRule(name="fast"), candidate).splitlines()[1]


def test_compact_spot_market_label_and_card_reason_limit():
    candidate = item(type="SF", buy_market_type="spot")
    reason = "原因" * 30
    message = build_big_spread_message(
        AlertRule(name="fast"), candidate, card_summary=f"未创建（{reason}）",
    )
    assert "Binance 现货 买" in message
    assert message.splitlines()[-1] == f"卡片：未创建（{reason[:40]}）"
    assert len(message.splitlines()) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted_legs", [(), ("买入腿",), ("卖出腿",), ("买入腿", "卖出腿")])
@pytest.mark.parametrize("labels", [[], ["FUNDING_AGAINST", "THIN_ORDER_BOOK"]])
async def test_compact_delivery_reuses_diagnostics_and_stores_identical_text(monkeypatch, restricted_legs, labels):
    candidate = compact_example().model_copy(update={"risk_labels": labels})
    app, stop = loop_app(candidate)
    app.state.settings_repo.risk_settings.huge_spread_pct = 3
    app.state.trade_availability_service = object()
    report = OpportunityTradeAvailabilityReport(
        text="【交易与充提状态】\n不应出现的诊断全文", opening_restricted=bool(restricted_legs),
        opening_restricted_legs=restricted_legs,
    )
    diagnostic = AsyncMock(return_value=report)
    monkeypatch.setattr(main, "build_opportunity_trade_availability_report", diagnostic)
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    diagnostic.assert_awaited_once()
    message = app.state.feishu_notifier.sent_texts[0]
    assert app.state.alert_event_repo.events[0].message == message
    assert "⚠请人工核实" in message.splitlines()[0]
    assert len(message.splitlines()) <= 6
    assert ("风险：资金费率逆风、盘口深度薄" in message) is bool(labels)
    assert ("交易状态：" in message) is bool(restricted_legs)
    if restricted_legs:
        assert f"交易状态：{'、'.join(restricted_legs)} 开仓受限" in message
    for removed in ("【交易与充提状态】", "诊断全文", "Astro:", "资金费率差", "预测", "口径",
                    "手续费", "滑点", "全部风险标签", "下一次结算", "结算周期"):
        assert removed not in message


@pytest.mark.asyncio
async def test_compact_diagnostic_failure_warns_both_unknown_legs(monkeypatch):
    app, stop = loop_app(item())
    app.state.trade_availability_service = object()
    monkeypatch.setattr(main, "build_opportunity_trade_availability_report",
                        AsyncMock(side_effect=TimeoutError()))
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    message = app.state.feishu_notifier.sent_texts[0]
    assert "交易状态：买入腿、卖出腿 开仓受限" in message
    assert len(message.splitlines()) == 6
    assert app.state.alert_event_repo.events[0].message == message


@pytest.mark.asyncio
async def test_compact_failed_delivery_keeps_event_equal_to_attempted_text():
    app, stop = loop_app(compact_example())
    attempted = []

    async def fail_send(*args, **kwargs):
        attempted.append(kwargs["prebuilt_text"])
        raise RuntimeError("test failure")

    app.state.feishu_notifier.send_alert = fail_send
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    event = app.state.alert_event_repo.events[0]
    assert event.status == "failed"
    assert event.message == attempted[0]
    assert len(event.message.splitlines()) == 6


@pytest.mark.asyncio
async def test_compact_live_send_disabled_keeps_short_event():
    app, stop = loop_app(compact_example())
    app.state.feishu_live_send_enabled = False
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    event = app.state.alert_event_repo.events[0]
    assert not app.state.feishu_notifier.sent_texts
    assert event.status == "muted"
    assert event.message.endswith("；飞书实时发送未启用")
    assert len(event.message.splitlines()) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["compact", "detailed"])
async def test_ordinary_delivery_matches_prechange_snapshot_byte_for_byte(monkeypatch, style):
    # Captured using the unchanged 477f866 delivery path, including diagnostics.
    snapshots = json.loads((Path(__file__).parent / "fixtures" /
                            "big_spread_compact_ordinary_messages.json").read_text(encoding="utf-8"))
    candidate = item(buy_funding_interval_hours=1, sell_funding_interval_hours=4)
    rule = AlertRule(id="ordinary-snapshot", name="ordinary", consecutive_hits=1)
    app, _ = loop_app(candidate, [rule])
    app.state.trade_availability_service = object()
    report = OpportunityTradeAvailabilityReport(
        text="【交易与充提状态】\n开仓路径：未知\n充提参考：未核实", opening_restricted=True,
    )
    monkeypatch.setattr(main, "build_opportunity_trade_availability_report", AsyncMock(return_value=report))
    await main._process_alert_match(
        app, main.AlertMatch(rule, candidate, []), RiskSettings(),
        AlertMessageTemplateSettings(format=style), app.state.alert_delivery_store,
        app.state.alert_event_repo, asyncio.Lock(), Settings(),
    )
    assert app.state.feishu_notifier.sent_texts == [snapshots[style]]
    assert app.state.alert_event_repo.events[0].message == snapshots[style]
