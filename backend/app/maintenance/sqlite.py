"""Daily, bounded retention; never borrow a repository connection."""

import asyncio
import logging
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import Settings
from app.db.database import connect_database
from app.runtime.supervisor import beat

logger = logging.getLogger(__name__)

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
        db = await connect_database(self.path)
        try:
            if self.settings.sqlite_maintenance_dry_run:
                await db.execute("PRAGMA query_only=ON")
            for table, column in RETENTION_TABLES:
                if stopped():
                    return
                days = getattr(self.settings, f"sqlite_maintenance_{table}_retention_days")
                await self._prune(db, table, column, now - timedelta(days=days), stopped)
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
            # interrupt() acts directly on sqlite, before close queues behind an
            # in-flight VACUUM/COUNT. Closing this owned connection also rolls
            # back any unfinished batch; no shared transaction is touched.
            try:
                await db.interrupt()
            finally:
                await db.close()

    async def _prune(self, db, table, column, cutoff, stopped):
        started = time.monotonic()
        deleted = would_delete = 0
        status = "ok"
        try:
            async with db.execute(f'PRAGMA table_info("{table}")') as cursor:
                columns = {row[1] for row in await cursor.fetchall()}
            if not columns:
                status = "missing_table"
                logger.warning("sqlite maintenance skipped table=%s reason=%s", table, status)
                return
            if column not in columns:
                status = "missing_column"
                logger.warning("sqlite maintenance skipped table=%s reason=%s column=%s",
                               table, status, column)
                return
            # Normalize Z/offset/space variants; invalid timestamps are retained.
            # Select outside a write transaction and advance rowid to scan once.
            predicate = f'julianday("{column}") < julianday(?)'
            cutoff_text = cutoff.isoformat()
            if self.settings.sqlite_maintenance_dry_run:
                async with db.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE {predicate}', (cutoff_text,),
                ) as cursor:
                    would_delete = (await cursor.fetchone())[0]
                return
            async with db.execute(f'SELECT MAX(rowid) FROM "{table}"') as cursor:
                upper = (await cursor.fetchone())[0]
            after = None
            while upper is not None and not stopped():
                lower_clause = "" if after is None else " AND rowid > ?"
                params = [cutoff_text, upper]
                if after is not None:
                    params.append(after)
                params.append(self.settings.sqlite_maintenance_batch_size)
                async with db.execute(
                    f'SELECT rowid FROM "{table}" WHERE {predicate} AND rowid <= ?'
                    f'{lower_clause} ORDER BY rowid LIMIT ?', params,
                ) as cursor:
                    ids = [row[0] for row in await cursor.fetchall()]
                if not ids or stopped():
                    break
                placeholders = ",".join("?" for _ in ids)
                # Recheck age in case a business writer changed a selected row.
                async with db.execute(
                    f'DELETE FROM "{table}" WHERE rowid IN ({placeholders}) AND {predicate}',
                    [*ids, cutoff_text],
                ) as cursor:
                    removed = cursor.rowcount
                await db.commit()
                deleted += removed
                after = ids[-1]
                await self._yield_between_batches()
            if stopped():
                status = "interrupted"
        except BaseException:
            status = "failed"
            raise
        finally:
            logger.info(
                "sqlite maintenance table=%s dry_run=%s deleted=%s would_delete=%s "
                "elapsed_seconds=%.3f status=%s",
                table, self.settings.sqlite_maintenance_dry_run, deleted, would_delete,
                time.monotonic() - started, status,
            )

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
