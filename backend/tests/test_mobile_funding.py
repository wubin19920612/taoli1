import asyncio
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_funding_arbitrage import market
from test_mobile_api import mobile_app  # noqa: F401 - shared production-auth fixture
from test_mobile_pins import login

from app.api import (
    mobile_query_utils,
    routes_funding_arbitrage,
    routes_mobile_funding,
    routes_mobile_instruments,
    routes_mobile_opportunities,
    routes_mobile_pair,
    routes_mobile_premium,
)
from app.db.auth import session_hash
from app.models.funding_arbitrage import FundingArbitrageSettings
from app.models.market import MarketType
from app.models.settings import RiskSettings
from app.services.funding_arbitrage import build_funding_arbitrage_preview

MOBILE_BASE = "/api/mobile/funding-arbitrage"
WEB_BASE = "/api/funding-arbitrage"
PREVIEW = f"{MOBILE_BASE}/preview"
AT = datetime(2026, 11, 26, tzinfo=UTC)
COUNTS = (
    "total_pairs_evaluated", "displayed_candidates", "blocked_missing_funding",
    "blocked_liquidity", "blocked_adl_risk", "blocked_expected_pnl",
    "enter_count", "hold_count", "exit_count", "blocked_count",
)
SETTINGS_FIELDS = (
    "enabled", "max_candidates", "min_entry_edge_pct", "min_funding_edge_pct",
    "min_volume_24h_usdt", "min_minutes_to_settlement", "max_minutes_to_settlement",
    "notional_per_symbol_usdt", "leverage",
)
CANDIDATE_FIELDS = (
    "id", "symbol", "type", "long_exchange", "long_market_type", "long_raw_symbol",
    "short_exchange", "short_market_type", "short_raw_symbol", "funding_source",
    "long_current_funding_pct", "short_current_funding_pct", "long_next_funding_pct",
    "short_next_funding_pct", "current_funding_edge_pct", "next_funding_edge_pct",
    "long_funding_interval_hours", "short_funding_interval_hours",
    "funding_comparison_interval_hours", "next_settlement_time", "minutes_to_settlement",
    "entry_basis_pct", "exit_basis_pct", "basis_width_pct", "expected_cycle_pnl_pct",
    "estimated_open_cost_pct", "estimated_close_cost_pct", "adl_risk_score", "adl_risk_level",
    "decision", "decision_reasons", "risk_labels", "primary_opportunity_type",
    "opportunity_types", "opportunity_reasons", "volume_24h_usdt", "depth_usdt",
    "uses_hyperliquid",
)


@pytest.fixture
async def phone(mobile_app, monkeypatch):  # noqa: F811 - imported fixture is wired by name
    app, client = mobile_app
    counter = int(time.time() // 30)
    await login(client, "mobile_readonly", counter - 1)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(mobile_query_utils, "monotonic", lambda: clock.now)
    monkeypatch.setattr(routes_mobile_funding, "utc_now", lambda: AT)
    monkeypatch.setattr(
        routes_funding_arbitrage, "build_funding_arbitrage_preview",
        partial(build_funding_arbitrage_preview, now=AT),
    )
    return app, client, clock, counter


@pytest.fixture
async def web_client(phone):
    app, _, _, counter = phone
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test",
    ) as client:
        await login(client, "full", counter)
        yield client


def markets_for(base, *, rate=0.3):
    symbol = base + "USDT"
    return [
        market("binance", MarketType.SPOT, bid=99.99, ask=100).model_copy(
            update={"symbol": symbol, "base": base, "raw_symbol": symbol},
        ),
        market(
            "okx", MarketType.FUTURE, bid=100.2, ask=100.21,
            funding_next_rate_pct=rate, next_time=AT + timedelta(minutes=30),
        ).model_copy(update={"symbol": symbol, "base": base, "raw_symbol": f"{base}-USDT-SWAP"}),
    ]


def fields(data, names):
    return {name: data[name] for name in names}


async def test_mobile_funding_matches_web_preview_and_filters(phone, web_client):
    app, client, clock, _ = phone
    await app.state.settings_repo.set_funding_arbitrage_settings(FundingArbitrageSettings(
        enabled=True, max_candidates=5, min_entry_edge_pct=0.04, min_funding_edge_pct=0.01,
        min_volume_24h_usdt=500_000, min_minutes_to_settlement=3, max_minutes_to_settlement=60,
        notional_per_symbol_usdt=120, leverage=2,
    ))
    await app.state.settings_repo.set_risk_settings(RiskSettings(ignored_exchanges=["gate"]))
    hood = markets_for("HOOD")
    hyperliquid = hood[1].model_copy(update={
        "exchange": "hyperliquid", "dex": "xyz", "raw_symbol": "xyz:HOOD",
        "funding_interval_hours": 1, "funding_next_rate_pct": 0.2,
    })
    app.state.snapshot_store.set_markets([
        *hood, hyperliquid, hood[1].model_copy(update={"exchange": "gate"}),
        *markets_for("OTHER", rate=0.2),
        *markets_for("MISSING", rate=None),
        *markets_for("LOW", rate=-0.1),
    ])
    web_response = await web_client.get(f"{WEB_BASE}/preview")
    assert web_response.status_code == 200
    baseline = web_response.json()
    rows = baseline["candidates"]
    assert baseline["total_pairs_evaluated"] == 6
    assert baseline["displayed_candidates"] == 5
    assert baseline["blocked_missing_funding"] == 1
    assert any(row["uses_hyperliquid"] and row["short_raw_symbol"] == "xyz:HOOD" for row in rows)

    sf_enter = [
        row for row in rows
        if row["decision"] == "ENTER" and row["type"] == "SF" and row["symbol"] == "HOODUSDT"
    ]
    assert len(sf_enter) == 2
    for params, expected in (
        ({}, rows),
        ({"decision": "BLOCKED"}, [row for row in rows if row["decision"] == "BLOCKED"]),
        ({"type": "FF"}, [row for row in rows if row["type"] == "FF"]),
        ({"symbol": "OoD"}, [row for row in rows if row["symbol"] == "HOODUSDT"]),
        ({"decision": "ENTER", "type": "SF", "symbol": "oOd", "limit": 1}, sf_enter),
        ({"symbol": "unknown"}, []),
    ):
        response = await client.get(PREVIEW, params=params)
        assert response.status_code == 200
        assert response.json() == {
            "observed_at": "2026-11-26T00:00:00Z",
            "settings": fields(baseline["settings"], SETTINGS_FIELDS),
            **fields(baseline, COUNTS),
            "filtered_total": len(expected),
            "candidates": [
                fields(row, CANDIDATE_FIELDS) for row in expected[:params.get("limit", 100)]
            ],
        }
        clock.now += 3


async def test_mobile_funding_default_and_maximum_limit(phone, web_client):
    app, client, clock, _ = phone
    await app.state.settings_repo.set_funding_arbitrage_settings(
        FundingArbitrageSettings(max_candidates=250),
    )
    app.state.snapshot_store.set_markets([
        leg for i in range(201) for leg in markets_for(f"ASSET{i}", rate=0.2 + i / 1000)
    ])
    baseline = (await web_client.get(f"{WEB_BASE}/preview")).json()
    expected_ids = [row["id"] for row in baseline["candidates"]]
    assert len(expected_ids) == 201
    for params, size in (({}, 100), ({"limit": 200}, 200)):
        response = await client.get(PREVIEW, params=params)
        assert response.status_code == 200
        result = response.json()
        assert result["displayed_candidates"] == result["filtered_total"] == 201
        assert [row["id"] for row in result["candidates"]] == expected_ids[:size]
        clock.now += 3


async def test_mobile_funding_permissions_and_session_quota(phone, web_client):
    app, client, _, _ = phone
    for method, path in (
        ("GET", f"{WEB_BASE}/preview"),
        ("GET", f"{WEB_BASE}/settings"),
        ("PUT", f"{WEB_BASE}/settings"),
        ("POST", PREVIEW),
        ("PUT", f"{MOBILE_BASE}/settings"),
    ):
        assert (await client.request(method, path)).status_code == 403
    assert {
        path: set(operations)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(f"{MOBILE_BASE}/")
    } == {PREVIEW: {"get"}}
    assert (await client.get(PREVIEW)).status_code == 200
    assert (await web_client.get(PREVIEW)).status_code == 200
    assert (await client.get(PREVIEW)).status_code == 429
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test",
    ) as guest:
        assert (await guest.get(PREVIEW)).status_code == 401
        assert (await guest.get(PREVIEW, headers={"X-Api-Token": "a" * 64})).status_code == 401


async def test_mobile_funding_inflight_and_cooldown_with_independent_module_quotas(phone, monkeypatch):
    app, client, clock, _ = phone
    request = SimpleNamespace(app=app, state=SimpleNamespace(
        auth_session_hash=session_hash(client.cookies["taoli1_session"]),
    ))
    owner = request.state.auth_session_hash
    entered, release = asyncio.Event(), asyncio.Event()
    settings = await app.state.settings_repo.get_funding_arbitrage_settings()

    async def delayed_settings():
        entered.set()
        await release.wait()
        return settings

    monkeypatch.setattr(app.state.settings_repo, "get_funding_arbitrage_settings", delayed_settings)
    with (
        routes_mobile_pair._limits(request).query(owner),
        routes_mobile_premium._limits(request).query(owner),
        routes_mobile_opportunities._limits(request).query(owner),
        routes_mobile_instruments._instrument_query(request),
        routes_mobile_instruments._instrument_query(request, upstream=True),
    ):
        first = asyncio.create_task(client.get(PREVIEW))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            clock.now += 3
            overlap = await client.get(PREVIEW)
            assert overlap.status_code == 429
            assert overlap.json() == {"detail": "上一个查询还在进行"}
        finally:
            release.set()
            completed = await first

    assert completed.status_code == 200
    assert (await client.get(PREVIEW)).status_code == 200
    clock.now += 2.99
    too_soon = await client.get(PREVIEW)
    assert too_soon.status_code == 429
    assert too_soon.json() == {"detail": "请求过于频繁，请至少间隔 3 秒"}
    clock.now = 106.0
    assert (await client.get(PREVIEW)).status_code == 200


@pytest.mark.parametrize("params", [
    {"decision": "WAIT"}, {"type": "FS"}, {"limit": 0}, {"limit": 201}, {"limit": "bad"},
])
async def test_mobile_funding_invalid_parameters_do_not_use_quota(phone, params):
    _, client, _, _ = phone
    assert (await client.get(PREVIEW, params=params)).status_code == 422
    assert (await client.get(PREVIEW)).status_code == 200


async def test_mobile_funding_masks_unavailable_settings_and_failures(phone, monkeypatch, caplog):
    app, client, clock, _ = phone
    secret = "private database details"
    with monkeypatch.context() as patch:
        patch.setattr(app.state, "settings_repo", None)
        unavailable = await client.get(PREVIEW)
        assert unavailable.status_code == 503
        assert unavailable.json() == {"detail": "资金费率数据暂不可用"}

    clock.now += 3
    settings = await app.state.settings_repo.get_funding_arbitrage_settings()
    getter = AsyncMock(side_effect=[
        RuntimeError(secret),
        settings.model_copy(update={"min_entry_edge_pct": float("nan")}),
        settings,
    ])
    monkeypatch.setattr(app.state.settings_repo, "get_funding_arbitrage_settings", getter)
    for _ in range(2):
        failed = await client.get(PREVIEW)
        assert failed.status_code == 503
        assert failed.json() == {"detail": "资金费率数据暂不可用"}
        clock.now += 3

    assert secret not in caplog.text
    assert (await client.get(PREVIEW)).status_code == 200
