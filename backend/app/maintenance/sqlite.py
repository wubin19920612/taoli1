"""Daily, bounded retention; never borrow a repository connection."""

import asyncio
import logging
import shutil
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from app.core.config import Settings
from app.runtime.supervisor import beat

logger = logging.getLogger(__name__)
TABLE_BUDGET_SECONDS = 30
SQL_BUDGET_SECONDS = 1
WAL_LIMIT_BYTES = 64 * 1024 * 1024

# Identifiers are a fixed allowlist, never environment-provided SQL.
RETENTION_TABLES = (
    ("second_level_market_samples", "observed_at"),
    ("second_level_index_component_samples", "observed_at"),
    ("alert_events", "created_at"),
    ("oil_news_items", "published_at"),
    ("squeeze_hourly_candles", "event_time"),
)


class SQLiteMaintenance:
    def __init__(self, path: str, settings: Settings):
        self.path = path
        self.settings = settings
        self._last_run_date = None
        self._now = lambda: datetime.now(UTC)

    async def _wait(self, stop_event: asyncio.Event):
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except TimeoutError:
            pass

    async def run(self, stop_event: asyncio.Event):
        if not self.settings.sqlite_maintenance_enabled:
            return
        if self.path == ":memory:":
            logger.warning("sqlite maintenance skipped reason=in_memory_database")
            return
        while not stop_event.is_set():
            now = self._now()
            if (now.hour == self.settings.sqlite_maintenance_hour_utc
                    and self._last_run_date != now.date()):
                # Supervisor retries must not create a busy failure loop.
                self._last_run_date = now.date()
                deadline = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                await self.run_once(now=now, stop_event=stop_event, deadline=deadline)
                beat("sqlite-maintenance", ok=True)
            await self._wait(stop_event)

    async def _yield_between_batches(self):
        await asyncio.sleep(0.05)

    async def run_once(self, *, now=None, stop_event=None, deadline=None):
        if not self.settings.sqlite_maintenance_enabled or self.path == ":memory:":
            return
        now = now or self._now()

        def stopped():
            return ((stop_event is not None and stop_event.is_set())
                    or (deadline is not None and self._now() >= deadline))

        if stopped():
            return
        dry_run = self.settings.sqlite_maintenance_dry_run
        path = Path(self.path).resolve()
        if dry_run and not path.is_file():
            logger.info("sqlite maintenance skipped reason=missing_database")
            return
        # Keep ownership from opening through initialization. Shield the open so
        # cancellation cannot abandon a connection still being made by the thread.
        opening = asyncio.ensure_future(aiosqlite.connect(
            path.as_uri() + ("?mode=ro" if dry_run else "?mode=rw"), uri=True,
        ))
        db = None
        try:
            db = await asyncio.shield(opening)
            if dry_run:
                await db.execute("PRAGMA query_only=ON")
            else:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA synchronous=NORMAL")
                await db.execute("PRAGMA wal_autocheckpoint=1000")
            await db.execute("PRAGMA busy_timeout=1000")
            await db.execute("PRAGMA foreign_keys=ON")
            for table, column in RETENTION_TABLES:
                if stopped():
                    return
                days = getattr(self.settings, f"sqlite_maintenance_{table}_retention_days")
                if not await self._prune(db, table, column, now - timedelta(days=days), stopped):
                    return
            if self.settings.sqlite_maintenance_dry_run or stopped():
                return
            async with db.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cursor:
                busy, pages, checkpointed = await cursor.fetchone()
            log = logger.warning if busy else logger.info
            log("sqlite maintenance checkpoint busy=%s wal_pages=%s checkpointed=%s",
                busy, pages, checkpointed)
            if busy:
                logger.warning("sqlite maintenance vacuum skipped reason=checkpoint_busy")
            elif not stopped():
                await self._vacuum_if_beneficial(db)
        finally:
            if db is None:
                try:
                    db = await opening
                except (sqlite3.Error, OSError):
                    logger.warning("sqlite maintenance connection open failed", exc_info=True)
            # interrupt() acts directly on sqlite, before close queues behind an
            # in-flight VACUUM/COUNT. Closing this owned connection also rolls
            # back any unfinished batch; no shared transaction is touched.
            if db is not None:
                try:
                    await db.interrupt()
                finally:
                    await db.close()

    async def _prune(self, db, table, column, cutoff, stopped):
        started = time.monotonic()
        deleted = would_delete = 0
        status = "ok"
        table_deadline = started + TABLE_BUDGET_SECONDS
        sql_deadline = min(table_deadline, started + SQL_BUDGET_SECONDS)

        def expired():
            return stopped() or time.monotonic() >= sql_deadline

        try:
            await db.set_progress_handler(lambda: int(expired()), 1000)
            if expired():
                status = "interrupted"
                return False
            async with db.execute(f'PRAGMA table_info("{table}")') as cursor:
                columns = {row[1] for row in await cursor.fetchall()}
            if not columns:
                status = "missing_table"
                logger.warning("sqlite maintenance skipped table=%s reason=%s", table, status)
                return True
            if column not in columns:
                status = "missing_column"
                logger.warning("sqlite maintenance skipped table=%s reason=%s column=%s",
                               table, status, column)
                return True
            cutoff = cutoff.astimezone(UTC)
            # ISO offsets are strictly less than 24 hours. Widen the indexed
            # lexical upper bound by a day, then parse candidates in Python.
            # This includes offset/microsecond boundaries without julianday(column)
            # defeating the index. Malformed/naive timestamps are retained; unusual
            # non-ISO encodings outside this range are conservatively left alone.
            upper_time = (cutoff + timedelta(days=1)).isoformat()
            async with db.execute(f'SELECT MAX(rowid) FROM "{table}"') as cursor:
                upper = (await cursor.fetchone())[0]
            after = None
            while upper is not None:
                sql_deadline = min(table_deadline, time.monotonic() + SQL_BUDGET_SECONDS)
                if expired():
                    status = "interrupted"
                    return False
                if not self.settings.sqlite_maintenance_dry_run and not await self._wal_guard(db):
                    status = "wal_pressure"
                    return False
                rows = []
                batch_size = self.settings.sqlite_maintenance_batch_size
                if after is not None:
                    # Seek directly within a duplicate timestamp, rather than
                    # rescanning all earlier rowids in that timestamp each batch.
                    async with db.execute(
                        f'SELECT rowid, "{column}" FROM "{table}" WHERE "{column}" = ?'
                        ' AND rowid > ? AND rowid <= ? ORDER BY rowid LIMIT ?',
                        [*after, upper, batch_size],
                    ) as cursor:
                        rows = await cursor.fetchall()
                if len(rows) < batch_size and not expired():
                    # Existing time indexes are (timestamp DESC, implicit rowid ASC).
                    # Match that order to avoid an unbounded temporary sort.
                    async with db.execute(
                        f'SELECT rowid, "{column}" FROM "{table}" WHERE "{column}" < ?'
                        f' AND rowid <= ? ORDER BY "{column}" DESC, rowid LIMIT ?',
                        [upper_time if after is None else after[0], upper, batch_size - len(rows)],
                    ) as cursor:
                        rows.extend(await cursor.fetchall())
                if expired():
                    status = "interrupted"
                    return False
                if not rows:
                    break
                candidates = []
                for rowid, value in rows:
                    try:
                        observed = datetime.fromisoformat(value)
                        if observed.tzinfo is not None and observed < cutoff:
                            candidates.append((rowid, value))
                    except (TypeError, ValueError):
                        pass
                if expired():
                    status = "interrupted"
                    return False
                if self.settings.sqlite_maintenance_dry_run:
                    would_delete += len(candidates)
                elif candidates:
                    # Exact value recheck protects rows changed by another writer
                    # after selection. The rowid lookup keeps each DELETE bounded.
                    async with db.executemany(
                        f'DELETE FROM "{table}" WHERE rowid = ? AND "{column}" = ?', candidates,
                    ) as cursor:
                        removed = cursor.rowcount
                    await db.commit()
                    deleted += removed
                after = (rows[-1][1], rows[-1][0])
                await self._yield_between_batches()
            return True
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT and expired():
                status = "interrupted"
                return False
            status = "failed"
            raise
        except asyncio.CancelledError:
            status = "interrupted"
            await db.interrupt()
            raise
        except BaseException:
            status = "failed"
            raise
        finally:
            await db.set_progress_handler(None, 0)
            logger.info(
                "sqlite maintenance table=%s dry_run=%s deleted=%s would_delete=%s "
                "elapsed_seconds=%.3f status=%s",
                table, self.settings.sqlite_maintenance_dry_run, deleted, would_delete,
                time.monotonic() - started, status,
            )

    async def _wal_guard(self, db):
        try:
            size = Path(self.path + "-wal").stat().st_size
        except FileNotFoundError:
            return True
        if size <= WAL_LIMIT_BYTES:
            return True
        async with db.execute("PRAGMA wal_checkpoint(PASSIVE)") as cursor:
            busy, pages, checkpointed = await cursor.fetchone()
        # PASSIVE may report busy=0 even when a reader pins uncheckpointed frames.
        # A fully checkpointed WAL may remain allocated but is reusable.
        proceed = not busy and pages == checkpointed
        logger.warning(
            "sqlite maintenance wal_pressure bytes=%s busy=%s wal_pages=%s "
            "checkpointed=%s paused=%s", size, busy, pages, checkpointed, not proceed,
        )
        return proceed

    async def _vacuum_if_beneficial(self, db):
        if self.settings.sqlite_maintenance_dry_run or db.in_transaction:
            return False
        async with db.execute(
            "SELECT page_count, page_size, freelist_count "
            "FROM pragma_page_count(), pragma_page_size(), pragma_freelist_count()"
        ) as cursor:
            page_count, page_size, freelist = await cursor.fetchone()
        size = page_count * page_size
        reclaim = freelist * page_size
        if reclaim < 16 * 1024 * 1024 or freelist < page_count * 0.10:
            logger.warning("sqlite maintenance vacuum skipped reason=small_freelist reclaim_bytes=%s",
                           reclaim)
            return False
        try:
            path = Path(self.path).resolve()
            size = max(size, path.stat().st_size)
            free = shutil.disk_usage(path.parent).free
        except OSError:
            logger.warning("sqlite maintenance vacuum skipped reason=disk_check_failed")
            return False
        if free < size * 1.5:
            logger.warning(
                "sqlite maintenance vacuum skipped reason=insufficient_disk free_bytes=%s "
                "required_bytes=%s", free, size * 1.5,
            )
            return False
        started = time.monotonic()
        await db.execute("VACUUM")
        logger.info("sqlite maintenance vacuum complete reclaim_bytes=%s elapsed_seconds=%.3f",
                    reclaim, time.monotonic() - started)
        return True
