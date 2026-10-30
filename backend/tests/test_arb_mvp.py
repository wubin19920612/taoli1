import hashlib
import hmac
import json
import logging
import re
from decimal import Decimal

import httpx
import pytest

from app.models.settings import AstroCardSettings
from app.services.arb_mvp_client import ArbMvpClient
from app.services.arb_mvp_planner import ArbMvpPlanner
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig
from test_astro_planner import opportunity


def arb_opportunity(**updates):
    return opportunity().model_copy(update={
        "buy_exchange": "hyperliquid", "sell_exchange": "lighter",
        "buy_raw_symbol": "BTC", "sell_raw_symbol": "BTC", **updates,
    })


@pytest.mark.parametrize("raw,dex", [("BTC", None), ("xyz:GOLD", "xyz")])
def test_mapping_raw_markets_and_safe_defaults(raw, dex):
    opp = arb_opportunity(buy_raw_symbol=raw, sell_raw_symbol=raw.split(":")[-1])
    settings = AstroCardSettings(max_trade_usdt=100, open_enabled=True)
    plan = ArbMvpPlanner(settings).plan(opp, "arb_paper")
    assert plan.can_submit
    assert plan.card_variant == "both" and plan.route_variants == []
    assert plan.pair["legA"] == {"venue": "hyperliquid", "symbol": raw.split(":")[-1], "leverage": settings.leverage, **({"dex": dex} if dex else {})}
    assert plan.pair["legB"]["venue"] == "lighter"
    assert plan.pair["disableOpen"] is True
    assert plan.pair["disableClose"] is False
    assert plan.pair["maxSlippage"] == "0.002"
    assert plan.sdk_payload == {"action": "add", "pair": plan.pair}


@pytest.mark.parametrize("funding,spread", [(0.02, 0.8), (-1.0, 0.8), (0, -0.2), (0, 0.000001)])
def test_thresholds_match_astro_decimal_values(funding, spread):
    settings = AstroCardSettings(max_trade_usdt=100)
    opp = arb_opportunity(net_funding_next_pct=funding, open_spread_pct=spread)
    # Use an Astro-supported route to isolate the threshold algorithm.
    astro = AstroPairPlanner(AstroPlannerConfig.from_card_settings(settings)).plan(
        opp.model_copy(update={"sell_exchange": "bitget"}), allow_manual_override=True)
    arb = ArbMvpPlanner(settings).plan(opp, "arb_paper")
    assert arb.pair["openSpread"] == astro.pair["openPosition"]
    assert arb.pair["closeSpread"] == astro.pair["closePosition"]


def test_rh_paper_only():
    planner = ArbMvpPlanner(AstroCardSettings(max_trade_usdt=100))
    opp = arb_opportunity(sell_exchange="rh-lighter")
    assert planner.plan(opp, "arb_paper").pair["legB"]["venue"] == "lighter-rh"
    assert not planner.plan(opp, "arb_live").can_submit


@pytest.mark.parametrize("updates", [
    {"buy_exchange": "bitget"}, {"buy_market_type": "spot"},
    {"sell_raw_symbol": "ETH"}, {"buy_raw_symbol": None},
    {"identity_enforced": True, "validation": None},
])
def test_blocked_mappings(updates):
    plan = ArbMvpPlanner(AstroCardSettings(max_trade_usdt=100)).plan(arb_opportunity(**updates), "arb_paper")
    assert not plan.can_submit and plan.blockers


@pytest.mark.parametrize("updates", [
    {"min_notional": 9}, {"min_notional": 20, "max_notional": 10},
    {"max_notional": 101}, {"leverage": 101}, {"leverage": 1.5},
    {"max_trade_usdt": float("inf")},
])
def test_invalid_sizes(updates):
    settings = AstroCardSettings(max_trade_usdt=100).model_copy(update=updates)
    assert not ArbMvpPlanner(settings).plan(arb_opportunity(), "arb_live").can_submit


@pytest.mark.parametrize("target,exchange,raw", [
    ("arb_live", "lighter", "ETH"),
    ("arb_paper", "rh-lighter", "xyz:GOLD"),
])
@pytest.mark.parametrize("spread", [0.15, -0.2, 0.000001])
def test_pair_matches_strict_arb_mvp_contract(target, exchange, raw, spread):
    settings = AstroCardSettings(
        max_trade_usdt=100, min_notional=15, max_notional=25, leverage=3,
    )
    plan = ArbMvpPlanner(settings).plan(arb_opportunity(
        symbol=raw.split(":")[-1], buy_raw_symbol=raw,
        sell_raw_symbol=raw.split(":")[-1], sell_exchange=exchange,
        open_spread_pct=spread,
    ), target)
    assert plan.can_submit
    pair = plan.pair
    assert set(pair) == {
        "name", "type", "legA", "legB", "openSpread", "closeSpread",
        "disableOpen", "disableClose", "maxPositionUsd", "minOrderUsd",
        "maxOrderUsd", "maxSlippage",
    }
    assert isinstance(pair["name"], str) and 1 <= len(pair["name"]) <= 40
    assert pair["type"] == "FF"
    for key in ("legA", "legB"):
        leg = pair[key]
        assert {"venue", "symbol", "leverage"} <= set(leg)
        assert set(leg) <= {"venue", "dex", "symbol", "leverage"}
        assert isinstance(leg["venue"], str) and isinstance(leg["symbol"], str)
        assert type(leg["leverage"]) is int and leg["leverage"] == 3
        if "dex" in leg:
            assert isinstance(leg["dex"], str) and leg["dex"] == "xyz"
    for key in ("maxPositionUsd", "minOrderUsd", "maxOrderUsd",
                "openSpread", "closeSpread", "maxSlippage"):
        assert isinstance(pair[key], str), key
        assert re.fullmatch(r"-?\d+(?:\.\d+)?", pair[key]), key
    assert Decimal(pair["openSpread"]) > Decimal(pair["closeSpread"])
    assert pair["disableOpen"] is True and pair["disableClose"] is False
    assert pair["maxPositionUsd"] == "100"
    assert pair["minOrderUsd"] == "15" and pair["maxOrderUsd"] == "25"
    assert pair["maxSlippage"] == "0.002"
    assert plan.sdk_payload == {"action": "add", "pair": pair}


@pytest.mark.parametrize("maximum,expected", [(100.0, "100"), (1e21, "1000000000000000000000")])
def test_sizes_use_plain_decimal_strings_without_rounding(maximum, expected):
    settings = AstroCardSettings(
        max_trade_usdt=maximum, min_notional=10.00001, max_notional=25.125,
    )
    plan = ArbMvpPlanner(settings).plan(arb_opportunity(), "arb_live")
    assert plan.can_submit
    assert plan.pair["maxPositionUsd"] == expected
    assert plan.pair["minOrderUsd"] == "10.00001"
    assert plan.pair["maxOrderUsd"] == "25.125"


@pytest.mark.parametrize("symbol,allowed", [
    ("A", True), ("A" * 40 + "USDT", True), ("A" * 41 + "USDT", False), (" ", False),
])
def test_name_length_is_validated_after_base_name_mapping(symbol, allowed):
    plan = ArbMvpPlanner(AstroCardSettings(max_trade_usdt=100)).plan(
        arb_opportunity(symbol=symbol), "arb_live",
    )
    assert plan.can_submit is allowed
    if allowed:
        assert 1 <= len(plan.pair["name"]) <= 40
    else:
        assert plan.pair is None and plan.sdk_payload is None
        assert any("名称" in blocker and "40" in blocker for blocker in plan.blockers)


@pytest.mark.asyncio
async def test_signature_is_over_sent_bytes_and_client_forces_disable_open(monkeypatch, caplog):
    monkeypatch.setattr("app.services.arb_mvp_client.time.time", lambda: 1234.5)
    monkeypatch.setattr("app.services.arb_mvp_client.secrets.token_hex", lambda n: "a" * (2 * n))
    seen = []
    def handler(request):
        seen.append(request)
        assert request.method == "POST" and request.url.path == "/api/pairs"
        expected = hmac.new(b"secret-test", b"1234500\n" + b"a" * 24 + b"\nPOST\n/api/pairs\n" + request.content, hashlib.sha256).hexdigest()
        assert request.headers["x-sign"] == expected
        assert request.headers["x-timestamp"] == "1234500"
        assert json.loads(request.content) == {"action": "add", "pair": {"name": "黄金", "disableOpen": True}}
        assert b'": ' not in request.content
        return httpx.Response(200, json={"code": 0, "data": {"config": {"name": "黄金"}}})
    with caplog.at_level(logging.DEBUG):
        result = await ArbMvpClient("http://arb.test", "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "黄金", "disableOpen": False})
    assert result.success and result.pair_name == "黄金"
    assert len(seen) == 1
    assert "secret-test" not in caplog.text and "x-sign" not in caplog.text
    assert seen[0].headers["x-sign"] not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expected", [
    (409, {"code": -1, "message": "DUPLICATE_NAME"}, "重名"),
    (409, {"code": -1, "error": "LIMIT_EXCEEDED"}, "上限"),
    (400, {"code": -1}, "配置"), (401, {"code": -1}, "签名"),
    (429, {"code": -1}, "频繁"), (200, None, "结果未知"),
    (302, None, "结果未知"),
])
async def test_client_errors(status, body, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body, headers={"location": "http://other.test"})
    result = await ArbMvpClient("http://arb.test", "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "BTC"})
    assert not result.success and expected in result.message
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_timeout_is_not_retried_or_leaked(caplog):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret-test x-sign", request=request)
    result = await ArbMvpClient("http://arb.test", "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "BTC"})
    assert not result.success and "结果未知" in result.message and "不要直接重试" in result.message
    assert len(calls) == 1 and calls[0].extensions["timeout"]["read"] == 10
    assert "secret-test" not in caplog.text and "x-sign" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status,prefix", [
    (400, "arb-mvp 卡片配置错误"),
    (409, "卡片重名（DUPLICATE_NAME），未自动改名或重试"),
])
async def test_rejection_reason_is_sanitized_and_limited(status, prefix, caplog):
    calls = []
    reason = "Invalid config: legA.venue\x00\t\r\x1b\x7f\x85\u200b ...\n" + "原因" * 200
    expected = ("Invalid config: legA.venue ...\n" + "原因" * 200)[:300]

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"code": -1, "message": reason})

    with caplog.at_level(logging.DEBUG):
        result = await ArbMvpClient("http://arb.test", "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "BTC"})
    assert not result.success and result.message == prefix + "：" + expected
    assert len(calls) == 1
    assert "secret-test" not in result.message and "x-sign" not in result.message
    assert "secret-test" not in caplog.text and "x-sign" not in caplog.text
    assert calls[0].headers["x-sign"] not in result.message + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 409])
async def test_rejection_reason_redacts_echoed_credentials_before_truncation(status, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        reason = ("Invalid config: secret-\x00test X-\tsign=" + request.headers["x-sign"]
                  + "\n" + "a" * 280 + "secret-test")
        return httpx.Response(status, json={"code": -1, "message": reason})

    with caplog.at_level(logging.DEBUG):
        result = await ArbMvpClient("http://arb.test", "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "BTC"})
    assert not result.success and "Invalid config:" in result.message
    assert "secret-test" not in result.message + caplog.text
    assert "x-sign" not in (result.message + caplog.text).lower()
    assert calls[0].headers["x-sign"] not in result.message + caplog.text
    assert len(result.message.split("：", 1)[1]) <= 300


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "", 123, {"detail": "bad"}, ["bad"]])
@pytest.mark.parametrize("status,prefix", [
    (400, "arb-mvp 卡片配置错误"), (409, "卡片数量已达上限（LIMIT_EXCEEDED）"),
])
async def test_rejection_without_string_reason_keeps_chinese_prefix(reason, status, prefix):
    def handler(request):
        return httpx.Response(status, json={"code": -1, "error": "LIMIT_EXCEEDED", "message": reason})

    result = await ArbMvpClient("http://arb.test", "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "BTC"})
    assert not result.success and result.message == prefix


@pytest.mark.asyncio
async def test_limit_rejection_includes_reason():
    def handler(request):
        return httpx.Response(409, json={"code": -1, "error": "LIMIT_EXCEEDED", "message": "Maximum pairs reached"})

    result = await ArbMvpClient("http://arb.test", "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "BTC"})
    assert result.message == "卡片数量已达上限（LIMIT_EXCEEDED）：Maximum pairs reached"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [
    (401, "arb-mvp 签名验证失败"), (429, "arb-mvp 请求过于频繁，请稍后再试"),
    (500, "结果未知，请到 arb-mvp 界面确认是否已建卡，不要直接重试"),
])
async def test_other_status_codes_do_not_surface_response_message(status, expected):
    def handler(request):
        return httpx.Response(status, json={"code": -1, "message": "secret-test x-sign upstream reason"})

    result = await ArbMvpClient("http://arb.test", "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "BTC"})
    assert not result.success and result.message == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://arb.test:bad", "ftp://arb.test", "http:///", "/relative"])
async def test_invalid_url_fails_before_transport(url, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"config": {"name": "BTC"}}})

    result = await ArbMvpClient(url, "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "BTC"})
    assert not result.success and result.message == "arb-mvp 地址配置错误"
    assert len(calls) == 0
    assert "secret-test" not in result.message + caplog.text


@pytest.mark.asyncio
async def test_unexpected_transport_error_is_unknown_without_retry(caplog):
    calls = []

    def handler(request):
        calls.append(request)
        raise RuntimeError("secret-test x-sign transport failure")

    result = await ArbMvpClient("http://arb.test", "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "BTC"})
    assert not result.success and result.message == "结果未知，请到 arb-mvp 界面确认是否已建卡，不要直接重试"
    assert len(calls) == 1
    assert "secret-test" not in result.message + caplog.text
    assert "x-sign" not in result.message + caplog.text


@pytest.mark.asyncio
async def test_transport_cancellation_propagates_without_retry():
    import asyncio

    calls = []

    def handler(request):
        calls.append(request)
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await ArbMvpClient("http://arb.test", "secret-test", transport=httpx.MockTransport(handler)).add_pair({"name": "BTC"})
    assert len(calls) == 1
