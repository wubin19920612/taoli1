from datetime import UTC, datetime, timedelta

import pytest

from app.models.pair_spread import PairSpreadCurrentLeg, PairSpreadKlinePoint, PairSpreadPriceField
from app.models.premium_index import PremiumIndexCurrentSnapshot, PremiumIndexMarketQuery, PremiumIndexPoint
from app.services.premium_index_query import (
    PremiumIndexQueryService,
    build_hyperliquid_candle_premium_points,
    build_premium_points_from_mark_index,
)


def test_build_premium_points_from_mark_index_aligns_by_time() -> None:
    first = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    points = build_premium_points_from_mark_index(
        [(first, 101), (first + timedelta(minutes=1), 102)],
        [(first, 100), (first + timedelta(minutes=2), 103)],
    )

    assert len(points) == 1
    assert points[0].bucket_at == first
    assert points[0].premium_pct == pytest.approx(1.0)
    assert points[0].mark_price == 101
    assert points[0].index_price == 100


def test_hyperliquid_candle_premium_points_use_minute_candles() -> None:
    first = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    candles = [
        PairSpreadKlinePoint(bucket_at=first + timedelta(minutes=index), close=100.0)
        for index in range(61)
    ]
    candles[30] = PairSpreadKlinePoint(bucket_at=first + timedelta(minutes=30), close=101.0)

    points = build_hyperliquid_candle_premium_points(
        candles,
        [
            PremiumIndexPoint(bucket_at=first, premium_pct=0.0, source="hyperliquid_funding_premium"),
            PremiumIndexPoint(
                bucket_at=first + timedelta(hours=1),
                premium_pct=0.0,
                source="hyperliquid_funding_premium",
            ),
        ],
        interval_minutes=1,
    )

    assert len(points) == 61
    assert points[1].bucket_at == first + timedelta(minutes=1)
    assert points[1].premium_pct == pytest.approx(0.0)
    assert points[30].premium_pct == pytest.approx(1.0)
    assert points[30].mark_price == pytest.approx(101.0)
    assert points[30].index_price == pytest.approx(100.0)
    assert points[30].source == "hyperliquid_candle_funding_anchor"


def test_hyperliquid_candle_premium_points_require_two_anchors() -> None:
    first = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)

    assert (
        build_hyperliquid_candle_premium_points(
            [PairSpreadKlinePoint(bucket_at=first, close=100.0)],
            [PremiumIndexPoint(bucket_at=first, premium_pct=0.0, source="hyperliquid_funding_premium")],
            interval_minutes=1,
        )
        == []
    )


@pytest.mark.asyncio
async def test_bybit_current_uses_official_premium_index_over_mark_deviation() -> None:
    now = datetime(2026, 7, 17, 10, 40, tzinfo=UTC)

    class FakePremiumIndexService(PremiumIndexQueryService):
        async def _fetch_bybit_current(self, symbol):
            assert symbol == "HOMEUSDT"
            return PairSpreadCurrentLeg(
                exchange="bybit",
                symbol="HOMEUSDT",
                raw_symbol="HOMEUSDT",
                price=0.009126,
                price_field=PairSpreadPriceField.MARK_PRICE,
                mark_price=0.009126,
                index_price=0.009291,
                mid_price=0.0091075,
                last_price=0.00912,
                funding_rate_pct=-1.9844,
                funding_next_time=now + timedelta(hours=2),
                funding_interval_hours=4,
                funding_rate_upper_pct=2,
                funding_rate_lower_pct=-2,
                timestamp=now,
            )

        async def _fetch_bybit_premium(self, symbol, start, end, interval_minutes):
            assert symbol == "HOMEUSDT"
            assert interval_minutes == 1
            return [
                PremiumIndexPoint(
                    bucket_at=now - timedelta(minutes=1),
                    premium_pct=-1.2345,
                    source="bybit_premium_index",
                )
            ]

    service = FakePremiumIndexService()
    try:
        current = await service.current(PremiumIndexMarketQuery(exchange="bybit", symbol="HOMEUSDT"))
    finally:
        await service.aclose()

    mark_deviation = (0.009126 - 0.009291) / 0.009291 * 100
    assert current.source == "bybit_premium_index"
    assert current.premium_pct == pytest.approx(-1.2345)
    assert current.premium_pct != pytest.approx(mark_deviation)
    assert current.mid_premium_pct == pytest.approx((0.0091075 - 0.009291) / 0.009291 * 100)
    assert current.funding_interval_hours == pytest.approx(4)
    assert current.funding_rate_upper_pct == pytest.approx(2)
    assert current.funding_rate_lower_pct == pytest.approx(-2)


@pytest.mark.asyncio
async def test_premium_index_query_builds_stats_and_current() -> None:
    now = datetime(2026, 7, 11, 12, 3, 40, tzinfo=UTC)
    first = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)

    class FakePremiumIndexService(PremiumIndexQueryService):
        async def _fetch_history(self, exchange, symbol, start, end, interval_minutes):
            assert exchange == "binance"
            assert symbol == "BTCUSDT"
            assert interval_minutes == 1
            return [
                PremiumIndexPoint(bucket_at=first, premium_pct=0.1, source="test"),
                PremiumIndexPoint(bucket_at=first + timedelta(minutes=1), premium_pct=0.2, source="test"),
            ]

        async def current(self, market):
            return PremiumIndexCurrentSnapshot(
                observed_at=now,
                exchange=market.exchange,
                symbol=market.symbol,
                raw_symbol=market.symbol,
                mark_price=101,
                index_price=100,
                premium_pct=1.0,
                source="mark_index",
            )

    service = FakePremiumIndexService()
    try:
        result = await service.query(
            PremiumIndexMarketQuery(exchange="binance", symbol="btc"),
            hours=6,
            interval_minutes=1,
            now=now,
        )
    finally:
        await service.aclose()

    assert result.symbol == "BTCUSDT"
    assert result.point_count == 2
    assert result.premium_pct.min == pytest.approx(0.1)
    assert result.premium_pct.max == pytest.approx(0.2)
    assert result.current is not None
    assert result.current.premium_pct == pytest.approx(1.0)
