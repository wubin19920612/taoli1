import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.db.database import connect_database
from app.db.repositories import OpportunityHistoryRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import HistorySettings, RiskSettings
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


@pytest.mark.parametrize("recover_stale_snapshots", [False, True])
@pytest.mark.parametrize("failure_index", [0, 1, 2])
@pytest.mark.asyncio
async def test_history_batch_failure_preserves_other_writes_without_partial_rows(
    recover_stale_snapshots: bool, failure_index: int,
) -> None:
    db = await connect_database(":memory:", recover_stale_snapshots=recover_stale_snapshots)
    try:
        await initialize_schema(db)
        repo = OpportunityHistoryRepository(db)
        now = datetime(2026, 10, 30, tzinfo=UTC)
        rows = [
            repo.row_from_opportunity(
                opportunity("FAILUSDT" if index == failure_index else f"ROW{index}USDT", 1.0),
                now,
            )
            for index in range(3)
        ]
        await db.execute("""CREATE TEMP TRIGGER fail_history_row
            BEFORE INSERT ON opportunity_history WHEN NEW.symbol = 'FAILUSDT'
            BEGIN SELECT RAISE(ABORT, 'injected history row failure'); END""")
        await db.commit()
        # A rollback here would silently discard another caller's pending write.
        await db.execute(
            "INSERT INTO app_settings (key, payload) VALUES (?, ?)",
            ("pending-history-test", "{}"),
        )

        with pytest.raises(sqlite3.IntegrityError, match="injected history row failure"):
            await repo.insert_many(rows)

        async with db.execute("SELECT COUNT(*) FROM opportunity_history") as cursor:
            assert (await cursor.fetchone())[0] == 0
        assert db.in_transaction

        await SettingsRepository(db).set_risk_settings(RiskSettings())

        assert not db.in_transaction
        async with db.execute("SELECT COUNT(*) FROM opportunity_history") as cursor:
            assert (await cursor.fetchone())[0] == 0
        async with db.execute(
            "SELECT payload FROM app_settings WHERE key = ?", ("pending-history-test",)
        ) as cursor:
            assert (await cursor.fetchone())[0] == "{}"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_history_batch_accepts_and_commits_1000_rows() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = OpportunityHistoryRepository(db)
        now = datetime(2026, 10, 30, tzinfo=UTC)
        rows = [
            repo.row_from_opportunity(opportunity(f"ROW{index}USDT", 1.0), now)
            for index in range(1000)
        ]

        assert await repo.insert_many(rows) == 1000

        assert not db.in_transaction
        stored = await repo.list(limit=1001)
        assert len(stored) == 1000
        assert {row.symbol for row in stored} == {row.symbol for row in rows}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_history_batch_rejects_1001_rows_before_database_access() -> None:
    db = await connect_database(":memory:")
    statements = []
    try:
        await initialize_schema(db)
        repo = OpportunityHistoryRepository(db)
        row = repo.row_from_opportunity(opportunity("BTCUSDT", 1.0), datetime.now(UTC))
        await db.set_trace_callback(statements.append)

        with pytest.raises(ValueError, match="1000"):
            await repo.insert_many([row] * 1001)

        assert statements == []
        assert not db.in_transaction
        async with db.execute("SELECT COUNT(*) FROM opportunity_history") as cursor:
            assert (await cursor.fetchone())[0] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_history_batch_serializes_all_rows_before_database_access() -> None:
    db = await connect_database(":memory:")
    statements = []
    try:
        await initialize_schema(db)
        repo = OpportunityHistoryRepository(db)
        row = repo.row_from_opportunity(opportunity("BTCUSDT", 1.0), datetime.now(UTC))
        invalid_row = row.model_copy(update={"market_identity": {"invalid": object()}})
        await db.set_trace_callback(statements.append)

        with pytest.raises(TypeError, match="not JSON serializable"):
            await repo.insert_many([row, invalid_row])

        assert statements == []
        assert not db.in_transaction
        async with db.execute("SELECT COUNT(*) FROM opportunity_history") as cursor:
            assert (await cursor.fetchone())[0] == 0
    finally:
        await db.close()


def test_history_settings_enforce_1000_row_limit() -> None:
    assert HistorySettings(keep_top_n=1000).keep_top_n == 1000
    with pytest.raises(ValidationError, match="keep_top_n"):
        HistorySettings(keep_top_n=1001)


@pytest.mark.parametrize("keep_top_n", [1000, 1001])
def test_history_environment_settings_enforce_row_limit(monkeypatch, keep_top_n: int) -> None:
    monkeypatch.setenv("HISTORY_KEEP_TOP_N", str(keep_top_n))
    monkeypatch.setattr("app.core.config.find_dotenv", lambda **kwargs: "")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.history_keep_top_n == keep_top_n
        if keep_top_n > 1000:
            with pytest.raises(ValidationError, match="keep_top_n"):
                _ = settings.history_settings
        else:
            assert settings.history_settings.keep_top_n == keep_top_n
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("failing_method", ["prune_before", "vacuum_if_beneficial"])
@pytest.mark.asyncio
async def test_history_recorder_returns_committed_rows_when_maintenance_fails(
    tmp_path, monkeypatch, caplog, failing_method: str,
) -> None:
    path = str(tmp_path / "history.db")
    db = await connect_database(path)
    try:
        await initialize_schema(db)
        repo = OpportunityHistoryRepository(db)
        now = datetime(2026, 10, 30, tzinfo=UTC)
        await repo.insert_many([
            repo.row_from_opportunity(opportunity("OLDUSDT", 1.0), now - timedelta(days=4))
        ])
        failure = RuntimeError("injected history maintenance failure")
        failing_call = AsyncMock(side_effect=failure)
        monkeypatch.setattr(repo, failing_method, failing_call)
        recorder = OpportunityHistoryRecorder(repo, HistorySettings())

        with caplog.at_level(logging.WARNING, logger="app.services.history"):
            inserted = await recorder.record(
                [opportunity("FIRSTUSDT", 2.0), opportunity("SECONDUSDT", 1.0)], now=now,
            )

        assert inserted == 2
        assert recorder._last_sample_at == now
        assert not db.in_transaction
        failing_call.assert_awaited_once()
        reader = await connect_database(path)
        try:
            stored = await OpportunityHistoryRepository(reader).list(since=now)
            assert {row.symbol for row in stored} == {"FIRSTUSDT", "SECONDUSDT"}
            assert len(stored) == 2
        finally:
            await reader.close()
        warnings = [
            record for record in caplog.records
            if record.name == "app.services.history" and record.levelno == logging.WARNING
        ]
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None
        assert warnings[0].exc_info[1] is failure
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_history_recorder_propagates_insert_failure_without_pruning(monkeypatch, caplog):
    repo = AsyncMock(spec=OpportunityHistoryRepository)
    failure = sqlite3.IntegrityError("injected history insert failure")
    repo.insert_many.side_effect = failure
    recorder = OpportunityHistoryRecorder(repo, HistorySettings())
    prune = AsyncMock()
    monkeypatch.setattr(recorder, "_prune", prune)

    with pytest.raises(sqlite3.IntegrityError, match="injected history insert failure"):
        await recorder.record([opportunity("BTCUSDT", 1.0)], now=datetime.now(UTC))

    assert recorder._last_sample_at is None
    prune.assert_not_awaited()
    assert not [record for record in caplog.records if record.name == "app.services.history"]
