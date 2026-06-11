from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.models.market import MarketSnapshot, MarketType
from app.services.funding_research.backtest import (
    FundingResearchReplayBatch,
    replay_funding_research_batches,
)
from app.services.funding_research.repository import FundingResearchRepository


def future(
    exchange: str,
    funding: float,
    mark: float,
    index: float,
    *,
    observed_at: datetime,
    next_time: datetime,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="LABUSDT",
        base="LAB",
        exchange=exchange,
        market_type=MarketType.FUTURE,
        bid=mark - 0.01,
        ask=mark + 0.01,
        bid_size=2_000,
        ask_size=2_000,
        volume_24h_usdt=500_000_000,
        funding_rate_pct=funding,
        funding_next_rate_pct=funding,
        funding_interval_hours=2,
        funding_next_time=next_time,
        mark_price=mark,
        index_price=index,
        timestamp=observed_at,
        raw_symbol="LABUSDT",
    )


@pytest.mark.asyncio
async def test_replay_batches_opens_and_closes_paper_trade() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = FundingResearchRepository(db)
        t0 = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
        settlement = t0 + timedelta(minutes=45)
        batches = [
            FundingResearchReplayBatch(
                observed_at=t0,
                markets=[
                    future("binance", -1.6, 8.8, 9.5, observed_at=t0, next_time=settlement),
                    future("okx", -0.1, 9.4, 9.5, observed_at=t0, next_time=settlement),
                ],
            ),
            FundingResearchReplayBatch(
                observed_at=settlement,
                markets=[
                    future("binance", -0.2, 9.1, 9.5, observed_at=settlement, next_time=settlement),
                    future("okx", -0.1, 9.3, 9.5, observed_at=settlement, next_time=settlement),
                ],
            ),
        ]

        result = await replay_funding_research_batches(batches=batches, repo=repo)

        assert len(result.runs) == 2
        assert result.summary.total_trades >= 1
        assert result.summary.closed_trades >= 1
        assert result.summary.average_realized_pnl_pct is not None
    finally:
        await db.close()
