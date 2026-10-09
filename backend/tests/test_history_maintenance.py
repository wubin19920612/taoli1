from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.db.database import connect_database
from app.db.repositories import OpportunityHistoryRepository
from app.models.settings import HistorySettings
from app.services.history import OpportunityHistoryRecorder


async def storage(db):
    async with db.execute(
        "SELECT page_count, page_size, freelist_count "
        "FROM pragma_page_count(), pragma_page_size(), pragma_freelist_count()"
    ) as cursor:
        return tuple(await cursor.fetchone())


@pytest.mark.parametrize("total_mb,deleted_mb,expected", [
    (24, 0, False),   # No reclaimable pages.
    (24, 8, False),   # Large ratio, insufficient absolute savings.
    (192, 16, False), # Enough bytes, but less than 10% of the full database.
    (32, 20, True),   # Both limits met: actually compact the database.
])
@pytest.mark.asyncio
async def test_auto_vacuum_requires_meaningful_reclaim_and_preserves_rows(
    tmp_path, total_mb, deleted_mb, expected,
):
    db = await connect_database(str(tmp_path / "maintenance.db"))
    statements = []
    try:
        await db.execute("CREATE TABLE preserved (id INTEGER PRIMARY KEY, payload BLOB)")
        await db.executemany(
            "INSERT INTO preserved VALUES (?, zeroblob(1048576))",
            [(i,) for i in range(total_mb)],
        )
        await db.commit()
        await db.execute("DELETE FROM preserved WHERE id < ?", (deleted_mb,))
        await db.commit()
        before = await storage(db)
        await db.set_trace_callback(statements.append)
        compacted = await OpportunityHistoryRepository(db).vacuum_if_beneficial()
        assert compacted is expected
        assert any(sql == "VACUUM" for sql in statements) is expected
        after = await storage(db)
        async with db.execute("SELECT id, length(payload) FROM preserved ORDER BY id") as cursor:
            assert [tuple(row) for row in await cursor.fetchall()] == [
                (i, 1048576) for i in range(deleted_mb, total_mb)
            ]
        async with db.execute("PRAGMA integrity_check") as cursor:
            assert (await cursor.fetchone())[0] == "ok"
        if expected:
            assert after[0] < before[0]
            assert after[2] == 0
        else:
            assert after == before
            # Skipped free pages are usable by new writes instead of growing
            # the file; retain the same row ids to avoid b-tree shape changes.
            await db.executemany(
                "INSERT INTO preserved VALUES (?, zeroblob(1048576))",
                [(i,) for i in range(deleted_mb)],
            )
            await db.commit()
            reused = await storage(db)
            assert reused[0] <= before[0] + 2
            assert reused[2] <= before[2]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_auto_vacuum_does_not_commit_an_existing_transaction(tmp_path):
    db = await connect_database(str(tmp_path / "transaction.db"))
    try:
        await db.execute("CREATE TABLE preserved (id INTEGER PRIMARY KEY)")
        await db.commit()
        await db.execute("INSERT INTO preserved VALUES (1)")
        assert db.in_transaction
        assert await OpportunityHistoryRepository(db).vacuum_if_beneficial() is False
        assert db.in_transaction
        await db.rollback()
        async with db.execute("SELECT COUNT(*) FROM preserved") as cursor:
            assert (await cursor.fetchone())[0] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_recorder_rechecks_skipped_vacuum_but_throttles_successful_compaction():
    repo = AsyncMock(spec=OpportunityHistoryRepository)
    repo.prune_before.return_value = 1
    repo.vacuum_if_beneficial.side_effect = [False, True, True]
    recorder = OpportunityHistoryRecorder(repo, HistorySettings(vacuum_interval_seconds=3600))
    now = datetime(2026, 10, 9, tzinfo=UTC)
    await recorder._prune(now)
    await recorder._prune(now + timedelta(minutes=1))
    assert repo.vacuum_if_beneficial.await_count == 2
    await recorder._prune(now + timedelta(minutes=59))
    assert repo.vacuum_if_beneficial.await_count == 2
    await recorder._prune(now + timedelta(minutes=61))
    assert repo.vacuum_if_beneficial.await_count == 3
    assert repo.prune_before.await_count == 4


@pytest.mark.asyncio
async def test_failed_vacuum_can_retry_without_delaying_history_pruning():
    repo = AsyncMock(spec=OpportunityHistoryRepository)
    repo.prune_before.return_value = 1
    repo.vacuum_if_beneficial.side_effect = [RuntimeError("database busy"), True]
    recorder = OpportunityHistoryRecorder(repo, HistorySettings())
    now = datetime(2026, 10, 9, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="database busy"):
        await recorder._prune(now)
    await recorder._prune(now + timedelta(minutes=1))
    assert repo.vacuum_if_beneficial.await_count == 2
    assert repo.prune_before.await_count == 2


@pytest.mark.asyncio
async def test_no_deleted_history_does_not_query_compaction_metadata():
    repo = AsyncMock(spec=OpportunityHistoryRepository)
    repo.prune_before.return_value = 0
    recorder = OpportunityHistoryRecorder(repo, HistorySettings())
    await recorder._prune(datetime(2026, 10, 9, tzinfo=UTC))
    repo.vacuum_if_beneficial.assert_not_awaited()
