import logging
import sqlite3

import aiosqlite

logger = logging.getLogger(__name__)


class _SnapshotRecoveringConnection(sqlite3.Connection):
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
    factory = _SnapshotRecoveringConnection if recover_stale_snapshots else sqlite3.Connection
    db = await aiosqlite.connect(path, factory=factory)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA foreign_keys=ON")
    return db
