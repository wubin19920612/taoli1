from auth_helpers import TEST_TOTP_SECRET, session_headers, IsolatedSessionLookup
from app.core.config import Settings
from app.core.security import require_dashboard_password
from fastapi import Depends
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes_pair_spread import router
from app.models.market import MarketType
from app.models.pair_range_backtest import PairRangeBacktestRequest
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadFundingPoint,
    PairSpreadKlinePoint,
)
from app.models.settings import FeeSettings
from app.services.funding_history_pagination import PartialFundingHistoryError
from app.services.pair_range_backtest import RangeLegData, calculate_range_backtest
from app.services.pair_spread_query import PairSpreadQueryError, PairSpreadQueryService

START = datetime(2026, 10, 1, tzinfo=UTC)


def request_for(hours=24, **updates):
    return PairRangeBacktestRequest.model_validate(
        dict(
            leg1={"exchange": "binance", "symbol": "BTCUSDT"},
            leg2={"exchange": "okx", "symbol": "BTCUSDT"},
            direction="long1_short2",
            start_at=START,
            end_at=START + timedelta(hours=hours),
            notional_usd=1000,
            **updates,
        )
    )


def book(leg, **updates):
    values = dict(
        **leg.model_dump(),
        raw_symbol=leg.symbol,
        price=100,
        price_field="mid_price",
        bid_price=99.9,
        ask_price=100.1,
        timestamp=START,
        funding_interval_hours=8,
        funding_next_time=START + timedelta(hours=8),
    )
    return PairSpreadCurrentLeg(**(values | updates))


def data_for(request, leg, *, entry=100, exit=110, rate=0.01, count=3):
    return RangeLegData(
        entry_points=(PairSpreadKlinePoint(
            bucket_at=request.start_at - timedelta(minutes=1), close=entry,
        ),),
        exit_points=(PairSpreadKlinePoint(
            bucket_at=request.end_at - timedelta(minutes=1), close=exit,
        ),),
        funding=tuple(
            PairSpreadFundingPoint(
                exchange=leg.exchange,
                symbol=leg.symbol,
                dex=leg.dex,
                funding_time=START + timedelta(hours=8 * i),
                funding_rate_pct=rate,
            )
            for i in range(1, count + 1)
        ),
        current=book(leg),
    )


def calculate(request, one, two, fees=None):
    return calculate_range_backtest(
        request,
        leg1_data=one,
        leg2_data=two,
        fee_settings=fees or FeeSettings(),
    )


@pytest.mark.parametrize("hours,count", [(1, 0), (8, 1), (24, 3)])
def test_hand_calculated_price_funding_and_four_costs(hours, count):
    request = request_for(hours)
    one = data_for(request, request.leg1, count=count)
    two = data_for(request, request.leg2, entry=200, exit=180, rate=0.02, count=count)
    result = calculate(request, one, two)
    assert result.price_pnl.model_dump() == {"leg1": 100.0, "leg2": 100.0, "total": 200.0}
    assert result.funding_pnl.total == pytest.approx(count * 0.1)
    assert len(result.funding_pnl.settlements) == count * 2
    assert result.funding_pnl.coverage.model_dump() == {"leg1": 1.0, "leg2": 1.0}
    assert result.fees == 2.0  # 4 * 1000 * 0.05%
    assert result.spread_cost_est == pytest.approx(4.0)  # 4 * 1000 * half-spread / mid
    assert result.total == pytest.approx(194 + count * 0.1)
    assert result.total_pct_of_notional == pytest.approx(result.total / 10)
    assert result.data_quality.is_estimate is True


def test_direction_reverses_gross_pnl_but_costs_stay_positive():
    request = request_for()
    one, two = data_for(request, request.leg1), data_for(request, request.leg2, rate=-0.02)
    forward = calculate(request, one, two)
    reverse_request = request.model_copy(update={"direction": "short1_long2"})
    reverse = calculate(reverse_request, one, two)
    assert reverse.price_pnl.leg1 == -forward.price_pnl.leg1
    assert reverse.price_pnl.leg2 == -forward.price_pnl.leg2
    assert reverse.funding_pnl.total == -forward.funding_pnl.total
    assert reverse.total + forward.total == pytest.approx(
        -2 * (forward.fees + forward.spread_cost_est)
    )
    no_cost = FeeSettings(spot_fee_pct=0, future_fee_pct=0)
    one = replace(one, current=book(request.leg1, bid_price=100, ask_price=100))
    two = replace(two, current=book(request.leg2, bid_price=100, ask_price=100))
    assert (
        calculate(request, one, two, no_cost).total
        == -calculate(reverse_request, one, two, no_cost).total
    )


def test_missing_funding_and_unknown_period_are_not_zero_filled():
    request = request_for()
    one = data_for(request, request.leg1, count=1)
    two = replace(data_for(request, request.leg2, count=0), current=None)
    result = calculate(request, one, two)
    assert result.funding_pnl.coverage.leg1 == pytest.approx(1 / 3)
    assert result.funding_pnl.coverage.leg2 is None
    assert len(result.funding_pnl.settlements) == 1
    assert any("资金费缺失" in warning for warning in result.warnings)
    assert any("周期未知" in warning for warning in result.warnings)
    assert result.spread_cost_est is None
    assert result.total == pytest.approx(
        result.price_pnl.total + result.funding_pnl.total - result.fees
    )


def test_settlement_interval_is_open_start_closed_end_and_deduplicated():
    request = request_for(8)
    one = data_for(request, request.leg1, count=1)
    at_end = one.funding[0]
    one = replace(
        one,
        funding=(
            at_end,
            at_end,
            at_end.model_copy(update={"funding_time": START}),
            at_end.model_copy(update={"funding_time": request.end_at + timedelta(seconds=1)}),
        ),
    )
    result = calculate(request, one, data_for(request, request.leg2, count=0))
    assert len(result.funding_pnl.settlements) == 1
    assert result.funding_pnl.settlements[0].funding_time == request.end_at


@pytest.mark.parametrize("offset,has_gap", [(120, False), (121, True), (-121, True)])
def test_endpoint_nearest_selection_and_deviation_warning(offset, has_gap):
    request = request_for()
    one = data_for(request, request.leg1)
    one = replace(
        one,
        entry_points=(
            PairSpreadKlinePoint(bucket_at=START + timedelta(seconds=offset - 60), close=100),
            PairSpreadKlinePoint(bucket_at=START + timedelta(seconds=500), close=999),
        ),
    )
    result = calculate(request, one, data_for(request, request.leg2))
    assert result.entry.leg1.price == 100
    assert result.entry.leg1.deviation_seconds == abs(offset)
    assert any(g.kind == "entry" for g in result.data_quality.gaps) == has_gap


def test_spot_fee_and_no_funding_and_missing_price():
    request = request_for()
    request.leg1.market_type = MarketType.SPOT
    one = data_for(request, request.leg1)
    two = data_for(request, request.leg2)
    result = calculate(request, one, two)
    assert result.fees == 3.0
    assert result.funding_pnl.coverage.leg1 is None
    assert result.data_quality.funding_coverage == result.funding_pnl.coverage
    assert all(p.leg == "leg2" for p in result.funding_pnl.settlements)
    with pytest.raises(ValueError, match="no historical price"):
        calculate(request, replace(one, entry_points=()), two)


def test_exact_hyperliquid_dex_identity_is_required():
    request = request_for()
    request.leg1 = request.leg1.model_validate({"exchange": "hyperliquid", "symbol": "xyz:BTC"})
    one = data_for(request, request.leg1)
    result = calculate(request, one, data_for(request, request.leg2))
    assert result.entry.leg1.market.dex == "xyz"
    assert result.funding_pnl.settlements[0].dex == "xyz"
    wrong = one.funding[0].model_copy(update={"dex": "main"})
    with pytest.raises(ValueError, match="identity mismatch"):
        calculate(request, replace(one, funding=(wrong,)), data_for(request, request.leg2))


@pytest.mark.parametrize(
    "exchange,limit,forward",
    [
        ("binance", 1000, True),
        ("aster", 1000, True),
        ("okx", 100, False),
        ("bybit", 200, False),
        ("gate", 1000, False),
        ("bitget", 100, False),
        ("hyperliquid", 500, True),
    ],
)
async def test_exchange_pagination_splices_sorts_deduplicates_and_uses_actual_rates(
    exchange, limit, forward
):
    service = PairSpreadQueryService()
    times = [START + timedelta(minutes=i) for i in range(limit + 3)]
    ordered = times if forward else list(reversed(times))
    calls = []

    def row(at):
        ms = int(at.timestamp() * 1000)
        return {
            "fundingTime": ms,
            "fundingRate": "0.0001",
            "realizedRate": "0.0002",
            "fundingRateTimestamp": ms,
            "time": ms,
            "t": int(at.timestamp()),
            "r": 0,
        }

    async def fake(url, body=None):
        query = parse_qs(urlsplit(url).query)
        calls.append(body or query)
        # Deliberately overlap one row between pages; the output must dedupe.
        rows = [row(t) for t in (ordered[:limit] if len(calls) == 1 else ordered[limit - 1 :])]
        if exchange == "okx":
            return {"code": "0", "data": rows}
        if exchange == "bybit":
            return {"retCode": 0, "result": {"list": rows}}
        if exchange == "bitget":
            return {"code": "00000", "data": rows}
        return rows

    service._get_json = fake
    service._post_json = fake
    service._resolve_hyperliquid_coin = AsyncMock(return_value=("xyz:BTC", "xyz"))
    try:
        points = await service._fetch_funding_history(exchange, "BTCUSDT", times[0], times[-1])
    finally:
        await service.aclose()
    assert len(calls) == 2
    assert [p.funding_time for p in points] == times
    assert all(
        p.funding_rate_pct
        == pytest.approx(0.02 if exchange == "okx" else 0 if exchange == "gate" else 0.01)
        for p in points
    )
    if exchange in {"binance", "aster"}:
        assert int(calls[1]["startTime"][0]) == int(ordered[limit - 1].timestamp() * 1000) + 1
    elif exchange == "okx":
        assert int(calls[1]["after"][0]) == int(ordered[limit - 1].timestamp() * 1000)
    elif exchange == "bybit":
        assert int(calls[1]["endTime"][0]) == int(ordered[limit - 1].timestamp() * 1000) - 1
    elif exchange == "bitget":
        assert calls[1]["pageNo"] == ["2"]
    elif exchange == "hyperliquid":
        assert calls[1]["coin"] == "xyz:BTC"
        assert all(p.dex == "xyz" for p in points)


@pytest.mark.parametrize("failure", ["repeat", "timeout", "invalid"])
async def test_partial_funding_failure_retains_prior_records_with_warning(failure):
    service = PairSpreadQueryService()
    request = request_for()
    rows = [
        {
            "fundingTime": int((START + timedelta(minutes=i)).timestamp() * 1000),
            "realizedRate": "0.0001",
        }
        for i in range(100)
    ]
    responses = [{"data": rows}]
    if failure == "timeout":
        responses.append(TimeoutError("upstream timeout"))
    elif failure == "invalid":
        responses.append(
            {"data": [{"fundingTime": int(START.timestamp() * 1000), "realizedRate": ""}]}
        )
    else:
        responses.extend([{"data": rows}] * 2)
    service._get_json = AsyncMock(side_effect=responses)
    warnings = []
    try:
        points = await service._fetch_funding_with_warning(
            request.leg2, START - timedelta(hours=1), request.end_at, warnings
        )
    finally:
        await service.aclose()
    assert len(points) == 100
    assert warnings and "历史资金费率失败" in warnings[0]


async def test_okx_does_not_use_predicted_rate_for_missing_realized_rate():
    service = PairSpreadQueryService()
    service._get_json = AsyncMock(
        return_value={
            "data": [
                {
                    "fundingTime": int(START.timestamp() * 1000),
                    "fundingRate": "0.01",
                    "realizedRate": "",
                }
            ]
        }
    )
    try:
        with pytest.raises(PartialFundingHistoryError) as error:
            await service._fetch_okx_funding("BTCUSDT", START, START + timedelta(hours=8))
        assert error.value.points == []
    finally:
        await service.aclose()


@pytest.mark.parametrize("same_leg", [False, True])
async def test_data_loader_only_fetches_endpoints_and_funding_once(same_leg):
    request = request_for(30 * 24)
    if same_leg:
        request.leg2 = request.leg1
    service = PairSpreadQueryService()
    windows, funding_calls = [], []

    async def klines(exchange, symbol, market_type, dex, start, end, interval, warnings):
        windows.append((start, end, interval))
        return [PairSpreadKlinePoint(bucket_at=start + (end - start) / 2, close=100)]

    async def funding(leg, start, end, warnings):
        funding_calls.append((leg, start, end))
        return []

    service._fetch_klines_with_warning = klines
    service._fetch_funding_with_warning = funding
    service._fetch_current_with_warning = AsyncMock(return_value=None)
    try:
        result = await service.query_range_backtest(request, fee_settings=FeeSettings())
    finally:
        await service.aclose()
    assert len(windows) == (2 if same_leg else 4)
    assert len(funding_calls) == (1 if same_leg else 2)
    assert all(
        end - start == timedelta(hours=10) and interval == 60 for start, end, interval in windows
    )
    assert result.data_quality.granularity_seconds == {"leg1": 3600, "leg2": 3600}


@pytest.fixture
def route_app():
    app = FastAPI()
    app.include_router(router, prefix="/api", dependencies=[Depends(require_dashboard_password)])
    app.state.settings = Settings(dashboard_password="test-password", dashboard_totp_secret=TEST_TOTP_SECRET)
    app.state.auth_store = IsolatedSessionLookup()
    request = request_for()
    result = calculate(request, data_for(request, request.leg1), data_for(request, request.leg2))
    service = SimpleNamespace(
        query_range_backtest=AsyncMock(return_value=result), aclose=AsyncMock()
    )
    app.state.pair_spread_query_service_factory = lambda: service
    return app, service


async def post(app, payload, password="test-password"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/pair-spread/range-backtest",
            json=payload,
            headers=session_headers(password),
        )


async def test_route_200_and_dependency_cleanup(route_app):
    app, service = route_app
    result = await post(app, request_for().model_dump(mode="json"))
    assert result.status_code == 200, result.text
    assert result.json()["data_quality"]["is_estimate"] is True
    assert result.json()["data_quality"]["funding_coverage"] == result.json()["funding_pnl"]["coverage"]
    service.query_range_backtest.assert_awaited_once()
    service.aclose.assert_awaited_once()


@pytest.mark.parametrize(
    "updates",
    [
        {"end_at": (START + timedelta(days=30, seconds=1)).isoformat()},
        {"end_at": START.isoformat()},
        {"end_at": (START - timedelta(seconds=1)).isoformat()},
        {"start_at": "2026-10-01T00:00:00"},
        {"end_at": "2026-10-02T00:00:00"},
        {"notional_usd": 0},
        {"notional_usd": -1},
        {"notional_usd": 10_000_001},
        {"direction": "bad"},
        {"leg1": {"exchange": "bad", "symbol": "BTC"}},
    ],
)
async def test_route_422_rejects_before_io(route_app, updates):
    app, service = route_app
    response = await post(app, request_for().model_dump(mode="json") | updates)
    assert response.status_code == 422, response.text
    service.query_range_backtest.assert_not_awaited()


async def test_route_exact_30_days_allowed_and_auth_required(route_app):
    app, service = route_app
    payload = request_for(30 * 24).model_dump(mode="json")
    assert (await post(app, payload)).status_code == 200
    assert (await post(app, payload, password="wrong")).status_code == 401
    service.query_range_backtest.assert_awaited_once()


@pytest.mark.parametrize(
    "error,status", [(PairSpreadQueryError("no prices"), 502), (TimeoutError(), 504)]
)
async def test_route_error_closes_service(route_app, error, status):
    app, service = route_app
    service.query_range_backtest.side_effect = error
    response = await post(app, request_for().model_dump(mode="json"))
    assert response.status_code == status
    service.aclose.assert_awaited_once()


async def test_bitget_pages_past_newer_history_to_requested_old_interval():
    service = PairSpreadQueryService()
    requested = [START + timedelta(hours=i) for i in range(1, 4)]
    newer = [START + timedelta(hours=i) for i in range(104, 4, -1)]
    service._get_json = AsyncMock(
        side_effect=[
            {
                "data": [
                    {"fundingTime": int(at.timestamp() * 1000), "fundingRate": "0.0001"}
                    for at in times
                ]
            }
            for times in (newer, list(reversed(requested)))
        ]
    )
    try:
        result = await service._fetch_bitget_funding("BTCUSDT", START, requested[-1])
    finally:
        await service.aclose()
    assert [p.funding_time for p in result] == requested
    assert service._get_json.await_count == 2


@pytest.mark.parametrize("bid,ask", [(None, 100), (101, 100), (0, 100), (float("nan"), 100)])
def test_unusable_orderbook_does_not_become_free_spread(bid, ask):
    request = request_for()
    one = replace(
        data_for(request, request.leg1), current=book(request.leg1, bid_price=bid, ask_price=ask)
    )
    result = calculate(request, one, data_for(request, request.leg2))
    assert result.spread_cost_est is None
    assert any("未扣除点差成本" in w for w in result.warnings)


def test_short_interval_crossing_settlement_and_non_utc_input():
    request = request_for().model_copy(
        update={
            "start_at": datetime.fromisoformat("2026-10-01T16:30:00+09:00"),
            "end_at": datetime.fromisoformat("2026-10-01T17:30:00+09:00"),
        }
    )
    result = calculate(
        request, data_for(request, request.leg1, count=1), data_for(request, request.leg2, count=0)
    )
    assert result.funding_pnl.coverage.leg1 == 1.0
    assert result.funding_pnl.coverage.leg2 == 0.0
    assert len(result.funding_pnl.settlements) == 1


async def test_route_uses_active_fee_settings(route_app):
    app, service = route_app
    fees = FeeSettings(spot_fee_pct=0.2, future_fee_pct=0.08)
    app.state.market_collector = SimpleNamespace(fee_settings=fees)
    assert (await post(app, request_for().model_dump(mode="json"))).status_code == 200
    assert service.query_range_backtest.call_args.kwargs["fee_settings"] is fees


async def test_loader_preserves_explicit_dex_for_all_sources():
    request = request_for()
    request.leg1 = request.leg1.model_validate({"exchange": "hyperliquid", "symbol": "xyz:BTC"})
    request.leg2 = request.leg1.model_copy(update={"dex": "main"})
    service = PairSpreadQueryService()
    observed = []

    async def klines(symbol, start, end, interval, *, dex):
        observed.append(("price", dex))
        return [PairSpreadKlinePoint(bucket_at=start + (end - start) / 2, close=100)]

    async def funding(symbol, start, end, *, dex):
        observed.append(("funding", dex))
        return []

    async def current(symbol, *, dex):
        observed.append(("book", dex))
        return book(request.leg1.model_copy(update={"dex": dex}))

    service._fetch_hyperliquid_klines = klines
    service._fetch_hyperliquid_funding = funding
    service._fetch_hyperliquid_current = current
    try:
        result = await service.query_range_backtest(request, fee_settings=FeeSettings())
    finally:
        await service.aclose()
    assert result.entry.leg1.market.dex == "xyz"
    assert result.entry.leg2.market.dex == "main"
    for dex in ("xyz", "main"):
        assert observed.count(("price", dex)) == 2
        assert observed.count(("funding", dex)) == 1
        assert observed.count(("book", dex)) == 1


@pytest.mark.parametrize("kind", ["entry", "exit"])
@pytest.mark.parametrize("granularity", [60, 3600])
@pytest.mark.parametrize("offset", [0, 10, -10])
def test_endpoint_uses_latest_close_not_after_target(kind, granularity, offset):
    request = request_for()
    target = request.start_at if kind == "entry" else request.end_at
    close_at = target - timedelta(seconds=granularity + offset)
    points = (
        PairSpreadKlinePoint(bucket_at=target, close=999),
        PairSpreadKlinePoint(bucket_at=close_at - timedelta(seconds=granularity), close=101),
        PairSpreadKlinePoint(bucket_at=close_at - timedelta(seconds=2 * granularity), close=98),
    )
    one = replace(data_for(request, request.leg1), granularity_seconds=granularity,
                  **{f"{kind}_points": points})
    result = calculate(request, one, data_for(request, request.leg2))
    endpoint = getattr(result, kind).leg1
    assert endpoint.price == 101
    assert endpoint.timestamp == close_at
    assert endpoint.deviation_seconds == granularity + offset
    assert not any(f"leg1 {kind}" in w and "之后" in w for w in result.warnings)


@pytest.mark.parametrize("kind", ["entry", "exit"])
@pytest.mark.parametrize("offset", [0, 30, 3600])
def test_endpoint_exact_close_or_future_fallback_warns(kind, offset):
    request = request_for()
    target = request.start_at if kind == "entry" else request.end_at
    close_at = target + timedelta(seconds=offset)
    points = (
        PairSpreadKlinePoint(bucket_at=target + timedelta(hours=1), close=999),
        PairSpreadKlinePoint(bucket_at=close_at - timedelta(hours=1), close=101),
    )
    one = replace(data_for(request, request.leg1), granularity_seconds=3600,
                  **{f"{kind}_points": points})
    result = calculate(request, one, data_for(request, request.leg2))
    endpoint = getattr(result, kind).leg1
    assert endpoint.price == 101
    assert endpoint.timestamp == close_at
    assert endpoint.deviation_seconds == offset
    assert any(f"leg1 {kind}" in w and "之后" in w for w in result.warnings) == (offset > 0)


@pytest.mark.parametrize("current_available", [True, False])
@pytest.mark.parametrize("hours,expected_interval,coverage", [
    ([4, 8], 4, 2 / 6),
    ([4, 8, 16], 6, 3 / 4),
    ([4, 8, 12, 24], 4, 4 / 6),
])
def test_unknown_funding_period_uses_median_of_unique_in_range_settlements(
    current_available, hours, expected_interval, coverage,
):
    request = request_for()
    request.leg1 = request.leg1.model_copy(update={"exchange": "bitget"})
    one = data_for(request, request.leg1)
    seed = one.funding[0]
    one = replace(
        one,
        current=book(request.leg1, funding_interval_hours=None) if current_available else None,
        funding=tuple(seed.model_copy(update={"funding_time": START + timedelta(hours=h)})
                      for h in [*reversed(hours), hours[0], 0, 25]),
    )
    result = calculate(request, one, data_for(request, request.leg2))
    assert result.funding_pnl.coverage.leg1 == pytest.approx(coverage)
    assert result.data_quality.funding_coverage == result.funding_pnl.coverage
    assert any("leg1" in w and "推断" in w and f"{expected_interval:g} 小时" in w
               for w in result.warnings)


@pytest.mark.parametrize("count", [0, 1])
def test_unknown_funding_period_requires_two_settlements(count):
    request = request_for()
    one = replace(data_for(request, request.leg1, count=count), current=None)
    result = calculate(request, one, data_for(request, request.leg2))
    assert result.funding_pnl.coverage.leg1 is None
    assert result.data_quality.funding_coverage == result.funding_pnl.coverage
    assert any("leg1" in w and "周期未知" in w for w in result.warnings)


def test_known_funding_period_takes_priority_over_observed_gaps():
    request = request_for()
    one = data_for(request, request.leg1)
    one = replace(one, funding=(one.funding[0], one.funding[2]))
    result = calculate(request, one, data_for(request, request.leg2))
    assert result.funding_pnl.coverage.leg1 == pytest.approx(2 / 3)
    assert result.data_quality.funding_coverage == result.funding_pnl.coverage
    assert not any("推断" in w for w in result.warnings)


@pytest.mark.parametrize("start,end", [
    ("9999-12-31T22:00:00Z", "9999-12-31T23:59:00Z"),
    ("9999-12-01T23:00:00Z", "9999-12-31T23:00:00Z"),
    ("0001-01-01T00:00:00Z", "0001-01-02T00:00:00Z"),
    ("9999-12-31T22:00:00-02:00", "9999-12-31T23:00:00-02:00"),
])
async def test_endpoint_window_overflow_returns_422_before_network(route_app, start, end):
    app, _ = route_app
    service = PairSpreadQueryService()
    service._resolved_result_leg = AsyncMock()
    app.state.pair_spread_query_service_factory = lambda: service
    response = await post(app, request_for().model_dump(mode="json") | {
        "start_at": start, "end_at": end,
    })
    assert response.status_code == 422, response.text
    service._resolved_result_leg.assert_not_awaited()
    assert service.client.is_closed
