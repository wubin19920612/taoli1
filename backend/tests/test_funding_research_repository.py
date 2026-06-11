from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.models.market import MarketSnapshot, MarketType
from app.services.funding_research.engine import build_funding_research_candidates
from app.services.funding_research.repository import FundingResearchRepository


def market(exchange: str, *, funding: float, mark: float, index: float) -> MarketSnapshot:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    return MarketSnapshot(
        symbol="LABUSDT",
        base="LAB",
        exchange=exchange,
        market_type=MarketType.FUTURE,
        bid=mark - 0.01,
        ask=mark + 0.01,
        bid_size=1_000,
        ask_size=1_000,
        volume_24h_usdt=500_000_000,
        funding_rate_pct=funding,
        funding_next_rate_pct=funding,
        funding_interval_hours=2,
        funding_next_time=now + timedelta(minutes=45),
        mark_price=mark,
        index_price=index,
        timestamp=now,
        raw_symbol="LABUSDT",
    )


@pytest.mark.asyncio
async def test_funding_research_repository_roundtrip() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = FundingResearchRepository(db)
        binance = market("binance", funding=-0.95, mark=9.0, index=9.4)
        okx = market("okx", funding=-0.53, mark=9.3, index=9.5)
        candidates = build_funding_research_candidates([binance, okx])

        market_count = await repo.create_market_snapshots([binance, okx])
        candidate_count = await repo.create_candidate_snapshots(
            candidates,
            observed_at=datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
        )
        loaded = await repo.list_recent_candidates(symbol="LABUSDT")

        assert market_count == 2
        assert candidate_count == 1
        assert len(loaded) == 1
        assert loaded[0].symbol == "LABUSDT"
        assert loaded[0].long_exchange == "binance"
        assert loaded[0].short_exchange == "okx"
    finally:
        await db.close()
