from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadFundingPoint,
    PairSpreadKlinePoint,
    PairSpreadLegQuery,
    PairSpreadPriceField,
)
from app.services.pair_spread_query import (
    PairSpreadQueryError,
    PairSpreadQueryService,
    _hyperliquid_history_limit_warning,
    build_pair_spread_points,
)


def kline(minutes: int, close: float) -> PairSpreadKlinePoint:
    return PairSpreadKlinePoint(
        bucket_at=datetime(2026, 7, 10, 12, minutes, tzinfo=UTC),
        close=close,
    )


def kline_at(bucket_at: datetime, close: float) -> PairSpreadKlinePoint:
    return PairSpreadKlinePoint(bucket_at=bucket_at, close=close)


def current_leg(exchange: str, symbol: str, price: float) -> PairSpreadCurrentLeg:
    return PairSpreadCurrentLeg(
        exchange=exchange,
        symbol=symbol,
        raw_symbol=symbol,
        price=price,
        price_field=PairSpreadPriceField.MARK_PRICE,
        mark_price=price,
        funding_rate_pct=0.01,
        funding_next_time=datetime(2026, 7, 10, 16, 0, tzinfo=UTC),
        timestamp=datetime(2026, 7, 10, 12, 2, tzinfo=UTC),
    )


def test_pair_spread_points_align_by_minute() -> None:
    points = build_pair_spread_points(
        [kline(0, 100), kline(1, 101), kline(2, 102)],
        [kline(1, 103), kline(2, 105), kline(3, 106)],
    )

    assert [point.bucket_at.minute for point in points] == [1, 2]
    assert points[0].spread_abs == 2
    assert points[0].spread_pct == pytest.approx((103 - 101) / ((101 + 103) / 2) * 100)
    assert points[1].spread_abs == 3


def test_pair_spread_points_apply_right_side_multiplier() -> None:
    points = build_pair_spread_points(
        [kline(0, 100)],
        [kline(0, 1050)],
        leg2_multiplier=10,
    )

    assert points[0].leg2_close == 105
    assert points[0].spread_abs == 5
    assert points[0].spread_pct == pytest.approx(5 / ((100 + 105) / 2) * 100)


def test_hyperliquid_history_limit_warning_recommends_15_minutes_for_30_days() -> None:
    warning = _hyperliquid_history_limit_warning(
        {"hyperliquid"},
        hours=720,
        interval_minutes=5,
    )

    assert warning is not None
    assert "最近5000根K线" in warning
    assert "需要约8640根" in warning
    assert "最多约17.4天" in warning
    assert "切换到15分钟可覆盖30天" in warning


def test_hyperliquid_history_limit_warning_is_not_needed_for_15_minutes() -> None:
    assert (
        _hyperliquid_history_limit_warning(
            {"hyperliquid"},
            hours=720,
            interval_minutes=15,
        )
        is None
    )


@pytest.mark.asyncio
async def test_pair_spread_query_builds_stats_current_and_funding() -> None:
    class FakePairSpreadService(PairSpreadQueryService):
        async def _fetch_klines(self, exchange: str, symbol: str, start, end, interval_minutes: int):
            assert interval_minutes == 5
            first_bucket = start + timedelta(minutes=5)
            if exchange == "binance":
                return [
                    kline_at(first_bucket, 100),
                    kline_at(first_bucket + timedelta(minutes=5), 101),
                    kline_at(first_bucket + timedelta(minutes=10), 102),
                ]
            return [
                kline_at(first_bucket, 1010),
                kline_at(first_bucket + timedelta(minutes=5), 1030),
                kline_at(first_bucket + timedelta(minutes=10), 1050),
            ]

        async def _fetch_current_leg(self, exchange: str, symbol: str):
            return current_leg(exchange, symbol, 100 if exchange == "binance" else 1040)

        async def _fetch_funding_history(self, exchange: str, symbol: str, start, end):
            return [
                PairSpreadFundingPoint(
                    exchange=exchange,
                    symbol=symbol,
                    funding_time=datetime(2026, 7, 10, 8, 0, tzinfo=UTC),
                    funding_rate_pct=0.01 if exchange == "binance" else 0.02,
                )
            ]

    service = FakePairSpreadService()
    try:
        result = await service.query(
            PairSpreadLegQuery(exchange="binance", symbol="btc"),
            PairSpreadLegQuery(exchange="okx", symbol="BTC-USDT-SWAP"),
            hours=24,
            interval_minutes=5,
            leg2_multiplier=10,
            now=datetime(2026, 7, 10, 12, 2, 30, tzinfo=UTC),
        )
    finally:
        await service.aclose()

    assert result.leg1.symbol == "BTCUSDT"
    assert result.leg2.symbol == "BTCUSDT"
    assert result.point_count == 3
    assert result.spread_abs.current == 3
    assert result.spread_pct.current == pytest.approx((105 - 102) / ((102 + 105) / 2) * 100)
    assert result.current is not None
    assert result.current.leg2.price == 104
    assert result.current.spread_pct == pytest.approx((104 - 100) / ((100 + 104) / 2) * 100)
    assert result.interval_minutes == 5
    assert result.leg2_multiplier == 10
    assert len(result.funding_history) == 2
    assert result.warnings == []


@pytest.mark.asyncio
async def test_pair_spread_query_falls_back_to_available_window() -> None:
    now = datetime(2026, 7, 10, 12, 30, tzinfo=UTC)
    point_time = now - timedelta(hours=2)

    class FakePairSpreadService(PairSpreadQueryService):
        def __init__(self) -> None:
            super().__init__()
            self.window_hours: list[int] = []

        async def _fetch_klines(self, exchange: str, symbol: str, start, end, interval_minutes: int):
            self.window_hours.append(round((end - start).total_seconds() / 3600))
            if start < end - timedelta(hours=168):
                return []
            close = 100 if exchange == "binance" else 103
            return [kline_at(point_time, close)]

        async def _fetch_current_leg(self, exchange: str, symbol: str):
            return current_leg(exchange, symbol, 100 if exchange == "binance" else 103)

        async def _fetch_funding_history(self, exchange: str, symbol: str, start, end):
            return []

    service = FakePairSpreadService()
    try:
        result = await service.query(
            PairSpreadLegQuery(exchange="binance", symbol="btc"),
            PairSpreadLegQuery(exchange="okx", symbol="btc"),
            hours=720,
            interval_minutes=5,
            now=now,
        )
    finally:
        await service.aclose()

    assert 720 in service.window_hours
    assert 168 in service.window_hours
    assert result.hours == 720
    assert result.point_count == 1
    assert result.first_seen_at == point_time
    assert any("自动改查最近7天" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_pair_spread_query_dedupes_repeated_kline_failures() -> None:
    class FakePairSpreadService(PairSpreadQueryService):
        async def _fetch_klines(self, exchange: str, symbol: str, start, end, interval_minutes: int):
            raise RuntimeError("Hyperliquid 接口返回 HTTP 500，可能是该合约未上线、名称不匹配，或接口临时异常")

    service = FakePairSpreadService()
    try:
        with pytest.raises(PairSpreadQueryError) as exc_info:
            await service.query(
                PairSpreadLegQuery(exchange="hyperliquid", symbol="skhy"),
                PairSpreadLegQuery(exchange="hyperliquid", symbol="skhynix"),
                hours=24,
                interval_minutes=1,
                now=datetime(2026, 7, 10, 12, 30, tzinfo=UTC),
            )
    finally:
        await service.aclose()

    message = str(exc_info.value)
    assert message.count("hyperliquid:SKHYUSDT 分钟K线失败") == 1
    assert message.count("hyperliquid:SKHYNIXUSDT 分钟K线失败") == 1
    assert "developer.mozilla.org" not in message


@pytest.mark.asyncio
async def test_hyperliquid_klines_resolve_prefixed_hip3_coin() -> None:
    start = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=2)
    bodies: list[dict[str, Any]] = []

    service = PairSpreadQueryService()

    async def fake_post_json(url: str, body: dict[str, Any]):
        bodies.append(body)
        if body.get("type") == "perpDexs":
            return [None, {"name": "xyz"}]
        if body.get("type") == "metaAndAssetCtxs":
            if body.get("dex") == "xyz":
                return [
                    {"universe": [{"name": "xyz:SKHY"}]},
                    [{"markPx": "10"}],
                ]
            return [
                {"universe": [{"name": "BTC"}]},
                [{"markPx": "60000"}],
            ]
        if body.get("type") == "candleSnapshot":
            assert body["req"]["coin"] == "xyz:SKHY"
            return [{"t": int(start.timestamp() * 1000), "c": "10"}]
        raise AssertionError(f"unexpected body: {body}")

    service._post_json = fake_post_json  # type: ignore[method-assign]
    try:
        points = await service._fetch_hyperliquid_klines("SKHYUSDT", start, end, 1)
    finally:
        await service.aclose()

    assert points == [PairSpreadKlinePoint(bucket_at=start, close=10)]
    assert [body.get("type") for body in bodies] == [
        "perpDexs",
        "metaAndAssetCtxs",
        "metaAndAssetCtxs",
        "candleSnapshot",
    ]


@pytest.mark.asyncio
async def test_bitget_klines_continue_after_empty_early_chunk() -> None:
    start = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    end = start + timedelta(minutes=1001)
    first_data_bucket = start + timedelta(minutes=1000)
    requested_starts: list[int] = []

    service = PairSpreadQueryService()

    async def fake_get_json(url: str):
        query = parse_qs(urlparse(url).query)
        start_ms = int(query["startTime"][0])
        requested_starts.append(start_ms)
        if len(requested_starts) == 1:
            return {"data": []}
        return {"data": [[int(first_data_bucket.timestamp() * 1000), "0", "0", "0", "10"]]}

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        points = await service._fetch_bitget_klines("BTCUSDT", start, end, 1)
    finally:
        await service.aclose()

    assert len(requested_starts) == 2
    assert points == [PairSpreadKlinePoint(bucket_at=first_data_bucket, close=10)]


@pytest.mark.asyncio
async def test_bybit_current_uses_instruments_info_for_funding_interval_and_limits() -> None:
    service = PairSpreadQueryService()
    requested_urls: list[str] = []

    async def fake_get_json(url: str):
        requested_urls.append(url)
        if "market/tickers" in url:
            return {
                "result": {
                    "list": [
                        {
                            "symbol": "HOMEUSDT",
                            "markPrice": "0.009126",
                            "indexPrice": "0.009291",
                            "bid1Price": "0.00910",
                            "ask1Price": "0.009115",
                            "lastPrice": "0.00912",
                            "fundingRate": "-0.019844",
                            "nextFundingTime": "1784256000000",
                        }
                    ]
                }
            }
        if "market/instruments-info" in url:
            return {
                "result": {
                    "list": [
                        {
                            "symbol": "HOMEUSDT",
                            "fundingInterval": "240",
                            "upperFundingRate": "0.020000",
                            "lowerFundingRate": "-0.020000",
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected url: {url}")

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        leg = await service._fetch_bybit_current("HOMEUSDT")
    finally:
        await service.aclose()

    assert any("market/tickers" in url for url in requested_urls)
    assert any("market/instruments-info" in url for url in requested_urls)
    assert leg.mid_price == pytest.approx((0.00910 + 0.009115) / 2)
    assert leg.funding_rate_pct == pytest.approx(-1.9844)
    assert leg.funding_interval_hours == pytest.approx(4)
    assert leg.funding_rate_upper_pct == pytest.approx(2)
    assert leg.funding_rate_lower_pct == pytest.approx(-2)


@pytest.mark.asyncio
async def test_okx_current_uses_funding_interval_and_limits() -> None:
    funding_time = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    next_funding_time = funding_time + timedelta(hours=4)
    service = PairSpreadQueryService()
    requested_urls: list[str] = []

    async def fake_get_json(url: str):
        requested_urls.append(url)
        if "market/ticker" in url:
            return {
                "data": [
                    {
                        "bidPx": "100.4",
                        "askPx": "100.6",
                        "last": "100.5",
                    }
                ]
            }
        if "funding-rate" in url:
            return {
                "data": [
                    {
                        "fundingRate": "-0.010000",
                        "nextFundingRate": "-0.005000",
                        "fundingTime": str(int(funding_time.timestamp() * 1000)),
                        "nextFundingTime": str(int(next_funding_time.timestamp() * 1000)),
                        "minFundingRate": "-0.010000",
                        "maxFundingRate": "0.010000",
                    }
                ]
            }
        raise AssertionError(f"unexpected url: {url}")

    service._get_json = fake_get_json  # type: ignore[method-assign]
    try:
        leg = await service._fetch_okx_current("OUSDT")
    finally:
        await service.aclose()

    assert any("market/ticker?instId=O-USDT-SWAP" in url for url in requested_urls)
    assert any("funding-rate?instId=O-USDT-SWAP" in url for url in requested_urls)
    assert leg.raw_symbol == "O-USDT-SWAP"
    assert leg.price == pytest.approx(100.5)
    assert leg.funding_rate_pct == pytest.approx(-1.0)
    assert leg.funding_next_rate_pct == pytest.approx(-0.5)
    assert leg.funding_next_time == next_funding_time
    assert leg.funding_interval_hours == pytest.approx(4)
    assert leg.funding_rate_lower_pct == pytest.approx(-1.0)
    assert leg.funding_rate_upper_pct == pytest.approx(1.0)


def test_pair_spread_rejects_htx() -> None:
    with pytest.raises(ValidationError):
        PairSpreadLegQuery(exchange="htx", symbol="BTCUSDT")


def test_pair_spread_symbol_normalization() -> None:
    assert PairSpreadLegQuery(exchange="okx", symbol="btc-usdt-swap").symbol == "BTCUSDT"
    assert PairSpreadLegQuery(exchange="gate", symbol="eth").symbol == "ETHUSDT"
