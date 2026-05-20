from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import connect_database
from app.db.repositories import OpportunityHistoryRepository
from app.db.schema import initialize_schema
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import HistorySettings
from app.services.history import OpportunityHistoryRecorder


def opportunity(
    symbol: str,
    open_spread_pct: float,
    volume: float | None = 1_000_000,
    net_funding_pct: float | None = 0.02,
) -> Opportunity:
    return Opportunity(
        id=f"{symbol}:binance:okx",
        type=OpportunityType.FF,
        symbol=symbol,
        buy_exchange="binance",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=open_spread_pct,
        close_spread_pct=open_spread_pct - 0.1,
        fee_adjusted_open_pct=open_spread_pct - 0.2,
        spread_width_pct=0.1,
        buy_bid=99,
        buy_ask=100,
        sell_bid=101,
        sell_ask=102,
        buy_volume_24h_usdt=volume,
        sell_volume_24h_usdt=volume,
        funding_rate_buy_pct=0.01,
        funding_rate_sell_pct=0.03 if net_funding_pct is not None else None,
        net_funding_pct=net_funding_pct,
        mark_index_diff_buy_pct=None,
        mark_index_diff_sell_pct=None,
        risk_labels=["FUNDING_AGAINST"],
        last_seen_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_history_repository_roundtrip_and_prunes_old_rows() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = OpportunityHistoryRepository(db)
        old_time = datetime(2026, 5, 18, 0, 0, tzinfo=UTC)
        new_time = old_time + timedelta(days=2)

        await repo.insert_many(
            [
                repo.row_from_opportunity(opportunity("OLDUSDT", 1.2), old_time),
                repo.row_from_opportunity(opportunity("NEWUSDT", 0.8), new_time),
            ]
        )
        deleted = await repo.prune_before(old_time + timedelta(days=1))
        rows = await repo.list(symbol="NEWUSDT")

        assert deleted == 1
        assert len(rows) == 1
        assert rows[0].symbol == "NEWUSDT"
        assert rows[0].open_spread_pct == 0.8
        assert rows[0].risk_labels == ["FUNDING_AGAINST"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_history_recorder_caps_filters_samples_and_prunes() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = OpportunityHistoryRepository(db)
        settings = HistorySettings(
            enabled=True,
            sample_seconds=120,
            retention_days=1,
            keep_top_n=2,
            min_open_spread_pct=0.5,
            min_volume_24h_k=100,
        )
        recorder = OpportunityHistoryRecorder(repo, settings)
        now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)

        await repo.insert_many([
            repo.row_from_opportunity(opportunity("STALEUSDT", 5.0), now - timedelta(days=2))
        ])
        await recorder.record(
            [
                opportunity("LOWSPREADUSDT", 0.2, volume=10_000_000),
                opportunity("NOVOLUMEUSDT", 2.0, volume=None),
                opportunity("THIRDUSDT", 0.9, volume=2_000_000),
                opportunity("FIRSTUSDT", 1.5, volume=2_000_000),
                opportunity("SECONDUSDT", 1.0, volume=2_000_000),
            ],
            now=now,
        )
        await recorder.record([opportunity("SKIPPEDUSDT", 9.0, volume=10_000_000)], now=now + timedelta(seconds=30))

        rows = await repo.list(limit=10)

        assert [row.symbol for row in rows] == ["FIRSTUSDT", "SECONDUSDT"]
        assert all(row.observed_at == now for row in rows)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_history_recorder_can_be_disabled() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = OpportunityHistoryRepository(db)
        recorder = OpportunityHistoryRecorder(repo, HistorySettings(enabled=False))

        await recorder.record([opportunity("BTCUSDT", 1.0)], now=datetime.now(UTC))

        assert await repo.list(limit=10) == []
    finally:
        await db.close()
