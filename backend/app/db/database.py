import logging
import operator
import sqlite3
from collections import deque

import aiosqlite

logger = logging.getLogger(__name__)


class _MaterializedCursor:
    """Finish result statements before yielding aiosqlite's worker thread."""

    def __init__(self, cursor):
        try:
            self._rows = deque(cursor.fetchall())
            self.description = cursor.description
            self.rowcount = cursor.rowcount
            self.lastrowid = cursor.lastrowid
            self.arraysize = cursor.arraysize
        finally:
            cursor.close()

    def _check_open(self):
        if self._rows is None:
            raise sqlite3.ProgrammingError("Cannot operate on a closed cursor.")

    def fetchone(self):
        self._check_open()
        return self._rows.popleft() if self._rows else None

    def fetchall(self):
        self._check_open()
        rows = list(self._rows)
        self._rows.clear()
        return rows

    def fetchmany(self, size=None):
        self._check_open()
        size = operator.index(self.arraysize if size is None else size)
        # sqlite3 on the supported Python 3.12 treats nonpositive sizes as all rows.
        if size <= 0:
            return self.fetchall()
        return [self._rows.popleft() for _ in range(min(size, len(self._rows)))]

    def close(self):
        self._rows = None

    def __iter__(self):
        return self

    def __next__(self):
        self._check_open()
        if not self._rows:
            raise StopIteration
        return self._rows.popleft()


class _MaterializingConnection(sqlite3.Connection):
    def execute(self, sql, parameters=(), /):
        cursor = super().execute(sql, parameters)
        return _MaterializedCursor(cursor) if cursor.description is not None else cursor


class _SnapshotRecoveringConnection(_MaterializingConnection):
    """Release failed read snapshots atomically on aiosqlite's worker thread."""

    def _recover_snapshot(self, exc: sqlite3.OperationalError) -> None:
        if exc.sqlite_errorcode == sqlite3.SQLITE_BUSY_SNAPSHOT:
            # A snapshot upgrade failure precedes any successful write in this
            # transaction. Release it before the next queued caller runs. Do not
            # replay an operation that may have been computed from stale reads.
            super().rollback()
            logger.warning("Rolled back stale SQLite snapshot; failed operation was not replayed")

    def execute(self, sql, parameters=(), /):
        try:
            return super().execute(sql, parameters)
        except sqlite3.OperationalError as exc:
            self._recover_snapshot(exc)
            raise

    def executemany(self, sql, seq_of_parameters, /):
        try:
            return super().executemany(sql, seq_of_parameters)
        except sqlite3.OperationalError as exc:
            self._recover_snapshot(exc)
            raise


async def connect_database(
    path: str, *, recover_stale_snapshots: bool = False
) -> aiosqlite.Connection:
    factory = _SnapshotRecoveringConnection if recover_stale_snapshots else _MaterializingConnection
    db = await aiosqlite.connect(path, factory=factory)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA foreign_keys=ON")
    return db
