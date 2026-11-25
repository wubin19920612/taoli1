import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_mobile_api import mobile_app  # noqa: F401 - production session guard
from test_mobile_pins import login
from test_pair_spread_presets import _preset

from app.api import routes_mobile_pair
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadFundingPoint,
    PairSpreadPoint,
    PairSpreadQueryResult,
)
from app.services.pair_spread_query import PairSpreadQueryError, PairSpreadQueryService, _stats

MOBILE = "/api/mobile/pair-spread"
WEB = "/api/pair-spread"
AT = datetime(2026, 1, 1, tzinfo=UTC)
PARAMS = {
    "leg1_exchange": "okx", "leg1_symbol": "ANTHROPIC",
    "leg2_exchange": "hyperliquid", "leg2_symbol": "io:ANTH", "leg2_dex": "io",
    "leg2_multiplier": 2,
}
CURRENT1 = PairSpreadCurrentLeg(
    exchange="okx", symbol="ANTHROPICUSDT", raw_symbol="ANTHROPIC-USDT-SWAP", price=10,
    price_field="mark_price", timestamp=AT, funding_rate_pct=0,
)
CURRENT2 = PairSpreadCurrentLeg(
    exchange="hyperliquid", symbol="ANTHUSDT", dex="io", raw_symbol="io:ANTH", price=240,
    price_field="mark_price", timestamp=AT, funding_rate_pct=-0.02,
)


@pytest.fixture
async def phone(mobile_app, monkeypatch):
    app, client = mobile_app
    await login(client, "mobile_readonly", int(time.time() // 30) - 1)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(routes_mobile_pair, "monotonic", lambda: clock.now)
    monkeypatch.setattr("app.services.pair_spread_query.utc_now", lambda: AT)

    async def query(leg1, leg2, *, hours, interval_minutes, interval_seconds, leg2_multiplier,
                    now, include_current):
        points = []
        for i in range(1603):
            # Skew time spacing so bucket-by-index sampling differs from bucket-by-time.
            minute = i if i < 1200 else i + 2000
            close = {401: 10, 980: 600}.get(i, 60 + i % 11)
            points.append(PairSpreadPoint(
                bucket_at=AT + timedelta(minutes=minute), leg1_close=10, leg2_close=close,
                spread_abs=close - 10, spread_pct=(close - 10) / ((close + 10) / 2) * 100,
            ))
        current = service._build_current_snapshot(
            CURRENT1, CURRENT2.model_copy(update={"price": 120}), AT,
        ) if include_current else None
        return PairSpreadQueryResult(
            leg1=leg1, leg2=leg2, hours=hours, interval_minutes=interval_minutes,
            interval_seconds=interval_seconds, leg2_multiplier=leg2_multiplier, observed_at=now or AT,
            complete=False, warnings=["覆盖不足"], point_count=len(points), points=points,
            spread_abs=_stats([point.spread_abs for point in points]),
            spread_pct=_stats([point.spread_pct for point in points]),
            current=current, funding_history=[PairSpreadFundingPoint(
                exchange="hyperliquid", symbol="ANTHUSDT", dex="io", funding_time=AT,
                funding_rate_pct=-0.012,
            )] if include_current else [],
        )

    service = SimpleNamespace(
        query=AsyncMock(side_effect=query), aclose=AsyncMock(),
        _fetch_current_with_warning=AsyncMock(side_effect=lambda leg, warnings: (
            CURRENT1 if leg.exchange == "okx" else CURRENT2
        )),
    )
    service._build_current_snapshot = PairSpreadQueryService._build_current_snapshot.__get__(service)
    app.state.pair_spread_query_service_factory = lambda: service
    return app, client, service, clock


async def test_mobile_pair_spread_readonly_access_and_current_only_fetches_prices(phone):
    app, client, service, clock = phone
    preset = await app.state.pair_spread_preset_repo.upsert(_preset("anthropic", AT))

    for endpoint in ("exchanges", "presets", "query", "current"):
        assert (await client.get(f"{MOBILE}/{endpoint}", params=PARAMS)).status_code == 200
    for endpoint in ("exchanges", "presets", "query"):
        assert (await client.get(f"{WEB}/{endpoint}", params=PARAMS)).status_code == 403
    assert (await client.put(f"{MOBILE}/presets/anthropic", json={})).status_code == 403
    assert (await client.get(f"{MOBILE}/presets")).json() == [
        preset.model_dump(mode="json", by_alias=True),
    ]
    assert service.query.await_count == 1

    clock.now += 3
    response = await client.get(f"{MOBILE}/current", params=PARAMS)
    current = response.json()
    assert response.status_code == 200, response.text
    assert set(current) == {"observed_at", "leg1", "leg2", "spread_abs", "spread_pct"}
    assert (current["leg1"]["price"], current["leg2"]["price"]) == (100, 120)
    assert (current["leg2"]["dex"], current["leg2"]["raw_symbol"]) == ("io", "io:ANTH")
    assert current["spread_abs"] == 20 and current["spread_pct"] == pytest.approx(20 / 110 * 100)
    assert service.query.await_count == 1
    assert service.aclose.await_count == 3

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as other:
        await login(other, "full", int(time.time() // 30))
        for endpoint in ("exchanges", "presets"):
            assert (await other.get(f"{WEB}/{endpoint}")).json() == (
                await client.get(f"{MOBILE}/{endpoint}")
            ).json()
    client.cookies.clear()
    client.headers["X-Api-Token"] = "a" * 64
    for endpoint in ("exchanges", "presets", "query", "current"):
        assert (await client.get(f"{MOBILE}/{endpoint}", params=PARAMS)).status_code == 401


async def test_mobile_pair_spread_downsampling_and_full_stats_match_website(phone):
    app, client, service, clock = phone
    params = dict(PARAMS, hours=1234, interval_minutes=15, interval_seconds=300, end_at=AT.isoformat())
    mobile = await client.get(f"{MOBILE}/query", params=params)
    assert mobile.status_code == 200, mobile.text
    legs = service.query.await_args.args
    assert (legs[1].exchange, legs[1].dex, legs[1].symbol) == ("hyperliquid", "io", "ANTHUSDT")
    assert service.query.await_args.kwargs == {
        "hours": 1234, "interval_minutes": 15, "interval_seconds": 300,
        "leg2_multiplier": 2, "now": AT, "include_current": True,
    }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as other:
        await login(other, "full", int(time.time() // 30))
        response = await other.get(f"{WEB}/query", params=params)
    assert response.status_code == 200, response.text
    data, web = mobile.json(), response.json()
    points = data["points"]
    full = web["points"]
    assert data["raw_point_count"] == len(full) == 1603
    assert 0 < len(points) <= 800
    assert all(set(point) == {"t", "a", "b", "pct"} for point in points)
    times = [point["t"] for point in points]
    assert times == sorted(set(times))
    assert times[0] == full[0]["bucket_at"] and times[-1] == full[-1]["bucket_at"]
    assert min(p["pct"] for p in points) == web["spread_pct"]["min"]
    assert max(p["pct"] for p in points) == web["spread_pct"]["max"]

    for field in ("spread_abs", "spread_pct"):
        stats = web[field].copy()
        stats["last"] = stats.pop("current")
        assert data[field] == stats
    assert data["spread_pct"]["mean"] != pytest.approx(sum(p["pct"] for p in points) / len(points))
    for field in ("leg1", "leg2", "leg2_multiplier", "current", "complete", "warnings", "observed_at"):
        assert data[field] == web[field]
    assert data["funding"] == {
        "leg1_rate_pct": 0, "leg2_rate_pct": -0.02, "funding_history": web["funding_history"],
    }

    clock.now += 3
    historical = await client.get(f"{MOBILE}/query", params=dict(PARAMS, include_current="false"))
    assert historical.status_code == 200, historical.text
    assert historical.json()["current"] is None
    assert historical.json()["funding"] == {
        "leg1_rate_pct": None, "leg2_rate_pct": None, "funding_history": [],
    }
    assert service.query.await_args.kwargs["include_current"] is False


async def test_mobile_pair_spread_query_in_flight_and_start_limits(phone):
    app, client, service, clock = phone
    original = service.query.side_effect
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    service.query.side_effect = slow
    path = f"{MOBILE}/query"
    first = asyncio.create_task(client.get(path, params=PARAMS))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        response = await client.get(path, params=PARAMS)
        assert (response.status_code, response.json()) == (429, {"detail": "上一个查询还在进行"})
        assert (await client.get(f"{MOBILE}/current", params=PARAMS)).status_code == 200
    finally:
        release.set()
        assert (await first).status_code == 200

    clock.now += 2.9
    for endpoint in ("query", "current"):
        response = await client.get(f"{MOBILE}/{endpoint}", params=PARAMS)
        assert (response.status_code, response.json()) == (
            429, {"detail": "请求过于频繁，请至少间隔 3 秒"},
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as other:
        await login(other, "mobile_readonly", int(time.time() // 30))
        assert (await other.get(path, params=PARAMS)).status_code == 200
    clock.now = 103.0
    for endpoint in ("query", "current"):
        assert (await client.get(f"{MOBILE}/{endpoint}", params=PARAMS)).status_code == 200


async def test_mobile_pair_spread_timeout_upstream_error_and_release(phone, monkeypatch):
    _, client, service, clock = phone
    deadline = asyncio.timeout(None)
    original = service.query.side_effect
    cancelled = asyncio.Event()

    def controlled_timeout(seconds):
        assert seconds == 60
        return deadline

    async def slow(*args, **kwargs):
        deadline.reschedule(asyncio.get_running_loop().time())
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    service.query.side_effect = slow
    path = f"{MOBILE}/query"
    with monkeypatch.context() as patch:
        patch.setattr(routes_mobile_pair, "timeout", controlled_timeout)
        response = await client.get(path, params=PARAMS)
    assert response.status_code == 504 and "查询超时" in response.json()["detail"]
    assert cancelled.is_set()
    service.aclose.assert_awaited_once()

    clock.now += 3
    service.query.side_effect = PairSpreadQueryError("没有拿到可对齐的历史K线")
    response = await client.get(path, params=PARAMS)
    assert (response.status_code, response.json()) == (502, {"detail": "没有拿到可对齐的历史K线"})

    clock.now += 3
    service.query.side_effect = original
    assert (await client.get(path, params=PARAMS)).status_code == 200
    service._fetch_current_with_warning.side_effect = lambda leg, warnings: None
    response = await client.get(f"{MOBILE}/current", params=PARAMS)
    assert response.status_code == 502 and "没有拿到" in response.json()["detail"]
    assert service.aclose.await_count == 4


@pytest.mark.parametrize("updates", [
    {"hours": 0}, {"interval_minutes": 2}, {"interval_seconds": 3}, {"interval_seconds": 86401},
    {"leg2_dex": "xyz"}, {"leg2_multiplier": 0}, {"leg1_market_type": "wrong"},
    {"include_current": "wrong"},
])
async def test_mobile_pair_spread_query_422_matches_website(phone, updates):
    app, client, service, _ = phone
    params = dict(PARAMS, **updates)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as other:
        await login(other, "full", int(time.time() // 30))
        web = await other.get(f"{WEB}/query", params=params)
    mobile = await client.get(f"{MOBILE}/query", params=params)
    assert mobile.status_code == web.status_code == 422
    assert mobile.json() == web.json()
    service.query.assert_not_awaited()


def test_mobile_pair_spread_expired_rate_limits_are_pruned(monkeypatch):
    now = 100.0
    monkeypatch.setattr(routes_mobile_pair, "monotonic", lambda: now)
    limits = routes_mobile_pair._PairSpreadLimits()
    with limits.query("still-running"):
        limits.start("expired", "current")
        now += 3
        limits.start("new", "current")
        assert limits._starts == {("new", "current"): now}
        with pytest.raises(routes_mobile_pair.HTTPException, match="上一个查询还在进行"):
            with limits.query("still-running"):
                pytest.fail("an active query was allowed to start twice")
