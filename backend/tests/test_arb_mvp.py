import hashlib
import hmac
import json
import logging

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
