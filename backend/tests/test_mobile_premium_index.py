import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_mobile_api import mobile_app  # noqa: F401 - production session guard
from test_mobile_pins import login

from app.api import mobile_query_utils
from app.models.premium_index import (
    SUPPORTED_PREMIUM_INDEX_EXCHANGES,
    PremiumIndexCurrentSnapshot,
    PremiumIndexPoint,
    PremiumIndexQueryResult,
)
from app.services.pair_spread_query import PairSpreadQueryError
from app.services.premium_index_query import PremiumIndexQueryError, _stats
from app.services.symbol_aliases import SymbolAliasResolver

MOBILE = "/api/mobile/premium-index"
WEB = "/api/premium-index"
AT = datetime(2026, 1, 1, tzinfo=UTC)
PARAMS = {"exchange": "hyperliquid", "symbol": "io:ANTH", "dex": "io"}
CURRENT = {
    "mark_price": 101.0,
    "index_price": 100.0,
    "premium_pct": 1.0,
    "funding_rate_pct": 0.0,
    "funding_next_rate_pct": -0.012,
    "funding_next_time": "2026-01-01T01:00:00Z",
    "funding_interval_hours": 1.0,
}


@pytest.fixture
async def phone(mobile_app, monkeypatch):  # noqa: F811 - pytest wires the imported fixture by name.
    app, client = mobile_app
    totp_window = int(time.time() // 30)
    await login(client, "mobile_readonly", totp_window - 1)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(mobile_query_utils, "monotonic", lambda: clock.now)

    def current(market):
        return PremiumIndexCurrentSnapshot(
            exchange=market.exchange, symbol=market.symbol, dex=market.dex, raw_symbol="io:ANTH",
            observed_at=AT, source="mark_index", **CURRENT,
        )

    async def query(market, *, hours, interval_minutes):
        points = [
            PremiumIndexPoint(
                bucket_at=AT + timedelta(minutes=i if i < 1200 else i + 2000),
                premium_pct={401: -8, 980: 10}.get(i, (i % 11) / 10),
                mark_price=101 + i / 10 if i % 2 == 0 else None,
                index_price=100 if i % 2 == 0 else None,
                source="history",
            )
            for i in range(1603)
        ]
        points.insert(402, points[401].model_copy(update={"premium_pct": 0.72}))
        return PremiumIndexQueryResult(
            exchange=market.exchange, symbol=market.symbol, dex=market.dex, hours=hours,
            interval_minutes=interval_minutes, observed_at=AT, point_count=len(points),
            first_seen_at=points[0].bucket_at, last_seen_at=points[-1].bucket_at,
            premium_pct=_stats([p.premium_pct for p in points]), current=current(market),
            points=points,
        )

    service = SimpleNamespace(
        query=AsyncMock(side_effect=query), current=AsyncMock(side_effect=current), aclose=AsyncMock(),
    )
    app.state.premium_index_query_service_factory = lambda: service
    return app, client, service, clock, totp_window


async def test_mobile_premium_index_readonly_access_and_web_scope(phone):
    app, client, service, _, totp_window = phone
    for endpoint in ("exchanges", "query", "current"):
        assert (await client.get(f"{WEB}/{endpoint}", params=PARAMS)).status_code == 403
        assert (await client.post(f"{MOBILE}/{endpoint}", params=PARAMS)).status_code == 403

    exchanges = await client.get(f"{MOBILE}/exchanges")
    assert exchanges.status_code == 200
    assert exchanges.json() == list(SUPPORTED_PREMIUM_INDEX_EXCHANGES)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as web:
        await login(web, "full", totp_window)
        assert exchanges.json() == (await web.get(f"{WEB}/exchanges")).json()

    response = await client.get(f"{MOBILE}/current", params=PARAMS)
    assert response.status_code == 200, response.text
    assert response.json() == dict(CURRENT, dex="io")
    service.query.assert_not_awaited()
    market = service.current.await_args.args[0]
    assert (market.exchange, market.dex, market.symbol) == ("hyperliquid", "io", "ANTHUSDT")
    assert (await client.get(f"{MOBILE}/query", params=PARAMS)).status_code == 200

    client.cookies.clear()
    client.headers["X-Api-Token"] = "a" * 64
    for endpoint in ("exchanges", "query", "current"):
        assert (await client.get(f"{MOBILE}/{endpoint}", params=PARAMS)).status_code == 401


async def test_mobile_premium_index_sampling_uses_full_service_stats(phone):
    app, client, service, _, totp_window = phone
    params = dict(PARAMS, hours=720, interval_minutes=15)
    response = await client.get(f"{MOBILE}/query", params=params)
    assert response.status_code == 200, response.text
    assert service.query.await_args.kwargs == {"hours": 720, "interval_minutes": 15}
    market = service.query.await_args.args[0]
    assert (market.exchange, market.dex, market.symbol) == ("hyperliquid", "io", "ANTHUSDT")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as full:
        await login(full, "full", totp_window)
        web_response = await full.get(f"{WEB}/query", params=params)

    assert web_response.status_code == 200, web_response.text
    result, web = response.json(), web_response.json()
    assert set(result) == {
        "exchange", "symbol", "dex", "hours", "interval_minutes", "observed_at", "raw_point_count",
        "premium_pct", "current", "points", "warnings",
    }
    for key in ("exchange", "symbol", "dex", "hours", "interval_minutes", "observed_at", "premium_pct"):
        assert result[key] == web[key]
    assert result["symbol"] == "ANTHROPICUSDT"
    assert result["current"] == CURRENT
    assert result["raw_point_count"] == len(web["points"]) == 1604
    points = result["points"]
    assert 0 < len(points) <= 800
    times = [p["t"] for p in points]
    assert times == sorted(set(times))
    assert (times[0], times[-1]) == (web["first_seen_at"], web["last_seen_at"])
    assert min(p["pct"] for p in points) == result["premium_pct"]["min"] == -8
    assert max(p["pct"] for p in points) == result["premium_pct"]["max"] == 10
    assert result["premium_pct"]["mean"] != pytest.approx(sum(p["pct"] for p in points) / len(points))
    assert all(set(p) == {"t", "pct", "mark", "index"} for p in points)
    assert points[0] == {"t": times[0], "pct": 0, "mark": 101, "index": 100}
    assert any(p["mark"] is None and p["index"] is None for p in points)


@pytest.mark.parametrize("endpoint", ["query", "current"])
async def test_mobile_premium_index_uses_alias_dex_when_omitted(phone, endpoint):
    _, client, service, _, _ = phone
    response = await client.get(f"{MOBILE}/{endpoint}", params={"exchange": "hyperliquid", "symbol": "ANTH"})

    assert response.status_code == 200, response.text
    assert response.json()["dex"] == "io"
    market = getattr(service, endpoint).await_args.args[0]
    assert (market.exchange, market.dex, market.symbol) == ("hyperliquid", "io", "ANTHUSDT")


async def test_mobile_premium_index_applies_web_alias_price_multiplier(phone):
    _, client, _, _, _ = phone
    params = {"exchange": "okx", "symbol": "ANTHROPIC"}
    query = await client.get(f"{MOBILE}/query", params=params)
    current = await client.get(f"{MOBILE}/current", params=params)
    assert query.status_code == current.status_code == 200
    expected = dict(CURRENT, mark_price=1010, index_price=1000)
    assert query.json()["dex"] is None
    assert query.json()["current"] == expected
    assert current.json() == dict(expected, dex=None)
    point = query.json()["points"][0]
    assert (point["pct"], point["mark"], point["index"]) == (0, 1010, 1000)


async def test_mobile_premium_index_in_flight_and_start_limits_are_independent_of_p13(phone):
    app, client, service, clock, totp_window = phone
    original = service.query.side_effect
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    service.query.side_effect = slow
    pair = SimpleNamespace(
        query=AsyncMock(side_effect=PairSpreadQueryError("stubbed pair-spread failure")),
        _fetch_funding_with_warning=AsyncMock(),
        _fetch_current_with_warning=AsyncMock(return_value=None),
        aclose=AsyncMock(),
    )
    app.state.pair_spread_query_service_factory = lambda: pair
    pair_params = {
        "leg1_exchange": "okx", "leg1_symbol": "BTC", "leg2_exchange": "bybit", "leg2_symbol": "BTC",
    }
    first = asyncio.create_task(client.get(f"{MOBILE}/query", params=PARAMS))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        response = await client.get(f"{MOBILE}/query", params=PARAMS)
        assert (response.status_code, response.json()) == (429, {"detail": "上一个查询还在进行"})
        assert (await client.get(f"{MOBILE}/current", params=PARAMS)).status_code == 200
        for endpoint in ("query", "current"):
            response = await client.get(f"/api/mobile/pair-spread/{endpoint}", params=pair_params)
            assert response.status_code == 502, response.text
        pair.query.assert_awaited_once()
    finally:
        release.set()
        assert (await first).status_code == 200

    clock.now = 102.9
    for endpoint in ("query", "current"):
        response = await client.get(f"{MOBILE}/{endpoint}", params=PARAMS)
        assert (response.status_code, response.json()) == (
            429, {"detail": "请求过于频繁，请至少间隔 3 秒"},
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as other:
        await login(other, "mobile_readonly", totp_window)
        assert (await other.get(f"{MOBILE}/query", params=PARAMS)).status_code == 200
    clock.now = 103.0
    for endpoint in ("query", "current"):
        assert (await client.get(f"{MOBILE}/{endpoint}", params=PARAMS)).status_code == 200


@pytest.mark.parametrize("endpoint", ["query", "current"])
async def test_mobile_premium_index_timeout_cancels_upstream(phone, monkeypatch, endpoint):
    _, client, service, _, _ = phone
    entered, cancelled, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def deadline(tasks, *, timeout, return_when):
        assert timeout == 60
        await asyncio.wait_for(entered.wait(), 5)
        return await asyncio.wait(tasks, timeout=0, return_when=return_when)

    getattr(service, endpoint).side_effect = slow
    service.aclose.side_effect = closed.set
    monkeypatch.setattr(mobile_query_utils, "wait", deadline)
    response = await client.get(f"{MOBILE}/{endpoint}", params=PARAMS)
    assert (response.status_code, response.json()) == (504, {"detail": "查询超时，可缩短时长或加大周期"})
    await asyncio.wait_for(cancelled.wait(), 5)
    await asyncio.wait_for(closed.wait(), 5)


async def test_mobile_premium_index_disconnect_releases_query_while_cleanup_is_running(phone):
    app, client, service, _, _ = phone
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = service.query.side_effect

    async def slow(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
            await release.wait()

    service.query.side_effect = slow
    path = f"{MOBILE}/query"
    request = client.build_request("GET", path, params=PARAMS)
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": request.url.query,
        "headers": [(key.lower(), value) for key, value in request.headers.raw],
        "client": ("127.0.0.1", 1234), "server": ("test", 443),
    }
    send = AsyncMock()
    handler = asyncio.create_task(app(scope, incoming.get, send))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        incoming.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(cancelled.wait(), 5)
        await asyncio.wait_for(handler, 5)
        assert send.await_args_list[0].args[0]["status"] == 499
        service.aclose.assert_not_awaited()
        service.query.side_effect = original
        response = await asyncio.wait_for(client.get(path, params=PARAMS), 5)
        assert response.status_code == 200, response.text
    finally:
        release.set()
        if not handler.done():
            handler.cancel()
        await asyncio.gather(handler, return_exceptions=True)
        await asyncio.gather(*app.state.mobile_premium_index_cleanup_tasks)
    assert service.aclose.await_count == 2


async def test_mobile_premium_index_sanitizes_failures_and_warnings(phone, caplog):
    _, client, service, clock, _ = phone
    original = service.query.side_effect
    diagnostic = "test exchange diagnostic https://upstream.test/?token=url-secret\nAuthorization: header-secret"
    service.query.side_effect = PremiumIndexQueryError(diagnostic)
    service.current.side_effect = RuntimeError(diagnostic)
    for endpoint in ("query", "current"):
        response = await client.get(f"{MOBILE}/{endpoint}", params=PARAMS)
        assert (response.status_code, response.json()) == (502, {"detail": "交易所数据获取失败（Hyperliquid）"})

    async def warn(*args, **kwargs):
        result = await original(*args, **kwargs)
        result.warnings = [
            "Hyperliquid 没有公开分钟级历史 premium 接口，曲线已用分钟K线和小时 premium 锚点估算。",
            f"请求720小时，最早可用数据为{AT.isoformat()}，已按可获取数据展示。",
            f"hyperliquid:io:ANTH 溢价指数历史失败: {diagnostic}",
            f"溢价指数历史失败: {diagnostic}",
            f"未知上游提示: {diagnostic}",
        ]
        return result

    clock.now += 3
    service.query.side_effect = warn
    response = await client.get(f"{MOBILE}/query", params=PARAMS)
    assert response.status_code == 200, response.text
    assert response.json()["warnings"] == [
        "Hyperliquid 分钟级历史溢价指数为估算值。",
        "历史数据覆盖不足，已按可获取数据展示。",
        "部分历史溢价指数获取失败。",
        "部分交易所数据暂不可用。",
    ]
    assert "test exchange diagnostic" in caplog.text
    assert "url-secret" not in caplog.text and "header-secret" not in caplog.text


@pytest.mark.parametrize(("endpoint", "updates"), [
    ("query", {"hours": 0}), ("query", {"interval_minutes": 2}),
    ("query", {"exchange": "unsupported"}), ("query", {"dex": "xyz"}),
    ("current", {"symbol": ""}), ("current", {"exchange": "okx"}),
])
async def test_mobile_premium_index_validation_matches_web(phone, endpoint, updates):
    app, client, service, _, totp_window = phone
    params = dict(PARAMS, **updates)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as full:
        await login(full, "full", totp_window)
        web = await full.get(f"{WEB}/{endpoint}", params=params)
    mobile = await client.get(f"{MOBILE}/{endpoint}", params=params)
    assert mobile.status_code == web.status_code == 422
    assert mobile.json() == web.json()
    service.query.assert_not_awaited()
    service.current.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["query", "current"])
async def test_mobile_premium_index_rejects_multiple_dex_prefixes(phone, endpoint):
    _, client, service, _, _ = phone
    params = {"exchange": "hyperliquid", "symbol": "io:xyz:ANTH"}
    response = await client.get(f"{MOBILE}/{endpoint}", params=params)

    assert (response.status_code, response.json()) == (422, {"detail": "Hyperliquid 标的只允许一个 DEX 前缀"})
    service.query.assert_not_awaited()
    service.current.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["query", "current"])
async def test_mobile_premium_index_sanitizes_alias_value_error(phone, monkeypatch, endpoint):
    _, client, service, _, _ = phone

    def fail_resolve(*args, **kwargs):
        raise ValueError("internal alias error: diagnostic details")

    monkeypatch.setattr(SymbolAliasResolver, "resolve", fail_resolve)
    response = await client.get(f"{MOBILE}/{endpoint}", params=PARAMS)

    assert (response.status_code, response.json()) == (422, {"detail": "无法解析溢价指数标的"})
    service.query.assert_not_awaited()
    service.current.assert_not_awaited()
