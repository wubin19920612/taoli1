from datetime import UTC, datetime, timedelta
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
from app.services.pair_spread_query import PairSpreadQueryService, build_pair_spread_points


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
    assert points[0].spread_pct == pytest.approx((103 - 101) / 101 * 100)
    assert points[1].spread_abs == 3


def test_pair_spread_points_apply_right_side_multiplier() -> None:
    points = build_pair_spread_points(
        [kline(0, 100)],
        [kline(0, 1050)],
        leg2_multiplier=10,
    )

    assert points[0].leg2_close == 105
    assert points[0].spread_abs == 5
    assert points[0].spread_pct == 5


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
    assert result.spread_pct.current == pytest.approx((105 - 102) / 102 * 100)
    assert result.current is not None
    assert result.current.leg2.price == 104
    assert result.current.spread_pct == 4
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


def test_pair_spread_rejects_htx() -> None:
    with pytest.raises(ValidationError):
        PairSpreadLegQuery(exchange="htx", symbol="BTCUSDT")


def test_pair_spread_symbol_normalization() -> None:
    assert PairSpreadLegQuery(exchange="okx", symbol="btc-usdt-swap").symbol == "BTCUSDT"
    assert PairSpreadLegQuery(exchange="gate", symbol="eth").symbol == "ETHUSDT"
