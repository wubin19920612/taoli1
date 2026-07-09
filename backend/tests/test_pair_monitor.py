from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import connect_database
from app.db.pair_monitor_repository import PairMonitorRepository
from app.db.schema import initialize_schema
from app.models.market import MarketSnapshot, MarketType
from app.models.pair_monitor import (
    PairMonitorLeg,
    PairMonitorPriceField,
    PairMonitorRule,
    PairMonitorSampleStatus,
)
from app.services.pair_monitor import PairMonitorSampler, build_pair_monitor_point


def market(
    symbol: str,
    exchange: str,
    bid: float,
    ask: float,
    *,
    mark_price: float | None = None,
    funding_rate_pct: float | None = None,
    timestamp: datetime | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        base=symbol.removesuffix("USDT"),
        quote="USDT",
        exchange=exchange,
        market_type=MarketType.FUTURE,
        bid=bid,
        ask=ask,
        volume_24h_usdt=1_000_000,
        funding_rate_pct=funding_rate_pct,
        funding_next_rate_pct=None,
        funding_interval_hours=8,
        funding_next_time=None,
        mark_price=mark_price,
        index_price=None,
        timestamp=timestamp or datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
        raw_symbol=symbol,
    )


def rule() -> PairMonitorRule:
    return PairMonitorRule(
        id="pair-1",
        name="BTC pair",
        leg1=PairMonitorLeg(exchange="binance", symbol="btcusdt"),
        leg2=PairMonitorLeg(
            exchange="okx",
            symbol="BTC-USDT-SWAP",
            price_field=PairMonitorPriceField.MID_PRICE,
        ),
        sample_interval_seconds=60,
        retention_days=1,
    )


def test_pair_monitor_builds_minute_point_from_markets() -> None:
    monitor_rule = rule()
    result = build_pair_monitor_point(
        monitor_rule,
        [
            market("BTCUSDT", "binance", 99, 101, mark_price=100, funding_rate_pct=0.01),
            market("BTCUSDT", "okx", 100, 104, mark_price=105, funding_rate_pct=0.03),
        ],
        now=datetime(2026, 7, 9, 12, 34, 56, tzinfo=UTC),
    )

    assert result.status == PairMonitorSampleStatus.RECORDED
    assert result.point is not None
    assert result.point.bucket_at == datetime(2026, 7, 9, 12, 34, tzinfo=UTC)
    assert result.point.leg1_price == 100
    assert result.point.leg1_price_field == PairMonitorPriceField.MARK_PRICE
    assert result.point.leg2_price == 102
    assert result.point.leg2_price_field == PairMonitorPriceField.MID_PRICE
    assert result.point.spread_abs == 2
    assert result.point.spread_pct == 2
    assert result.point.leg1_funding_rate_pct == 0.01
    assert result.point.leg2_funding_rate_pct == 0.03


def test_pair_monitor_reports_missing_market() -> None:
    result = build_pair_monitor_point(rule(), [], now=datetime(2026, 7, 9, 12, 0, tzinfo=UTC))

    assert result.status == PairMonitorSampleStatus.SKIPPED
    assert result.point is None
    assert "market not found" in (result.reason or "")


@pytest.mark.asyncio
async def test_pair_monitor_repository_and_sampler_roundtrip() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = PairMonitorRepository(db)
        sampler = PairMonitorSampler(repo)
        monitor_rule = await repo.create_rule(rule())
        now = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)

        first = await sampler.sample(
            [
                market("BTCUSDT", "binance", 99, 101, mark_price=100),
                market("BTCUSDT", "okx", 101, 103),
            ],
            now=now,
        )
        duplicate = await sampler.sample(
            [
                market("BTCUSDT", "binance", 99, 101, mark_price=100),
                market("BTCUSDT", "okx", 105, 107),
            ],
            now=now + timedelta(seconds=30),
        )
        second = await sampler.sample(
            [
                market("BTCUSDT", "binance", 100, 102, mark_price=101),
                market("BTCUSDT", "okx", 102, 104),
            ],
            now=now + timedelta(minutes=1),
        )

        points = await repo.list_points(monitor_rule.id)

        assert first[0].status == PairMonitorSampleStatus.RECORDED
        assert duplicate[0].status == PairMonitorSampleStatus.SKIPPED
        assert second[0].status == PairMonitorSampleStatus.RECORDED
        assert len(points) == 2
        assert points[-1].spread_pct == pytest.approx((103 - 101) / 101 * 100)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pair_monitor_sampler_prunes_retention_window() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = PairMonitorRepository(db)
        sampler = PairMonitorSampler(repo)
        monitor_rule = await repo.create_rule(rule())
        old_time = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
        new_time = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)

        await sampler.sample(
            [
                market("BTCUSDT", "binance", 99, 101, mark_price=100),
                market("BTCUSDT", "okx", 101, 103),
            ],
            now=old_time,
        )
        await sampler.sample(
            [
                market("BTCUSDT", "binance", 100, 102, mark_price=101),
                market("BTCUSDT", "okx", 102, 104),
            ],
            now=new_time,
        )

        points = await repo.list_points(monitor_rule.id)

        assert len(points) == 1
        assert points[0].bucket_at == new_time
    finally:
        await db.close()
