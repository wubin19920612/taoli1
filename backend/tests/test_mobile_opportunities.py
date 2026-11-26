import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_mobile_api import mobile_app  # noqa: F401 - shared production-auth app fixture
from test_mobile_pins import login

from app.api import (
    mobile_query_utils,
    routes_mobile_opportunities,
    routes_mobile_pair,
    routes_mobile_premium,
    routes_opportunities,
)
from app.db.auth import session_hash
from app.models.opportunity import Opportunity
from app.models.settings import RiskSettings

MOBILE = "/api/mobile/opportunities"
WEB = "/api/opportunities"
AT = datetime(2026, 11, 26, tzinfo=UTC)


def opportunity(id_, **overrides):
    return Opportunity.model_validate({
        "id": id_,
        "type": "FF",
        "symbol": "HOODUSDT",
        "buy_exchange": "hyperliquid",
        "buy_market_type": "future",
        "buy_raw_symbol": "xyz:HOOD",
        "buy_dex": "xyz",
        "buy_local_timestamp": AT - timedelta(milliseconds=2200),
        "sell_exchange": "lighter",
        "sell_market_type": "future",
        "sell_raw_symbol": "HOOD",
        "sell_local_timestamp": AT - timedelta(milliseconds=1000),
        "open_spread_pct": 2.0,
        "close_spread_pct": -2.5,
        "fee_adjusted_open_pct": 1.7,
        "spread_width_pct": 0.5,
        "buy_bid": 100,
        "buy_ask": 101,
        "sell_bid": 103.02,
        "sell_ask": 105,
        "buy_volume_24h_usdt": 750_000,
        "sell_volume_24h_usdt": 900_000,
        "net_funding_hourly_pct": 0.1,
        "risk_labels": ["FUNDING_AGAINST"],
        "last_seen_at": AT,
        "executable": True,
        **overrides,
    })


@pytest.fixture
async def phone(mobile_app, monkeypatch):  # noqa: F811 - imported fixture is wired by name.
    app, client = mobile_app
    counter = int(time.time() // 30)
    await login(client, "mobile_readonly", counter - 1)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(mobile_query_utils, "monotonic", lambda: clock.now)
    monkeypatch.setattr(routes_opportunities, "utc_now", lambda: AT)
    monkeypatch.setattr(routes_mobile_opportunities, "utc_now", lambda: AT)
    return app, client, clock, counter


@pytest.mark.parametrize(
    ("sort_by", "sort_order", "expected"),
    [
        ("open_spread_pct", "desc", ["high", "a", "z"]),
        ("net_funding_hourly_pct", "asc", ["z", "high", "a"]),
    ],
)
async def test_mobile_opportunities_matches_web_filters_and_sort(phone, sort_by, sort_order, expected):
    app, client, clock, counter = phone
    await app.state.settings_repo.set_risk_settings(RiskSettings(ignored_exchanges=["bitget"]))
    app.state.snapshot_store.set_opportunities([
        opportunity("z", net_funding_hourly_pct=-0.05),
        opportunity("risky", risk_labels=["LOW_VOLUME"], open_spread_pct=7),
        opportunity("a", net_funding_hourly_pct=None),
        opportunity("high", open_spread_pct=3),
        opportunity("other-asset", symbol="BTCUSDT", open_spread_pct=6),
        opportunity("wrong-type", type="SF", buy_market_type="spot"),
        opportunity("wrong-exchange", buy_exchange="binance", buy_dex=None),
        opportunity("small-spread", open_spread_pct=1),
        opportunity("small-volume", sell_volume_24h_usdt=49_999),
        opportunity("ignored", sell_exchange="bitget"),
        opportunity("stale", buy_local_timestamp=AT - timedelta(seconds=31)),
    ])
    params = {
        "type": "FF", "symbol": " rh ", "exchange": " HYPERLIQUID ",
        "min_open_spread_pct": 1.5, "min_volume_24h_k": 50,
        "sort_by": sort_by, "sort_order": sort_order,
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as web:
        await login(web, "full", counter)
        for include_risky in (False, True):
            query = dict(params, include_risky="true") if include_risky else params
            actual = await client.get(MOBILE, params=query)
            baseline = await web.get(WEB, params=query)
            assert actual.status_code == baseline.status_code == 200
            ids = [row["id"] for row in actual.json()["items"]]
            assert ids == [row["id"] for row in baseline.json()]
            assert [id_ for id_ in ids if id_ != "risky"] == expected
            assert ("risky" in ids) is include_risky
            assert actual.json()["total"] == len(ids)
            clock.now += 3


async def test_mobile_opportunities_limits_after_sorting_and_counting(phone):
    app, client, clock, _ = phone
    app.state.snapshot_store.set_opportunities([
        opportunity(str(i), open_spread_pct=i / 100) for i in range(102)
    ])

    for params, count in (({}, 50), ({"limit": 1}, 1), ({"limit": 100}, 100)):
        response = await client.get(MOBILE, params=params)
        assert response.status_code == 200
        result = response.json()
        assert result["total"] == 102
        assert [row["id"] for row in result["items"]] == [str(i) for i in range(101, 101 - count, -1)]
        clock.now += 3


async def test_mobile_opportunities_compact_fields_and_leg_values(phone):
    app, client, _, _ = phone
    app.state.snapshot_store.set_opportunities([
        opportunity("both", fake_flag=True),
        opportunity(
            "unknown", sell_volume_24h_usdt=None, buy_local_timestamp=None,
            sell_local_timestamp=None, last_seen_at=AT - timedelta(milliseconds=4000),
        ),
    ])
    response = await client.get(MOBILE)
    assert response.status_code == 200
    expected = {
        "id": "both", "type": "FF", "symbol": "HOODUSDT",
        "buy_exchange": "hyperliquid", "buy_market_type": "future",
        "buy_raw_symbol": "xyz:HOOD", "buy_dex": "xyz",
        "sell_exchange": "lighter", "sell_market_type": "future",
        "sell_raw_symbol": "HOOD", "sell_dex": None,
        "open_spread_pct": 2.0, "close_spread_pct": -2.5, "fee_adjusted_open_pct": 1.7,
        "min_volume_24h_usdt": 750_000, "net_funding_hourly_pct": 0.1,
        "net_funding_next_hourly_pct": None, "risk_labels": ["FUNDING_AGAINST"],
        "max_age_ms": 2200.0, "executable": True, "fake_flag": True,
    }
    assert response.json() == {
        "observed_at": "2026-11-26T00:00:00Z", "total": 2,
        "items": [expected, dict(expected, id="unknown", min_volume_24h_usdt=None,
                                 max_age_ms=4000.0, fake_flag=False)],
    }


async def test_mobile_opportunities_permission_and_session_quotas(phone):
    app, client, _, counter = phone
    assert (await client.get(WEB)).status_code == 403
    assert (await client.post(MOBILE)).status_code == 403
    assert (await client.get(MOBILE)).json() == {
        "observed_at": "2026-11-26T00:00:00Z", "total": 0, "items": [],
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as other:
        assert (await other.get(MOBILE)).status_code == 401
        assert (await other.get(MOBILE, headers={"X-Api-Token": "a" * 64})).status_code == 401
        await login(other, "full", counter)
        assert (await other.get(MOBILE)).status_code == 200
    assert (await client.get(MOBILE)).status_code == 429


async def test_mobile_opportunities_inflight_and_interval_are_separate_from_other_modules(phone, monkeypatch):
    app, client, clock, _ = phone
    owner = session_hash(client.cookies["taoli1_session"])
    request = SimpleNamespace(app=app)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_settings():
        entered.set()
        await release.wait()
        return RiskSettings()

    monkeypatch.setattr(app.state.settings_repo, "get_risk_settings", delayed_settings)
    with routes_mobile_pair._limits(request).query(owner), routes_mobile_premium._limits(request).query(owner):
        first = asyncio.create_task(client.get(MOBILE))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            clock.now += 3
            overlapping = await client.get(MOBILE)
            assert overlapping.status_code == 429
            assert overlapping.json() == {"detail": "上一个查询还在进行"}
        finally:
            release.set()
            completed = await first

    assert completed.status_code == 200
    assert (await client.get(MOBILE)).status_code == 200
    too_soon = await client.get(MOBILE)
    assert too_soon.status_code == 429
    assert too_soon.json() == {"detail": "请求过于频繁，请至少间隔 3 秒"}
    clock.now += 3
    assert (await client.get(MOBILE)).status_code == 200


@pytest.mark.parametrize("params", [
    {"type": "FS"}, {"sort_by": "buy_raw_symbol"}, {"sort_order": "up"},
    {"min_volume_24h_k": -1}, {"min_volume_24h_k": "inf"},
    {"min_open_spread_pct": "nan"}, {"include_risky": "maybe"},
    {"limit": 0}, {"limit": 101},
])
async def test_mobile_opportunities_rejects_invalid_parameters(phone, params):
    _, client, _, _ = phone
    assert (await client.get(MOBILE, params=params)).status_code == 422
    assert (await client.get(MOBILE)).status_code == 200


async def test_mobile_opportunities_hides_failure_details_and_releases_inflight(phone, monkeypatch, caplog):
    app, client, clock, _ = phone
    secret = "private database details"
    getter = AsyncMock(side_effect=[RuntimeError(secret), RiskSettings()])
    monkeypatch.setattr(app.state.settings_repo, "get_risk_settings", getter)

    failed = await client.get(MOBILE)
    assert failed.status_code == 503
    assert failed.json() == {"detail": "机会数据暂不可用"}
    assert secret not in caplog.text
    clock.now += 3
    assert (await client.get(MOBILE)).status_code == 200
