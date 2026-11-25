import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_mobile_api import mobile_app  # noqa: F401 - production session guard
from test_mobile_pins import login
from test_pair_range_backtest import calculate, data_for, request_for

from app.api import routes_mobile
from app.models.settings import FeeSettings
from app.services.pair_spread_query import PairSpreadQueryError


PATH = "/api/mobile/range-backtest"
START = datetime(2025, 1, 1, tzinfo=UTC)
PAIR = {"id": "Card_A-1", "name": "ANTHROPIC", "type": "SF", "buyEx": "gc-binance", "sellEx": "hl"}


def body(start=START, end=START + timedelta(minutes=1), **updates):
    return {"card_id": PAIR["id"], "start_at": start.isoformat(), "end_at": end.isoformat(), **updates}


@pytest.fixture
async def phone(mobile_app):
    app, client = mobile_app
    await login(client, "mobile_readonly", int(time.time() // 30) - 1)
    app.state.astro_client.list_pairs_cached.return_value = [PAIR.copy()]
    query = request_for()
    result = calculate(query, data_for(query, query.leg1), data_for(query, query.leg2))
    service = SimpleNamespace(query_range_backtest=AsyncMock(return_value=result), aclose=AsyncMock())
    app.state.pair_spread_query_service_factory = lambda: service
    return app, client, service


@pytest.mark.parametrize("dex,raw_symbol", [("io", "ANTHUSDT"), ("main", "ANTHROPICUSDT")])
async def test_readonly_query_uses_resolved_legs_fees_and_website_result(phone, dex, raw_symbol):
    app, client, service = phone
    pair = dict(PAIR, bEffectiveHlDex="io") if dex == "io" else PAIR
    app.state.astro_client.list_pairs_cached.return_value = [dict(PAIR, id="other", name="BTC"), pair]
    fees = FeeSettings(spot_fee_pct=0.02, future_fee_pct=0.03)
    app.state.market_collector = SimpleNamespace(fee_settings=fees)
    end = START + timedelta(hours=72)
    response = await client.post(PATH, json=body(end=end, notional_usd=10_000_000))
    assert response.status_code == 200, response.text
    call = service.query_range_backtest.await_args
    query, = call.args
    assert call.kwargs == {"fee_settings": fees}
    assert (query.direction, query.notional_usd, query.start_at, query.end_at) == (
        "long1_short2", 10_000_000, START, end,
    )
    assert query.leg1.model_dump() == dict(exchange="binance", symbol="ANTHROPICUSDT", market_type="spot", dex=None)
    assert query.leg2.model_dump() == dict(exchange="hyperliquid", symbol=raw_symbol, market_type="future", dex=dex)
    assert response.json() == {
        **service.query_range_backtest.return_value.model_dump(mode="json"),
        "direction": "long1_short2", "notional_usd": 10_000_000,
        "leg1_label": "gc-binance ANTHROPIC", "leg2_label": f"hl {dex}:{raw_symbol.removesuffix('USDT')}",
    }
    service.aclose.assert_awaited_once()
    assert (await client.post("/api/pair-spread/range-backtest", json={})).status_code == 403
    client.cookies.clear()
    client.headers["X-Api-Token"] = "a" * 64
    assert (await client.post(PATH, json=body())).status_code == 401


@pytest.mark.parametrize("position,override,expected", [
    ({}, {}, 1000),
    ({"aExPosition": 2}, {"notional_usd": None}, 1000),
    ({"aExPosition": "-2", "avgOpenAExPrice": "120", "bExPosition": 3, "avgOpenBExPrice": 90}, {}, 510),
    ({"aExPosition": "-2", "avgOpenAExPrice": "120", "bExPosition": 3}, {}, 240),
    ({"aExPosition": 3, "avgOpenAExPrice": 120}, {"notional_usd": 0.25}, 0.25),
])
async def test_notional_matches_summary_and_supports_overrides(phone, position, override, expected):
    app, client, service = phone
    app.state.astro_client.list_pairs_cached.return_value = [dict(PAIR, **position)]
    response = await client.post(PATH, json=body(**override))
    assert response.status_code == 200, response.text
    assert service.query_range_backtest.await_args.args[0].notional_usd == expected
    assert response.json()["notional_usd"] == expected


async def test_missing_card_and_unrecognized_legs(phone):
    app, client, service = phone
    response = await client.post(PATH, json=body(card_id="missing"))
    assert response.status_code == 404 and response.json() == {"detail": "未找到卡片"}
    for pair in (dict(PAIR, name=""), dict(PAIR, buyEx="private-exchange")):
        app.state.astro_client.list_pairs_cached.return_value = [pair]
        response = await client.post(PATH, json=body())
        assert response.status_code == 422 and "private" not in response.text
    service.query_range_backtest.assert_not_awaited()


async def test_invalid_ranges_and_inputs_are_private(phone):
    app, client, service = phone
    future = datetime.now(UTC) + timedelta(days=1)
    for payload in (
        body(end=START), body(end=START - timedelta(minutes=1)), body(end=START + timedelta(seconds=59)),
        body(end=START + timedelta(hours=72, microseconds=1)), body(start=future - timedelta(minutes=1), end=future),
        body(start_at="private-time"), body(end_at="2025-01-01T00:01:00"),
        body(card_id="private/id"), body(notional_usd=0), body(notional_usd=-1),
        body(notional_usd=10_000_001), body(notional_usd=True),
    ):
        response = await client.post(PATH, json=payload)
        assert response.status_code == 422 and response.json() == {"detail": "区间无效"}
    app.state.astro_client.list_pairs_cached.assert_not_awaited()
    service.query_range_backtest.assert_not_awaited()


async def test_timeout_query_error_and_release(phone, monkeypatch):
    _, client, service = phone
    assert routes_mobile.RANGE_TIMEOUT_SECONDS == 60
    monkeypatch.setattr(routes_mobile, "RANGE_TIMEOUT_SECONDS", 0.01)
    cancelled = asyncio.Event()

    async def slow(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    service.query_range_backtest.side_effect = slow
    response = await client.post(PATH, json=body())
    assert response.status_code == 504 and response.json() == {"detail": "区间估算超时"}
    assert cancelled.is_set()
    service.query_range_backtest.side_effect = PairSpreadQueryError("行情不可用")
    assert (await client.post(PATH, json=body())).status_code == 502
    service.query_range_backtest.side_effect = None
    assert (await client.post(PATH, json=body())).status_code == 200
    assert service.aclose.await_count == 3


async def test_one_in_flight_even_while_fetching_card(phone):
    app, client, service = phone
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch():
        entered.set()
        await release.wait()
        return [PAIR]

    app.state.astro_client.list_pairs_cached.side_effect = fetch
    first = asyncio.create_task(client.post(PATH, json=body()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        second = await asyncio.wait_for(client.post(PATH, json=body()), timeout=3)
        assert second.status_code == 429 and second.json() == {"detail": "上一次估算还在进行"}
    finally:
        release.set()
        response = await first
    assert response.status_code == 200
    service.query_range_backtest.assert_awaited_once()


async def test_six_per_rolling_minute_per_session(phone, monkeypatch):
    app, client, service = phone
    now = 100.0
    monkeypatch.setattr(routes_mobile, "monotonic", lambda: now)
    assert (await client.post(PATH, json=body())).status_code == 200
    now += 59
    for _ in range(5):
        assert (await client.post(PATH, json=body())).status_code == 200
    assert (await client.post(PATH, json=body())).status_code == 429
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as other:
        await login(other, "mobile_readonly", int(time.time() // 30))
        assert (await other.post(PATH, json=body())).status_code == 200
    now += 1
    assert (await client.post(PATH, json=body())).status_code == 200
    assert (await client.post(PATH, json=body())).status_code == 429
    assert service.query_range_backtest.await_count == 8
