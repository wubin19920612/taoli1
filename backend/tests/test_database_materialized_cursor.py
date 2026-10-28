import asyncio
import sqlite3

import aiosqlite
import pytest

from app.db import database


@pytest.mark.parametrize("mode", ["plain", "materialized", "recovering"])
async def test_concurrent_read_other_commit_and_write(tmp_path, monkeypatch, mode):
    path = str(tmp_path / "concurrent.db")
    original_connect = aiosqlite.connect

    def plain_connect(*args, **kwargs):
        kwargs["factory"] = sqlite3.Connection
        return original_connect(*args, **kwargs)

    async with asyncio.timeout(30):
        with monkeypatch.context() as patch:
            if mode == "plain":
                patch.setattr(aiosqlite, "connect", plain_connect)
            db = await database.connect_database(
                path, recover_stale_snapshots=mode == "recovering"
            )
            try:
                other = await database.connect_database(path)
            except BaseException:
                await db.close()
                raise
        errors = []
        try:
            await db.execute("CREATE TABLE s(k TEXT PRIMARY KEY, v TEXT)")
            await db.executemany(
                "INSERT INTO s VALUES (?, ?)", [(str(i), "v") for i in range(50)]
            )
            await db.commit()
            for _ in range(200):
                reading = asyncio.Event()
                committed = asyncio.Event()
                written = asyncio.Event()

                async def reader(reading=reading, written=written):
                    async with db.execute("SELECT * FROM s") as cursor:
                        reading.set()
                        await asyncio.sleep(0)
                        # Fix the vulnerable interleaving instead of relying on
                        # OS scheduling between the two worker queue operations.
                        await written.wait()
                        rows = await cursor.fetchall()
                        assert len(rows) >= 50 and rows[0]["k"] is not None

                async def other_commit(reading=reading, committed=committed):
                    await reading.wait()
                    await other.execute("INSERT OR REPLACE INTO s VALUES ('o', '1')")
                    await other.commit()
                    committed.set()

                async def writer(committed=committed, written=written):
                    await committed.wait()
                    await asyncio.sleep(0)
                    try:
                        await db.execute("INSERT OR REPLACE INTO s VALUES ('w', '1')")
                        await db.commit()
                    except sqlite3.OperationalError as exc:
                        errors.append(exc.sqlite_errorcode)
                        await db.rollback()
                    finally:
                        written.set()

                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(reader())
                    tasks.create_task(other_commit())
                    tasks.create_task(writer())
            assert all(code == sqlite3.SQLITE_BUSY_SNAPSHOT for code in errors)
            if mode == "plain":
                assert len(errors) > 0
            else:
                assert len(errors) == 0
        finally:
            await db.rollback()
            await other.close()
            await db.close()


@pytest.mark.parametrize("recover", [False, True])
async def test_aiosqlite_cursor_semantics(tmp_path, recover):
    db = await database.connect_database(
        str(tmp_path / "cursor.db"), recover_stale_snapshots=recover
    )
    try:
        async with db.execute("PRAGMA journal_mode") as cursor:
            assert (await cursor.fetchone())[0] == "wal"
        await db.execute("CREATE TABLE probe(id INTEGER PRIMARY KEY, value TEXT)")
        async with db.execute("INSERT INTO probe(value) VALUES ('a')") as cursor:
            assert isinstance(cursor._cursor, sqlite3.Cursor)
            assert (cursor.rowcount, cursor.lastrowid, cursor.description) == (1, 1, None)
        async with db.executemany(
            "INSERT INTO probe(value) VALUES (?)", [("b",), ("c",), ("d",)]
        ) as cursor:
            assert isinstance(cursor._cursor, sqlite3.Cursor)
            assert cursor.rowcount == 3
        await db.commit()
        query = "SELECT id, value FROM probe ORDER BY id"
        async with db.execute(query) as cursor:
            assert [column[0] for column in cursor.description] == ["id", "value"]
            assert cursor.rowcount == -1
            assert cursor.arraysize == 1
            row = await cursor.fetchone()
            assert isinstance(row, aiosqlite.Row)
            assert (row["id"], row["value"]) == (1, "a")
            assert [row["id"] for row in await cursor.fetchall()] == [2, 3, 4]
            assert await cursor.fetchone() is None
            assert await cursor.fetchall() == []
        async with db.execute(query) as cursor:
            assert [row["id"] for row in await cursor.fetchmany(2)] == [1, 2]
            cursor.arraysize = 1
            assert [row["id"] for row in await cursor.fetchmany()] == [3]
            cursor.arraysize = 2
            assert [row["id"] for row in await cursor.fetchmany()] == [4]
            assert await cursor.fetchmany(2) == []
        async with db.execute(query) as cursor:
            assert [(await cursor.fetchone())["id"] for _ in range(4)] == [1, 2, 3, 4]
            assert await cursor.fetchone() is None
        assert [row["id"] for row in await db.execute_fetchall(query)] == [1, 2, 3, 4]
        async with db.execute(query) as cursor:
            cursor.iter_chunk_size = 2
            assert [row["id"] async for row in cursor] == [1, 2, 3, 4]
        async with db.execute("SELECT id FROM probe WHERE 0") as cursor:
            assert cursor.description[0][0] == "id"
            assert await cursor.fetchone() is None
            assert await cursor.fetchmany() == []
            assert await cursor.fetchall() == []
        async with db.execute(
            "INSERT INTO probe(value) VALUES ('e'), ('f') RETURNING id, value"
        ) as cursor:
            # The statement must already be finalized before caller-side fetch.
            await db.commit()
            assert cursor.rowcount == 2
            assert cursor.lastrowid == 6
            assert [tuple(row) for row in await cursor.fetchall()] == [(5, "e"), (6, "f")]
    finally:
        await db.close()


def test_materialized_cursor_matches_native_iteration_and_close():
    with sqlite3.connect(":memory:") as db:
        source = db.execute("SELECT 1 AS value UNION ALL SELECT 2 UNION ALL SELECT 3")
        source.arraysize = 2
        cursor = database._MaterializedCursor(source)
        assert cursor.arraysize == 2
        assert cursor.description == source.description
        assert cursor.lastrowid == source.lastrowid
        assert cursor.rowcount == source.rowcount
        with pytest.raises(sqlite3.ProgrammingError):
            source.fetchone()
        iterator = iter(cursor)
        assert next(iterator) == (1,)
        assert cursor.fetchone() == (2,)
        assert list(iterator) == [(3,)]
        assert cursor.fetchone() is None
        cursor.close()
        cursor.close()
        for fetch in (cursor.fetchone, cursor.fetchall, cursor.fetchmany):
            with pytest.raises(sqlite3.ProgrammingError):
                fetch()


@pytest.mark.parametrize("size", [0, -1, 1, 10])
def test_materialized_fetchmany_matches_native(size):
    with sqlite3.connect(":memory:") as db:
        query = "SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3"
        native = db.execute(query)
        cursor = database._MaterializedCursor(db.execute(query))
        assert cursor.fetchmany(size) == native.fetchmany(size)
        assert cursor.fetchall() == native.fetchall()


def test_materialization_closes_source_if_fetch_fails():
    with sqlite3.connect(":memory:") as db:
        def fail_on_row(cursor, row):
            raise ValueError("row conversion failed")

        source = db.execute("SELECT 1 UNION ALL SELECT 2")
        source.row_factory = fail_on_row
        with pytest.raises(ValueError, match="row conversion failed"):
            database._MaterializedCursor(source)
        with pytest.raises(sqlite3.ProgrammingError):
            source.fetchone()
