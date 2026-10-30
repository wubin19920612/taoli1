import asyncio
import json

from app.models.fs_monitor import FsReport, FsSettings


class FsRepository:
    """Dedicated connection; bounded observations are included in radar.db backups."""

    def __init__(self, db):
        self.db = db
        self.lock = asyncio.Lock()

    async def initialize(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS fs_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fs_snapshots (bucket INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fs_events (id INTEGER PRIMARY KEY AUTOINCREMENT, observed_at TEXT NOT NULL,
                symbol TEXT NOT NULL, stage TEXT NOT NULL, reasons TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fs_astro_attempts (symbol TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
        await self.db.commit()

    async def settings(self):
        async with (
            self.lock,
            self.db.execute("SELECT payload FROM fs_settings WHERE id=1") as cursor,
        ):
            row = await cursor.fetchone()
        return FsSettings.model_validate_json(row[0]) if row else FsSettings()

    async def save_settings(self, settings):
        async with self.lock:
            try:
                await self.db.execute(
                    "INSERT INTO fs_settings VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (settings.model_dump_json(),),
                )
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise
        return settings

    async def save(self, report, events):
        bucket = int(report.observed_at.timestamp()) // 300
        async with self.lock:
            try:
                await self.db.execute(
                    "INSERT OR IGNORE INTO fs_snapshots VALUES (?, ?, ?)",
                    (bucket, report.observed_at.isoformat(), report.model_dump_json()),
                )
                await self.db.executemany(
                    "INSERT INTO fs_events(observed_at, symbol, stage, reasons) VALUES (?, ?, ?, ?)",
                    [
                        (report.observed_at.isoformat(), s, stage, json.dumps(reasons))
                        for s, stage, reasons in events
                    ],
                )
                await self.db.execute(
                    "DELETE FROM fs_snapshots WHERE bucket NOT IN (SELECT bucket FROM fs_snapshots ORDER BY bucket DESC LIMIT 288)"
                )
                await self.db.execute(
                    "DELETE FROM fs_events WHERE id NOT IN (SELECT id FROM fs_events ORDER BY id DESC LIMIT 500)"
                )
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def history(self, limit=24):
        async with (
            self.lock,
            self.db.execute(
                "SELECT payload FROM fs_snapshots ORDER BY bucket DESC LIMIT ?", (limit,)
            ) as cursor,
        ):
            rows = await cursor.fetchall()
        return [FsReport.model_validate_json(row[0]) for row in rows]

    async def events(self):
        async with (
            self.lock,
            self.db.execute("SELECT * FROM fs_events ORDER BY id DESC LIMIT 100") as cursor,
        ):
            rows = await cursor.fetchall()
        return [{**dict(row), "reasons": json.loads(row["reasons"])} for row in rows]

    async def astro_attempt(self, symbol):
        async with (
            self.lock,
            self.db.execute(
                "SELECT state FROM fs_astro_attempts WHERE symbol=?", (symbol,)
            ) as cursor,
        ):
            row = await cursor.fetchone()
        return row[0] if row else None

    async def record_astro_attempt(self, symbol, state):
        from datetime import UTC, datetime

        async with self.lock:
            try:
                now = datetime.now(UTC).isoformat()
                await self.db.execute(
                    "INSERT INTO fs_astro_attempts VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at",
                    (symbol, state, now),
                )
                await self.db.execute(
                    "INSERT INTO fs_events(observed_at,symbol,stage,reasons) VALUES(?,?,?,?)",
                    (now, symbol, "astro_" + state, "[]"),
                )
                await self.db.execute(
                    "DELETE FROM fs_events WHERE id NOT IN (SELECT id FROM fs_events ORDER BY id DESC LIMIT 500)"
                )
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def aclose(self):
        await self.db.close()
