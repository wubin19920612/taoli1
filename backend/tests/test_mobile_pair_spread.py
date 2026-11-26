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
from app.core.config import Settings
from app.main import create_app
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadFundingPoint,
    PairSpreadKlinePoint,
    PairSpreadPoint,
    PairSpreadQueryResult,
)
from app.services.pair_spread_query import (
    REALTIME_CACHE_MISS_WARNING,
    PairSpreadQueryError,
    PairSpreadQueryService,
    _stats,
)

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
        funding = []
        if include_current:
            for leg in (leg1, leg2):
                funding.extend(await service._fetch_funding_with_warning(leg, AT, AT, []))
        return PairSpreadQueryResult(
            leg1=leg1, leg2=leg2, hours=hours, interval_minutes=interval_minutes,
            interval_seconds=interval_seconds, leg2_multiplier=leg2_multiplier, observed_at=now or AT,
            complete=False, warnings=[REALTIME_CACHE_MISS_WARNING], point_count=len(points), points=points,
            spread_abs=_stats([point.spread_abs for point in points]),
            spread_pct=_stats([point.spread_pct for point in points]),
            current=current, funding_history=funding,
        )

    service = SimpleNamespace(
        query=AsyncMock(side_effect=query), aclose=AsyncMock(),
        _fetch_current_with_warning=AsyncMock(side_effect=lambda leg, warnings: (
            CURRENT1 if leg.exchange == "okx" else CURRENT2
        )),
        _fetch_funding_with_warning=AsyncMock(side_effect=lambda leg, start, end, warnings: [
            PairSpreadFundingPoint(exchange=leg.exchange, symbol=leg.symbol, dex=leg.dex,
                                   funding_time=AT, funding_rate_pct=-0.012),
        ] if leg.exchange == "hyperliquid" else []),
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
        "leg1_rate_pct": 0,
        "leg2_rate_pct": -0.02,
        "funding_history": [{"funding_time": "2026-01-01T00:00:00Z", "leg": "leg2", "funding_rate_pct": -0.012}],
    }

    clock.now += 3
    historical = await client.get(f"{MOBILE}/query", params=dict(PARAMS, include_current="false"))
    assert historical.status_code == 200, historical.text
    assert historical.json()["current"] is None
    assert historical.json()["funding"] == {
        "leg1_rate_pct": None, "leg2_rate_pct": None, "funding_history": [],
    }
    assert service.query.await_args.kwargs["include_current"] is False


@pytest.mark.parametrize("count", [8, 1603])
def test_mobile_pair_spread_downsampling_keeps_extrema_at_duplicate_times(count):
    def point(index, spread):
        return PairSpreadPoint(
            bucket_at=AT + timedelta(minutes=index),
            leg1_close=100,
            leg2_close=100 + spread,
            spread_abs=spread,
            spread_pct=spread,
        )

    first, last = point(0, -80), point(count - 1, 90)
    bucket_extreme = point(count // 2, 60)
    points = [point(i, 1) for i in range(count)] + [
        first, point(count // 2, -50), bucket_extreme, last,
    ]
    sampled = routes_mobile_pair._downsample(points)
    timestamps = [p.bucket_at for p in sampled]
    assert timestamps == sorted(set(timestamps))
    assert len(sampled) <= 800
    if count < 800:
        assert len(sampled) == count
    assert sampled[0] == min(sampled, key=lambda p: p.spread_pct) == first
    assert sampled[-1] == max(sampled, key=lambda p: p.spread_pct) == last
    assert bucket_extreme in sampled


async def test_mobile_pair_spread_funding_history_is_latest_200_and_distinguishes_dex_legs(phone):
    _, client, service, _ = phone

    async def funding(leg, start, end, warnings):
        return [
            PairSpreadFundingPoint(
                exchange=leg.exchange, symbol=leg.symbol, dex=leg.dex,
                funding_time=AT + timedelta(hours=i), funding_rate_pct=-i / 1000,
            ) for i in reversed(range(220)) if i % 2 == (leg.dex == "io")
        ]

    service._fetch_funding_with_warning.side_effect = funding
    params = dict(
        PARAMS,
        leg1_exchange="hyperliquid", leg1_symbol="xyz:TEST", leg1_dex="xyz", leg2_symbol="io:TEST",
    )
    response = await client.get(f"{MOBILE}/query", params=params)
    assert response.status_code == 200, response.text
    history = response.json()["funding"]["funding_history"]
    assert len(history) == 200
    for i, point in enumerate(history, start=20):
        assert point == {
            "funding_time": (AT + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
            "leg": "leg2" if i % 2 else "leg1",
            "funding_rate_pct": -i / 1000,
        }


async def test_mobile_pair_spread_funding_history_tracks_same_market_legs_by_source(phone, monkeypatch):
    app, client, _, _ = phone
    service = PairSpreadQueryService()
    app.state.pair_spread_query_service_factory = lambda: service
    monkeypatch.setattr(service, "_fetch_klines_with_warning", AsyncMock(return_value=[
        PairSpreadKlinePoint(bucket_at=AT, close=10),
        PairSpreadKlinePoint(bucket_at=AT + timedelta(minutes=1), close=11),
    ]))
    monkeypatch.setattr(service, "_fetch_current_with_warning", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_query_open_interest", AsyncMock(return_value=([], ("current",) * 3)))
    funding = AsyncMock(side_effect=[
        [PairSpreadFundingPoint(exchange="hyperliquid", dex="io", symbol="ANTHUSDT",
                                funding_time=AT + timedelta(minutes=1), funding_rate_pct=0.01)],
        [PairSpreadFundingPoint(exchange="hyperliquid", dex="io", symbol="ANTHUSDT",
                                funding_time=AT, funding_rate_pct=-0.02)],
    ])
    monkeypatch.setattr(service, "_fetch_hyperliquid_funding", funding)

    params = dict(PARAMS, leg1_exchange="hyperliquid", leg1_symbol="io:ANTH", leg1_dex="io",
                  end_at=(AT + timedelta(minutes=1)).isoformat())
    response = await client.get(f"{MOBILE}/query", params=params)
    assert response.status_code == 200, response.text
    assert funding.await_count == 2
    assert response.json()["funding"]["funding_history"] == [
        {"funding_time": "2026-01-01T00:00:00Z", "leg": "leg2", "funding_rate_pct": -0.02},
        {"funding_time": "2026-01-01T00:01:00Z", "leg": "leg1", "funding_rate_pct": 0.01},
    ]


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


async def test_mobile_pair_spread_timeout_returns_before_cancellation_or_close(phone, monkeypatch, caplog):
    app, client, service, clock = phone
    original = service.query.side_effect
    entered = asyncio.Event()
    cancelling, release_query = asyncio.Event(), asyncio.Event()
    closing, release_close = asyncio.Event(), asyncio.Event()

    async def wait_for_deadline(tasks, *, timeout, return_when):
        assert timeout == 60
        await asyncio.wait_for(entered.wait(), timeout=5)
        return await asyncio.wait(tasks, timeout=0, return_when=return_when)

    async def slow(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelling.set()
            await release_query.wait()

    async def close():
        closing.set()
        await release_close.wait()
        raise RuntimeError("test close failed")

    service.query.side_effect = slow
    service.aclose.side_effect = close
    path = f"{MOBILE}/query"
    try:
        with monkeypatch.context() as patch:
            patch.setattr(routes_mobile_pair, "wait", wait_for_deadline)
            response = await asyncio.wait_for(client.get(path, params=PARAMS), timeout=5)
        assert (response.status_code, response.json()) == (
            504, {"detail": "查询超时，可缩短时长或加大周期"},
        )
        limits = app.state.mobile_pair_spread_limits
        assert not limits._active_queries
        await asyncio.wait_for(cancelling.wait(), timeout=5)
        assert not closing.is_set()
        release_query.set()
        await asyncio.wait_for(closing.wait(), timeout=5)

        clock.now += 3
        service.query.side_effect = original
        service.aclose.side_effect = None
        response = await asyncio.wait_for(client.get(path, params=PARAMS), timeout=5)
        assert response.status_code == 200
    finally:
        release_query.set()
        release_close.set()
        await asyncio.gather(*app.state.mobile_pair_spread_limits._upstream_tasks, return_exceptions=True)
    assert "test close failed" in caplog.text
    assert not limits._upstream_tasks


async def test_mobile_pair_spread_disconnect_allows_immediate_retry_during_cleanup(phone):
    app, client, service, _ = phone
    entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    both_closed = asyncio.Event()
    original = service.query.side_effect

    async def slow(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelling.set()
            await release.wait()

    async def close():
        if service.aclose.await_count == 2:
            both_closed.set()

    service.query.side_effect = slow
    service.aclose.side_effect = close
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
    handler = asyncio.create_task(app(scope, incoming.get, AsyncMock()))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        incoming.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(cancelling.wait(), 2)
        service.query.side_effect = original
        response = await asyncio.wait_for(client.get(path, params=PARAMS), 2)
        assert response.status_code == 200, response.text
        await asyncio.wait_for(handler, 2)
    finally:
        release.set()
        handler.cancel()
        await asyncio.gather(handler, return_exceptions=True)
        await asyncio.gather(*app.state.mobile_pair_spread_limits._upstream_tasks, return_exceptions=True)
    await asyncio.wait_for(both_closed.wait(), 2)
    assert service.aclose.await_count == 2


async def test_mobile_pair_spread_pending_cleanup_has_a_capacity_limit(phone, monkeypatch):
    app, client, service, clock = phone
    closing, release = asyncio.Event(), asyncio.Event()

    async def close():
        closing.set()
        await release.wait()

    monkeypatch.setattr(routes_mobile_pair, "MAX_UPSTREAM_TASKS", 1)
    service.aclose.side_effect = close
    try:
        first = await asyncio.wait_for(client.get(f"{MOBILE}/query", params=PARAMS), timeout=5)
        assert first.status_code == 200
        await asyncio.wait_for(closing.wait(), timeout=5)
        response = await client.get(f"{MOBILE}/current", params=PARAMS)
        assert (response.status_code, response.json()) == (503, {"detail": "交易所查询繁忙，请稍后重试"})
        service._fetch_current_with_warning.assert_not_awaited()
        assert len(app.state.mobile_pair_spread_limits._upstream_tasks) == 1
    finally:
        release.set()
        await asyncio.gather(*app.state.mobile_pair_spread_limits._upstream_tasks)
    clock.now += 3
    assert (await client.get(f"{MOBILE}/current", params=PARAMS)).status_code == 200


async def test_mobile_pair_spread_expired_cancellation_releases_capacity(phone, monkeypatch, caplog):
    app, client, service, clock = phone
    entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = service.query.side_effect

    async def wait_for_deadline(tasks, *, timeout, return_when):
        await asyncio.wait_for(entered.wait(), 2)
        return await asyncio.wait(tasks, timeout=0, return_when=return_when)

    async def slow(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelling.set()
            await release.wait()

    assert routes_mobile_pair.CLOSE_TIMEOUT_SECONDS == 10
    monkeypatch.setattr(routes_mobile_pair, "CLOSE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(routes_mobile_pair, "MAX_UPSTREAM_TASKS", 1)
    service.query.side_effect = slow
    path = f"{MOBILE}/query"
    try:
        with monkeypatch.context() as patch:
            patch.setattr(routes_mobile_pair, "wait", wait_for_deadline)
            assert (await client.get(path, params=PARAMS)).status_code == 504
        await asyncio.wait_for(cancelling.wait(), 2)
        cleanup, = app.state.mobile_pair_spread_cleanup_tasks
        limits = app.state.mobile_pair_spread_limits
        assert cleanup in limits._upstream_tasks
        async with asyncio.timeout(2):
            while cleanup in limits._upstream_tasks:
                await asyncio.sleep(0.01)
        assert cleanup in app.state.mobile_pair_spread_cleanup_tasks
        assert not cleanup.done()
        assert "释放查询名额" in caplog.text
        service.aclose.assert_not_awaited()

        clock.now += 3
        service.query.side_effect = original
        assert (await client.get(path, params=PARAMS)).status_code == 200
    finally:
        release.set()
        await asyncio.gather(*app.state.mobile_pair_spread_cleanup_tasks, return_exceptions=True)
    assert service.aclose.await_count == 2
    assert not app.state.mobile_pair_spread_cleanup_tasks


async def test_mobile_pair_spread_lifespan_cancels_and_waits_for_registered_cleanups(tmp_path, monkeypatch, caplog):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'radar.db'}")
    app = create_app(settings=settings, start_background_workers=False)
    started = [asyncio.Event(), asyncio.Event()]
    cancelled = [asyncio.Event(), asyncio.Event()]
    finished, release = asyncio.Event(), asyncio.Event()
    path = "/api/mobile/pair-spread/query"

    async def close(i):
        started[i].set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled[i].set()
            if i == 1:
                await release.wait()
            else:
                await asyncio.sleep(0)
                finished.set()
            raise

    async def query(service):
        return service

    async def receive():
        await asyncio.Event().wait()

    assert routes_mobile_pair.CLEANUP_SHUTDOWN_SECONDS == 5
    monkeypatch.setattr(routes_mobile_pair, "CLEANUP_SHUTDOWN_SECONDS", 0.05)
    request = routes_mobile_pair.Request({"type": "http", "app": app, "path": path}, receive=receive)
    request.state.auth_session_hash = "mobile"
    tasks = set()
    try:
        async with asyncio.timeout(2):
            async with app.router.lifespan_context(app):
                limits = routes_mobile_pair._limits(request)
                for i in range(2):
                    service = SimpleNamespace(aclose=lambda i=i: close(i))
                    app.state.pair_spread_query_service_factory = lambda: service
                    assert await limits.run_upstream(request, "query", 0, query) is service
                await asyncio.gather(*(event.wait() for event in started))
                tasks = set(app.state.mobile_pair_spread_cleanup_tasks)
                assert len(tasks) == 2
        assert all(event.is_set() for event in cancelled)
        assert finished.is_set()
        assert len(app.state.mobile_pair_spread_cleanup_tasks) == 1
        assert "清理任务未完成" in caplog.text
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert not app.state.mobile_pair_spread_cleanup_tasks


async def test_mobile_pair_spread_upstream_errors_and_warnings_are_sanitized(phone, caplog):
    _, client, service, clock = phone
    original = service.query.side_effect
    failure = (
        "ReadTimeout: upstream diagnostic /private/ticker?api_key=url-key "
        "'Authorization': 'Bearer header-secret'"
    )
    service.query.side_effect = PairSpreadQueryError(failure)
    response = await client.get(f"{MOBILE}/query", params=PARAMS)
    assert (response.status_code, response.json()) == (
        502, {"detail": "交易所数据获取失败（OKX、Hyperliquid）"},
    )
    service.aclose.assert_awaited_once_with()

    async def current(leg, warnings):
        if leg.exchange == "okx":
            return CURRENT1
        warnings.append(f"当前价格/资金失败: {failure}")
        return None

    service._fetch_current_with_warning.side_effect = current
    response = await client.get(f"{MOBILE}/current", params=PARAMS)
    assert (response.status_code, response.json()) == (502, {"detail": "交易所数据获取失败（Hyperliquid）"})
    assert service.aclose.await_count == 2

    async def partial_result(*args, **kwargs):
        result = await original(*args, **kwargs)
        result.warnings += [
            f"hyperliquid:io:合约:ANTHUSDT 历史资金费率失败: {failure}",
            "查询窗口超过7天，历史周期已从1分钟自动调整为至少1小时（实际使用1小时）。",
            f"未知提示: {failure}",
        ]
        return result

    service.query.side_effect = partial_result
    clock.now += 3
    response = await client.get(f"{MOBILE}/query", params=PARAMS)
    assert response.status_code == 200
    assert response.json()["warnings"] == [
        REALTIME_CACHE_MISS_WARNING,
        "部分交易所的历史资金费率获取失败。",
        "查询窗口较长，已自动调整历史周期。",
        "部分交易所数据暂不可用。",
    ]
    assert any(
        record.levelname == "WARNING" and "ReadTimeout: upstream diagnostic" in record.message
        for record in caplog.records
    )
    assert "url-key" not in caplog.text
    assert "header-secret" not in caplog.text


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
    limits = routes_mobile_pair._PairSpreadLimits(set())
    with limits.query("still-running"):
        limits.start("expired", "current")
        now += 3
        limits.start("new", "current")
        assert limits._starts == {("new", "current"): now}
        with pytest.raises(routes_mobile_pair.HTTPException, match="上一个查询还在进行"):
            with limits.query("still-running"):
                pytest.fail("an active query was allowed to start twice")
