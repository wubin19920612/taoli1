import sqlite3

import pytest

from app.db.database import connect_database


@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("many", [False, True])
async def test_stale_snapshot_is_released_without_replaying_the_failed_write(tmp_path, recover, many):
    path = str(tmp_path / "snapshot.db")
    db = await connect_database(path, recover_stale_snapshots=recover)
    other = None
    try:
        await db.execute("CREATE TABLE probe (value INTEGER PRIMARY KEY)")
        await db.execute("INSERT INTO probe VALUES (1)")
        await db.commit()
        other = await connect_database(path)
        await db.execute("BEGIN")
        async with db.execute("SELECT value FROM probe") as cursor:
            assert [row[0] for row in await cursor.fetchall()] == [1]
        await other.execute("INSERT INTO probe VALUES (2)")
        await other.commit()

        async def write():
            if many:
                await db.executemany("INSERT INTO probe VALUES (?)", [(3,), (4,)])
            else:
                await db.execute("INSERT INTO probe VALUES (3)")

        with pytest.raises(sqlite3.OperationalError) as failed:
            await write()
        assert failed.value.sqlite_errorcode == sqlite3.SQLITE_BUSY_SNAPSHOT
        assert db.in_transaction is (not recover)
        async with other.execute("SELECT value FROM probe ORDER BY value") as cursor:
            # Recovery does not replay a write computed from stale reads.
            assert [row[0] for row in await cursor.fetchall()] == [1, 2]
        if recover:
            await write()
            await db.commit()
            async with other.execute("SELECT value FROM probe ORDER BY value") as cursor:
                assert [row[0] for row in await cursor.fetchall()] == ([1, 2, 3, 4] if many else [1, 2, 3])
    finally:
        await db.rollback()
        if other is not None:
            await other.close()
        await db.close()


async def test_snapshot_recovery_does_not_rollback_successful_writes_on_other_errors(tmp_path):
    db = await connect_database(str(tmp_path / "snapshot.db"), recover_stale_snapshots=True)
    try:
        await db.execute("CREATE TABLE probe (value INTEGER PRIMARY KEY)")
        await db.execute("INSERT INTO probe VALUES (1)")
        await db.commit()
        await db.execute("INSERT INTO probe VALUES (2)")
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute("INSERT INTO probe VALUES (1)")
        assert db.in_transaction
        await db.commit()
        async with db.execute("SELECT value FROM probe ORDER BY value") as cursor:
            assert [row[0] for row in await cursor.fetchall()] == [1, 2]
    finally:
        await db.close()
