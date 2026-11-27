import asyncio
import json
import logging
from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import test_mobile_card_control as controls
from test_mobile_card_control import CONTROL, FREE, PAIR, unlock_code

from app.core.config import Settings, get_settings
from app.services.astro_client import AstroClientError

mobile_app = controls.mobile_app
clock = controls.clock
phone = controls.phone
enabled = controls.enabled

PARAMS = "/api/mobile/cards/params"
CARD = dict(PAIR, sellEx="gate", openPosition="0.0255", closePosition="0.005", maxTradeUSDT="10.00",
            leverage="1", minNotional="10.000", maxNotional="60", aExPosition="2", bExPosition="2")
OLD = {"open_condition_pct": 2.55, "close_condition_pct": 0.5, "max_trade_usdt": 10,
       "leverage": 1, "min_notional": 10, "max_notional": 60}


def payload(changes, expected=None):
    return {"card_id": CARD["id"], "changes": changes,
            "expected": {key: OLD.get(key) for key in changes} if expected is None else expected}


@pytest.fixture
async def unlocked(enabled, clock):
    app, client = enabled
    assert (await unlock_code(client, clock.code())).status_code == 200
    app.state.astro_client.list_pairs.return_value = [CARD.copy()]
    app.state.mobile_push.send_urgent = AsyncMock()
    return app, client


async def test_session_switch_and_unlock_gate_even_noops(enabled):
    app, client = enabled
    body = payload({"leverage": 1})
    response = await client.post(PARAMS, json=body)
    assert (response.status_code, response.json()["detail"]) == (403, "请先解锁")
    app.state.settings = replace(app.state.settings, mobile_card_control_enabled=False)
    assert (await client.post(PARAMS, json=body)).status_code == 503
    client.cookies.clear()
    assert (await client.post(PARAMS, json=body, headers={"X-Api-Token": "a" * 64})).status_code == 401
    app.state.astro_client.list_pairs.assert_not_awaited()
    app.state.astro_client.update_pair.assert_not_awaited()


async def test_schema_and_finite_numbers_fail_closed(unlocked):
    app, client = unlocked
    cases = [
        (payload({}), "修改"), (payload({"leverage": 2}, {}), "杠杆"),
        (dict(payload({"leverage": 2}), status=True), "字段"),
        (payload({"disable_open": 1}), "字段"),
        (payload({"leverage": 2}, {"leverage": 1, "aHlDex": 2}), "字段"),
        (payload({"open_condition_pct": None}), "开仓条件"),
        (payload({"close_condition_pct": float("nan")}), "清仓条件"),
        (payload({"max_trade_usdt": float("inf")}), "单次下单"),
        (payload({"min_notional": True}), "最小持仓"), (payload({"leverage": "3"}), "杠杆"),
        (payload({"max_notional": 100}, {"max_notional": False}), "最大持仓"),
        (payload({"leverage": 2}, {"leverage": float("-inf")}), "杠杆"),
    ]
    for body, label in cases:
        response = await client.post(PARAMS, content=json.dumps(body), headers={"Content-Type": "application/json"})
        assert response.status_code == 422 and label in response.json()["detail"], response.text
    app.state.astro_client.update_pair.assert_not_awaited()
    app.state.mobile_push.send_urgent.assert_not_awaited()


async def test_validates_all_fields_and_final_relationships_before_any_write(unlocked):
    app, client = unlocked
    for changes, label in [
        ({"open_condition_pct": 0.5}, "开仓条件"), ({"close_condition_pct": 3}, "清仓条件"),
        ({"leverage": 2, "open_condition_pct": -50.1}, "开仓条件"),
        ({"close_condition_pct": 50.1}, "清仓条件"), ({"leverage": 1.5}, "杠杆"),
        ({"leverage": 0}, "杠杆"), ({"leverage": 11}, "杠杆"),
        ({"min_notional": 0}, "最小持仓"), ({"max_trade_usdt": -1}, "单次下单"),
        ({"min_notional": 61}, "最小持仓"), ({"max_trade_usdt": 61}, "单次下单"),
        ({"max_notional": 8}, "最大持仓"),
        ({"max_notional": 90, "min_notional": 91, "leverage": 5}, "最小持仓"),
    ]:
        response = await client.post(PARAMS, json=payload(changes))
        assert response.status_code == 422 and label in response.json()["detail"], response.text
    app.state.astro_client.update_pair.assert_not_awaited()
    app.state.mobile_push.send_urgent.assert_not_awaited()
    assert (await client.get("/api/mobile/alerts")).json() == []


async def test_expected_is_checked_on_locked_reread_including_extra_expected_keys(unlocked):
    app, client = unlocked
    astro = app.state.astro_client
    for field, stale in [("openPosition", "0.02550002"), ("maxTradeUSDT", "10.000000002"), ("name", "OTHER")]:
        astro.list_pairs.side_effect = [[CARD], [dict(CARD, **{field: stale})]]
        response = await client.post(PARAMS, json=payload({"leverage": 2}, OLD))
        assert (response.status_code, response.json()["detail"]) == (409, "卡片已变化，请刷新")
    astro.list_pairs_cached.assert_not_awaited()
    astro.update_pair.assert_not_awaited()
    app.state.mobile_push.send_urgent.assert_not_awaited()


async def test_noop_at_expected_tolerance_boundaries_does_not_write_or_notify(unlocked):
    app, client = unlocked
    app.state.astro_client.list_pairs.return_value = [dict(CARD, openPosition="0.01")]
    response = await client.post(PARAMS, json=payload(
        {"open_condition_pct": 1, "max_trade_usdt": 10},
        {"open_condition_pct": 1.000001, "max_trade_usdt": 10.000000001},
    ))
    assert response.status_code == 200, response.text
    assert response.json()["changed"] == [] and response.json()["card"]["open_condition"] == 1
    app.state.astro_client.update_pair.assert_not_awaited()
    app.state.mobile_push.send_urgent.assert_not_awaited()
    assert (await client.get("/api/mobile/alerts")).json() == []


async def test_one_leverage_edit_preserves_locked_latest_card_including_exact_dex(unlocked):
    app, client = unlocked
    astro = app.state.astro_client
    lock = app.state.astro_alert_service.pair_lock(CARD["name"])
    latest = dict(CARD, aHlDex="io", aRawSymbol="io:OPENAI", unknown={"orders": [3, 4]})
    reads = []

    async def read():
        reads.append(lock.locked())
        return [latest if lock.locked() else CARD]

    async def write(pair):
        assert lock.locked() and pair == dict(latest, leverage="10")
        astro.list_pairs_cached.return_value = [pair]

    astro.list_pairs.side_effect = read
    astro.update_pair.side_effect = write
    assert (await client.post("/api/mobile/pins", json={"card_id": CARD["id"], "pinned": True})).status_code == 200
    response = await client.post(PARAMS, json=payload({"leverage": 10, "max_trade_usdt": 10}))
    assert response.status_code == 200, response.text
    assert reads == [False, True] and latest["leverage"] == "1"
    astro.update_pair.assert_awaited_once()
    astro.list_pairs_cached.assert_not_awaited()
    summary_card, = (await client.get("/api/mobile/summary")).json()["cards"]
    assert response.json() == {"ok": True, "card_id": CARD["id"], "changed": ["leverage"], "card": summary_card}
    assert summary_card["holding"] and summary_card["pinned_at"] and summary_card["leverage"] == 10


@pytest.mark.parametrize("kind", ["FF", "FFR"])
@pytest.mark.parametrize("opening,closing", [(50, 0.4), (-0.5, -50)])
async def test_condition_round_trip_both_modes_including_negative_and_boundaries(unlocked, kind, opening, closing):
    app, client = unlocked
    ratio = kind.endswith("R")
    pair = dict(CARD, type=kind, regressionValue="2.5", openPosition="2.5" if ratio else "0", closePosition="Infinity")
    astro = app.state.astro_client
    astro.list_pairs.return_value = [pair]
    changes = {"open_condition_pct": opening, "close_condition_pct": closing}
    response = await client.post(PARAMS, json=payload(changes, {"open_condition_pct": 0, "close_condition_pct": None}))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["changed"] == list(changes)
    written = astro.update_pair.await_args.args[0]
    for key, pct in [("open", opening), ("close", closing)]:
        raw = written[key + "Position"]
        assert isinstance(raw, str) and "e" not in raw.lower()
        expected_raw = 2.5 * (200 - pct) / (200 + pct) if ratio else pct / 100
        assert float(raw) == pytest.approx(expected_raw, rel=1e-14)
        assert result["card"][key + "_condition"] == pytest.approx(pct, abs=1e-6)
    astro.update_pair.assert_awaited_once()


async def test_ratio_rejects_missing_reference_and_failed_round_trip_before_write(unlocked):
    app, client = unlocked
    for ref in [None, 0, -2, "Infinity", True, "5e-324"]:
        app.state.astro_client.list_pairs.return_value = [dict(CARD, type="FR", regressionValue=ref, openPosition=None)]
        response = await client.post(PARAMS, json=payload({"leverage": 2, "open_condition_pct": 30},
                                                         {"leverage": 1, "open_condition_pct": None}))
        assert response.status_code == 422, response.text
        assert ("开仓条件" if ref == "5e-324" else "比值卡缺少基准，请在网页改") in response.json()["detail"]
    app.state.astro_client.update_pair.assert_not_awaited()


@pytest.mark.parametrize("inactive,expected", [("Infinity", None), (None, None), ("-1", -100), ("-2", -200)])
async def test_inactive_other_side_skips_ordering_but_expected_uses_actual_percent(unlocked, inactive, expected):
    app, client = unlocked
    astro = app.state.astro_client
    astro.list_pairs.return_value = [dict(CARD, openPosition=inactive)]
    expected_values = {"close_condition_pct": 0.5, "open_condition_pct": expected}
    if expected is not None:
        response = await client.post(PARAMS, json=payload({"close_condition_pct": 2}, dict(expected_values, open_condition_pct=None)))
        assert response.status_code == 409
        astro.update_pair.assert_not_awaited()
    response = await client.post(PARAMS, json=payload({"close_condition_pct": 2}, expected_values))
    assert response.status_code == 200 and response.json()["card"]["open_auto"] is False
    assert astro.update_pair.await_args.args[0]["openPosition"] == inactive


async def test_multiple_fields_audit_one_alert_and_push_outside_lock(unlocked, caplog):
    app, client = unlocked
    title = CARD["name"] + " 参数已修改"
    body = "开仓条件 2.55% → 2.30%；单次下单 10 → 20 U；杠杆 1 → 3 倍（手机）"
    push_lock_states = []

    async def push(**notice):
        push_lock_states.append(app.state.astro_alert_service.pair_lock(CARD["name"]).locked())
        raise RuntimeError("offline push")

    app.state.mobile_push.send_urgent.side_effect = push
    with caplog.at_level(logging.INFO, logger="astro_card_update"):
        response = await client.post(PARAMS, json=payload({"open_condition_pct": 2.3, "max_trade_usdt": 20, "leverage": 3}))
    assert response.status_code == 200, response.text
    app.state.astro_client.update_pair.assert_awaited_once_with(dict(CARD, openPosition="0.023", maxTradeUSDT="20", leverage="3"))
    assert [record.message for record in caplog.records if record.name == "astro_card_update"] == [
        "astro_card_params pair=OPENAI changes=open_condition_pct:2.55→2.3,max_trade_usdt:10→20,leverage:1→3 auth=验证码"
    ]
    event, = (await client.get("/api/mobile/alerts")).json()
    assert (event["kind"], event["title"], event["body"]) == ("card_control", title, body)
    assert push_lock_states == [False]
    app.state.mobile_push.send_urgent.assert_awaited_once_with(
        kind="card_control", title=title, body=body, card_id=CARD["id"], card_name=CARD["name"],
    )


async def test_upstream_rejection_and_post_write_alert_failure_are_distinct(unlocked):
    app, client = unlocked
    astro = app.state.astro_client
    astro.update_pair.side_effect = AstroClientError("private upstream body", 502)
    response = await client.post(PARAMS, json=payload({"leverage": 2}))
    assert (response.status_code, response.json()) == (502, {"detail": "Astro 拒绝修改（HTTP 502）"})
    app.state.mobile_push.send_urgent.assert_not_awaited()
    assert (await client.get("/api/mobile/alerts")).json() == []
    astro.update_pair.side_effect = None
    app.state.mobile_alert_repo.record = AsyncMock(side_effect=ValueError("Connection closed"))
    response = await client.post(PARAMS, json=payload({"leverage": 2}))
    assert (response.status_code, response.json()) == (503, {"detail": "卡片已修改，提醒记录写入失败，请刷新"})
    assert astro.update_pair.await_count == 2
    app.state.mobile_push.send_urgent.assert_awaited_once()


async def test_params_and_control_share_rate_limit(unlocked):
    app, client = unlocked
    for _ in range(19):
        assert (await client.post(CONTROL, json=dict(FREE, value=False))).status_code == 200
    assert (await client.post(PARAMS, json=payload({"leverage": 1}))).status_code == 200
    assert (await client.post(CONTROL, json=FREE)).status_code == 429
    assert (await client.post(PARAMS, json=payload({"leverage": 2}))).status_code == 429
    app.state.astro_client.update_pair.assert_not_awaited()


async def test_configured_leverage_limit(unlocked, monkeypatch):
    app, client = unlocked
    assert Settings().mobile_max_leverage == 10
    monkeypatch.setattr("app.core.config.find_dotenv", lambda **kwargs: "")
    monkeypatch.setenv("MOBILE_MAX_LEVERAGE", "4")
    get_settings.cache_clear()
    try:
        limit = get_settings().mobile_max_leverage
    finally:
        get_settings.cache_clear()
    assert limit == 4
    app.state.settings = replace(app.state.settings, mobile_max_leverage=limit)
    response = await client.post(PARAMS, json=payload({"leverage": 5}))
    assert response.status_code == 422 and "4" in response.json()["detail"]
    app.state.astro_client.update_pair.assert_not_awaited()
    assert (await client.post(PARAMS, json=payload({"leverage": 4}))).status_code == 200


async def test_missing_old_amounts_and_large_finite_values_have_no_money_cap(unlocked):
    app, client = unlocked
    astro = app.state.astro_client
    pair = dict(CARD, maxTradeUSDT=None, minNotional=None, maxNotional=None, leverage="1.5")
    astro.list_pairs.return_value = [pair]
    large = 10**35 + 1
    changes = {"max_trade_usdt": large, "min_notional": 5e-10, "leverage": 2}
    response = await client.post(PARAMS, json=payload(changes, dict.fromkeys(changes)))
    assert response.status_code == 200, response.text
    assert response.json()["changed"] == list(changes) and response.json()["card"]["max_notional"] is None
    astro.update_pair.assert_awaited_once_with(dict(pair, maxTradeUSDT=str(large), minNotional="0.0000000005", leverage="2"))


async def test_amount_order_uses_exact_stored_limit_not_rounded_display_float(unlocked):
    app, client = unlocked
    astro = app.state.astro_client
    astro.list_pairs.return_value = [dict(CARD, maxNotional="9007199254740995")]
    response = await client.post(PARAMS, json=payload({"max_trade_usdt": 9007199254740996}))
    assert response.status_code == 422 and "最大持仓" in response.json()["detail"]
    astro.update_pair.assert_not_awaited()


async def test_explicit_small_amount_change_is_not_an_expected_value_tolerance_noop(unlocked):
    app, client = unlocked
    astro = app.state.astro_client
    small = dict(CARD, maxTradeUSDT="0.0000000009")
    astro.list_pairs.return_value = [small]
    response = await client.post(PARAMS, json=payload({"max_trade_usdt": 1e-10}, {"max_trade_usdt": 9e-10}))
    assert response.status_code == 200 and response.json()["changed"] == ["max_trade_usdt"]
    assert Decimal(astro.update_pair.await_args.args[0]["maxTradeUSDT"]) == Decimal("1e-10")


async def test_stored_conditions_must_still_be_ordered_after_round_trip(unlocked):
    app, client = unlocked
    astro = app.state.astro_client
    astro.list_pairs.return_value = [dict(CARD, type="FR", regressionValue=1, openPosition=None, closePosition=None)]
    changes = {"open_condition_pct": 1e-15, "close_condition_pct": 0}
    response = await client.post(PARAMS, json=payload(changes, dict.fromkeys(changes)))
    assert response.status_code == 422 and "开仓条件" in response.json()["detail"]
    astro.update_pair.assert_not_awaited()


@pytest.mark.parametrize("race,status", [("expired", 403), ("disabled", 503), ("projection_error", 503)])
async def test_rechecks_write_authority_after_building_summary_card(unlocked, clock, monkeypatch, race, status):
    app, client = unlocked
    get_risk = app.state.settings_repo.get_risk_settings

    async def while_projecting():
        if race == "expired":
            clock.now += 301
        elif race == "disabled":
            app.state.settings = replace(app.state.settings, astro_dry_run_only=True)
        else:
            raise ValueError("offline projection failed")
        return await get_risk()

    monkeypatch.setattr(app.state.settings_repo, "get_risk_settings", while_projecting)
    assert (await client.post(PARAMS, json=payload({"leverage": 2}))).status_code == status
    app.state.astro_client.update_pair.assert_not_awaited()
    app.state.mobile_push.send_urgent.assert_not_awaited()


async def test_concurrent_edits_with_same_expected_only_commit_once(unlocked):
    app, client = unlocked
    astro = app.state.astro_client
    latest = [CARD.copy()]
    astro.list_pairs.return_value = latest

    async def save(pair):
        latest[0] = pair

    astro.update_pair.side_effect = save
    responses = await asyncio.gather(*(client.post(PARAMS, json=payload({"leverage": value})) for value in (2, 3)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert str(next(response for response in responses if response.status_code == 200).json()["card"]["leverage"]) == latest[0]["leverage"]
    astro.update_pair.assert_awaited_once()
    app.state.mobile_push.send_urgent.assert_awaited_once()
