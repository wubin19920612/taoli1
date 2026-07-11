from datetime import UTC, datetime, timedelta

import pytest

from app.models.premium_index import PremiumIndexCurrentSnapshot, PremiumIndexMarketQuery, PremiumIndexPoint
from app.services.premium_index_query import PremiumIndexQueryService, build_premium_points_from_mark_index


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
