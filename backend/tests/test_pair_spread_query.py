from datetime import UTC, datetime, timedelta

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
            if exchange == "binance":
                return [kline(0, 100), kline(1, 101), kline(2, 102)]
            return [kline(0, 1010), kline(1, 1030), kline(2, 1050)]

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


def test_pair_spread_rejects_htx() -> None:
    with pytest.raises(ValidationError):
        PairSpreadLegQuery(exchange="htx", symbol="BTCUSDT")


def test_pair_spread_symbol_normalization() -> None:
    assert PairSpreadLegQuery(exchange="okx", symbol="btc-usdt-swap").symbol == "BTCUSDT"
    assert PairSpreadLegQuery(exchange="gate", symbol="eth").symbol == "ETHUSDT"
