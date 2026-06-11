from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.services.funding_research.models import FundingResearchSettings
from app.services.funding_research.repository import FundingResearchRepository
from app.services.funding_research.runner import record_funding_research_run


def future(exchange: str, funding: float, mark: float, index: float) -> MarketSnapshot:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
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
        funding_next_time=now + timedelta(minutes=30),
        mark_price=mark,
        index_price=index,
        timestamp=now,
        raw_symbol="LABUSDT",
    )


class FakeDepthAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot:
        now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
        return OrderBookSnapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=raw_symbol,
            bids=[
                OrderBookLevel(price=9.39 if self.name == "okx" else 8.79, size=1_000)
                for _ in range(limit)
            ],
            asks=[
                OrderBookLevel(price=9.41 if self.name == "okx" else 8.81, size=1_000)
                for _ in range(limit)
            ],
            timestamp=now,
        )


@pytest.mark.asyncio
async def test_record_funding_research_run_persists_markets_and_candidates() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = FundingResearchRepository(db)

        result = await record_funding_research_run(
            markets=[
                future("binance", -0.95, 9.0, 9.4),
                future("okx", -0.53, 9.3, 9.5),
            ],
            repo=repo,
            observed_at=datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
        )

        loaded = await repo.list_recent_candidates(limit=10)

        assert result.market_snapshot_count == 2
        assert result.candidate_snapshot_count == 1
        assert len(result.candidates) == 1
        assert len(loaded) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_record_funding_research_run_can_manage_paper_trades() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = FundingResearchRepository(db)

        result = await record_funding_research_run(
            markets=[
                future("binance", -1.6, 8.8, 9.5),
                future("okx", -0.1, 9.4, 9.5),
            ],
            repo=repo,
            observed_at=datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
            manage_paper_trades=True,
        )
        loaded_open = await repo.list_paper_trades(status="OPEN")

        assert result.market_snapshot_count == 2
        assert len(result.opened_paper_trades) == 1
        assert len(result.closed_paper_trades) == 0
        assert len(loaded_open) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_record_funding_research_run_enriches_candidates_with_orderbook_depth() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = FundingResearchRepository(db)

        result = await record_funding_research_run(
            markets=[
                future("binance", -1.6, 8.8, 9.5),
                future("okx", -0.1, 9.4, 9.5),
            ],
            repo=repo,
            observed_at=datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
            depth_adapters=[FakeDepthAdapter("binance"), FakeDepthAdapter("okx")],
            orderbook_depth_levels=20,
        )

        assert result.candidates[0].depth_stats is not None
        assert result.candidates[0].depth_stats.source == "orderbook"
        assert result.candidates[0].depth_stats.levels == 20
        assert result.candidates[0].depth_stats.min_entry_depth_usdt is not None
        assert result.candidates[0].depth_stats.min_entry_depth_usdt > 1_000
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_record_funding_research_run_prunes_expired_snapshots() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = FundingResearchRepository(db)
        old_time = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
        new_time = old_time + timedelta(hours=4)

        await record_funding_research_run(
            markets=[
                future("binance", -0.95, 9.0, 9.4),
                future("okx", -0.53, 9.3, 9.5),
            ],
            repo=repo,
            observed_at=old_time,
            settings=FundingResearchSettings(snapshot_retention_hours=0),
        )

        result = await record_funding_research_run(
            markets=[
                future("binance", -0.95, 9.0, 9.4).model_copy(update={"timestamp": new_time}),
                future("okx", -0.53, 9.3, 9.5).model_copy(update={"timestamp": new_time}),
            ],
            repo=repo,
            observed_at=new_time,
            settings=FundingResearchSettings(snapshot_retention_hours=1),
        )
        market_rows = await db.execute_fetchall("SELECT * FROM funding_research_market_snapshots")
        candidate_rows = await db.execute_fetchall("SELECT * FROM funding_research_opportunity_snapshots")

        assert result.pruned_snapshot_count == 3
        assert len(market_rows) == 2
        assert len(candidate_rows) == 1
    finally:
        await db.close()
