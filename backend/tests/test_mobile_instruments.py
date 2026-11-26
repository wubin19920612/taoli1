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
    routes_instruments,
    routes_mobile_opportunities,
    routes_mobile_pair,
    routes_mobile_premium,
)
from app.db.auth import session_hash
from app.models.instrument import InstrumentMarketCapResult, InstrumentStatisticsResult
from app.models.market import MarketSnapshot, MarketType
from app.models.settings import RiskSettings

MOBILE_LOOKUP = "/api/mobile/instruments/BTC"
MOBILE_STATS = f"{MOBILE_LOOKUP}/statistics"
MOBILE_CAP = "/api/mobile/instrument-market-cap/BTC"
WEB_LOOKUP = "/api/instruments/BTC"
WEB_STATS = f"{WEB_LOOKUP}/statistics"
WEB_CAP = "/api/instrument-market-cap/BTC"

LOOKUP_FIELDS = (
    "query", "symbol", "base", "observed_at", "exchange_count", "market_count", "identity_ambiguous",
)
MARKET_FIELDS = (
    "market_id", "exchange", "market_type", "raw_symbol", "dex", "bid", "ask", "volume_24h_usdt",
    "funding_rate_pct", "funding_next_rate_pct", "funding_interval_hours", "funding_next_time",
    "mark_price", "index_price", "data_status", "age_seconds", "error", "is_estimated", "asset_name",
)
SPREAD_FIELDS = (
    "id", "opportunity_type", "buy_exchange", "buy_market_type", "buy_raw_symbol", "buy_dex",
    "sell_exchange", "sell_market_type", "sell_raw_symbol", "sell_dex", "buy_ask", "sell_bid",
    "executable_spread_pct", "close_spread_pct", "mid_spread_pct", "buy_volume_24h_usdt",
    "sell_volume_24h_usdt", "buy_funding_rate_pct", "sell_funding_rate_pct", "buy_funding_interval_hours",
    "sell_funding_interval_hours", "astro_supported", "astro_blocker", "reference_only", "validation_reasons",
)


@pytest.fixture
async def phone(mobile_app, monkeypatch):  # noqa: F811 - imported fixture is wired by name.
    app, client = mobile_app
    counter = int(time.time() // 30)
    await login(client, "mobile_readonly", counter - 1)
    clock = SimpleNamespace(monotonic=100.0, now=datetime.now(UTC))
    monkeypatch.setattr(mobile_query_utils, "monotonic", lambda: clock.monotonic)
    monkeypatch.setattr(routes_instruments, "datetime", SimpleNamespace(now=lambda _: clock.now))
    return app, client, clock, counter


@pytest.fixture
async def web_client(phone):
    app, _, _, counter = phone
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://test") as web:
        await login(web, "full", counter)
        yield web


def market(now, exchange, raw_symbol, *, dex=None, asset_id="btc", price=100.0):
    return MarketSnapshot(
        symbol="BTCUSDT", base="BTC", exchange=exchange, market_type=MarketType.FUTURE,
        raw_symbol=raw_symbol, dex=dex, bid=price, ask=price + 1, timestamp=now,
        volume_24h_usdt=1_000_000, funding_rate_pct=0.01, funding_next_rate_pct=0.02,
        funding_interval_hours=8, funding_next_time=now + timedelta(hours=1),
        mark_price=price + 0.5, index_price=price + 0.4,
        asset_id=asset_id, asset_name=f"Asset {asset_id}", is_estimated=exchange == "bybit",
    )


def seed_markets(app, rows, monkeypatch):
    # Supply identity annotations directly to exercise lookup with known asset groups.
    monkeypatch.setattr(app.state.market_validation, "annotate", lambda row: row)
    app.state.snapshot_store.set_all_markets(rows)


def project(row, fields):
    return {field: row[field] for field in fields}


async def test_mobile_lookup_matches_web_projection_and_order(phone, web_client, monkeypatch):
    app, client, clock, _ = phone
    seed_markets(app, [
        market(clock.now, "okx", "BTC-USDT-SWAP", price=104),
        market(clock.now, "hyperliquid", "xyz:BTC", dex="xyz", price=102),
        market(clock.now, "hyperliquid", "BTC", dex="main", price=106),
        market(clock.now, "binance", "BTCUSDT"),
        market(clock.now - timedelta(minutes=2), "bybit", "BTCUSDT"),
    ], monkeypatch)
    app.state.snapshot_store.set_exchange_errors({"bybit:future": "行情暂不可用"})
    params = {"dex": "xyz"}

    mobile = await client.get(MOBILE_LOOKUP, params=params)
    app.state.astro_client.list_pairs_cached.assert_not_awaited()
    web = await web_client.get(WEB_LOOKUP, params=params)

    assert mobile.status_code == web.status_code == 200
    app.state.astro_client.list_pairs_cached.assert_awaited_once()
    expected = web.json()
    actual = mobile.json()
    assert actual == {
        **project(expected, LOOKUP_FIELDS),
        "asset_groups": [
            {**project(group, ("asset_id", "display_name")), "market_count": len(group["market_ids"])}
            for group in expected["asset_groups"]
        ],
        "markets": [project(row, MARKET_FIELDS) for row in expected["markets"]],
        "spreads": [project(row, SPREAD_FIELDS) for row in expected["spreads"]],
        "spread_total": len(expected["spreads"]),
    }
    assert len(actual["spreads"]) == 3
    assert [row["raw_symbol"] for row in actual["markets"]] == [
        "BTCUSDT", "BTCUSDT", "xyz:BTC", "BTC-USDT-SWAP",
    ]
    assert actual["markets"][1]["error"] == "行情暂不可用"
    assert actual["markets"][1]["data_status"] == "stale"
    assert actual["markets"][2]["dex"] == "xyz"


async def test_mobile_lookup_caps_spreads_after_web_sorting(phone, web_client, monkeypatch):
    app, client, clock, _ = phone
    seed_markets(app, [
        market(clock.now, "hyperliquid", f"dex{i}:BTC", dex=f"dex{i}", price=100 + i)
        for i in range(22)
    ], monkeypatch)
    mobile = await client.get(MOBILE_LOOKUP)
    web = await web_client.get(WEB_LOOKUP)

    assert mobile.status_code == web.status_code == 200
    spreads = web.json()["spreads"]
    assert mobile.json()["spread_total"] == len(spreads) == 231
    assert [row["id"] for row in mobile.json()["spreads"]] == [row["id"] for row in spreads[:200]]


async def test_mobile_lookup_filters_asset_and_statistics_preserves_web_409(
    phone, web_client, monkeypatch,
):
    app, client, clock, _ = phone
    seed_markets(app, [
        market(clock.now, "binance", "BTCUSDT"),
        market(clock.now, "okx", "BTC-USDT-SWAP", price=104),
        market(clock.now, "hyperliquid", "xyz:BTC", dex="xyz", asset_id="other"),
    ], monkeypatch)
    result = InstrumentStatisticsResult(
        symbol="BTCUSDT", observed_at=clock.now, markets={"market": {"change_1h_pct": 2.0}},
    )
    service = SimpleNamespace(lookup=AsyncMock(return_value=result))
    monkeypatch.setattr(app.state, "instrument_statistics_service", service)
    params = {"asset_id": "btc", "dex": "xyz"}

    mobile = await client.get(MOBILE_LOOKUP, params=params)
    assert mobile.status_code == 200
    data = mobile.json()
    assert data["identity_ambiguous"] is True
    assert data["asset_groups"] == [
        {"asset_id": "btc", "display_name": "Asset btc", "market_count": 2},
        {"asset_id": "other", "display_name": "Asset other", "market_count": 1},
    ]
    assert [row["asset_name"] for row in data["markets"]] == ["Asset btc", "Asset btc"]
    assert data["market_count"] == 2
    assert data["spread_total"] == 1

    conflict = await client.get(MOBILE_STATS)
    web_conflict = await web_client.get(WEB_STATS)
    assert conflict.status_code == web_conflict.status_code == 409
    assert conflict.json() == web_conflict.json() == {
        "detail": "同名标的包含不同资产，请先选择资产再查询历史",
    }
    service.lookup.assert_not_awaited()

    clock.monotonic += 5
    stats = await client.get(MOBILE_STATS, params=params)
    assert stats.status_code == 200
    service.lookup.assert_awaited_once()
    received = service.lookup.await_args.args[0]
    assert [row.asset_id for row in received.markets] == ["btc", "btc"]
    assert stats.json() == (await web_client.get(WEB_STATS, params=params)).json()
    assert stats.json() == result.model_dump(mode="json")


@pytest.mark.parametrize(("mobile_path", "web_path"), [
    ("/api/mobile/instruments/-", "/api/instruments/-"),
    ("/api/mobile/instrument-market-cap/bad-symbol", "/api/instrument-market-cap/bad-symbol"),
])
async def test_mobile_instruments_preserve_web_422(phone, web_client, mobile_path, web_path):
    _, client, _, _ = phone
    mobile = await client.get(mobile_path)
    web = await web_client.get(web_path)
    assert mobile.status_code == web.status_code == 422
    assert mobile.json() == web.json()


async def test_mobile_market_cap_passes_coin_id_and_web_payload(phone, web_client, monkeypatch):
    app, client, clock, _ = phone
    result = InstrumentMarketCapResult(
        base="BTC", status="available", selected_id="bitcoin", market_cap_usd=1_000_000,
        updated_at=clock.now,
        candidates=[{"id": "bitcoin", "name": "Bitcoin", "symbol": "btc", "market_cap_rank": 1}],
    )
    service = SimpleNamespace(lookup=AsyncMock(return_value=result))
    monkeypatch.setattr(app.state, "instrument_market_cap_service", service)
    params = {"coin_id": "bitcoin"}

    mobile = await client.get(MOBILE_CAP, params=params)
    assert mobile.status_code == 200
    service.lookup.assert_awaited_once_with("BTC", "bitcoin")
    web = await web_client.get(WEB_CAP, params=params)
    assert web.status_code == 200
    assert mobile.json() == web.json() == result.model_dump(mode="json")


async def test_mobile_instruments_permissions_and_per_session_quota(phone, monkeypatch):
    app, client, clock, counter = phone
    monkeypatch.setattr(app.state, "instrument_statistics_service", SimpleNamespace(
        lookup=AsyncMock(return_value=InstrumentStatisticsResult(symbol="BTCUSDT", observed_at=clock.now)),
    ))
    monkeypatch.setattr(app.state, "instrument_market_cap_service", SimpleNamespace(
        lookup=AsyncMock(return_value=InstrumentMarketCapResult(base="BTC", status="not_found")),
    ))
    for path in (WEB_LOOKUP, WEB_STATS, WEB_CAP):
        assert (await client.get(path)).status_code == 403
    for path in (MOBILE_LOOKUP, MOBILE_STATS):
        assert (await client.get(path)).status_code == 200
    clock.monotonic += 5
    assert (await client.get(MOBILE_CAP)).status_code == 200
    assert (await client.get(MOBILE_LOOKUP)).status_code == 200

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://test") as other:
        for path in (MOBILE_LOOKUP, MOBILE_STATS, MOBILE_CAP):
            assert (await other.get(path)).status_code == 401
        await login(other, "mobile_readonly", counter)
        assert (await other.get(MOBILE_LOOKUP)).status_code == 200
        assert (await other.get(MOBILE_STATS)).status_code == 200
    assert (await client.get(MOBILE_LOOKUP)).status_code == 429
    assert (await client.get(MOBILE_STATS)).status_code == 429


async def test_mobile_instrument_lookup_inflight_and_interval(phone, monkeypatch):
    app, client, clock, _ = phone
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_risk_settings():
        entered.set()
        await release.wait()
        return RiskSettings()

    monkeypatch.setattr(app.state.settings_repo, "get_risk_settings", delayed_risk_settings)
    first = asyncio.create_task(client.get(MOBILE_LOOKUP))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        clock.monotonic += 2
        overlapping = await client.get(MOBILE_LOOKUP)
        assert overlapping.status_code == 429
        assert overlapping.json() == {"detail": "上一个查询还在进行"}
    finally:
        release.set()
        completed = await first

    assert completed.status_code == 200
    assert (await client.get(MOBILE_LOOKUP)).status_code == 200
    clock.monotonic += 1
    too_soon = await client.get(MOBILE_LOOKUP)
    assert too_soon.status_code == 429
    assert too_soon.json() == {"detail": "请求过于频繁，请至少间隔 2 秒"}
    clock.monotonic += 1
    assert (await client.get(MOBILE_LOOKUP)).status_code == 200


async def test_mobile_instrument_upstream_inflight_and_interval_are_shared(phone, monkeypatch):
    app, client, clock, _ = phone
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_statistics(instrument):
        entered.set()
        await release.wait()
        return InstrumentStatisticsResult(symbol=instrument.symbol, observed_at=clock.now)

    monkeypatch.setattr(app.state, "instrument_statistics_service", SimpleNamespace(lookup=delayed_statistics))
    monkeypatch.setattr(app.state, "instrument_market_cap_service", SimpleNamespace(
        lookup=AsyncMock(return_value=InstrumentMarketCapResult(base="BTC", status="not_found")),
    ))
    owner = session_hash(client.cookies["taoli1_session"])
    request = SimpleNamespace(app=app)
    with (
        routes_mobile_pair._limits(request).query(owner),
        routes_mobile_premium._limits(request).query(owner),
        routes_mobile_opportunities._limits(request).query(owner),
    ):
        first = asyncio.create_task(client.get(MOBILE_STATS))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            clock.monotonic += 5
            for path in (MOBILE_STATS, MOBILE_CAP):
                overlapping = await client.get(path)
                assert overlapping.status_code == 429
                assert overlapping.json() == {"detail": "上一个查询还在进行"}
            assert (await client.get(MOBILE_LOOKUP)).status_code == 200
        finally:
            release.set()
            completed = await first

        assert completed.status_code == 200
        assert (await client.get(MOBILE_CAP)).status_code == 200
        clock.monotonic += 4
        for path in (MOBILE_CAP, MOBILE_STATS):
            too_soon = await client.get(path)
            assert too_soon.status_code == 429
            assert too_soon.json() == {"detail": "请求过于频繁，请至少间隔 5 秒"}
        assert (await client.get(MOBILE_LOOKUP)).status_code == 200
        clock.monotonic += 1
        assert (await client.get(MOBILE_STATS)).status_code == 200


@pytest.mark.parametrize(("path", "service_name", "method", "interval"), [
    (MOBILE_LOOKUP, "settings_repo", "get_risk_settings", 2),
    (MOBILE_STATS, "instrument_statistics_service", "lookup", 5),
    (MOBILE_CAP, "instrument_market_cap_service", "lookup", 5),
])
async def test_mobile_instruments_mask_internal_errors_and_release_quota(
    phone, monkeypatch, caplog, path, service_name, method, interval,
):
    app, client, clock, _ = phone
    secret = "private upstream failure details"
    successes = {
        MOBILE_LOOKUP: RiskSettings(),
        MOBILE_STATS: InstrumentStatisticsResult(symbol="BTCUSDT", observed_at=clock.now),
        MOBILE_CAP: InstrumentMarketCapResult(base="BTC", status="not_found"),
    }
    service = getattr(app.state, service_name)
    monkeypatch.setattr(service, method, AsyncMock(side_effect=[RuntimeError(secret), successes[path]]))

    failed = await client.get(path)
    assert failed.status_code == 503
    assert failed.json() == {"detail": "标的数据暂不可用"}
    assert secret not in caplog.text
    clock.monotonic += interval
    assert (await client.get(path)).status_code == 200
