from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

import aiosqlite

from .models import utc_iso
from .route_engine import LegSnapshot, Route, RouteEvaluation
from .route_tracker import RouteTracker


async def initialize_route_schema(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS squeeze_route_state (
          route_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS squeeze_route_latest (
          route_id TEXT PRIMARY KEY, evaluated_at TEXT NOT NULL,
          evaluation_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS squeeze_route_events (
          id TEXT PRIMARY KEY, route_id TEXT NOT NULL, phase TEXT NOT NULL,
          occurred_at TEXT NOT NULL, rule_version TEXT NOT NULL,
          evaluation_json TEXT NOT NULL, inputs_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_squeeze_route_events_time
          ON squeeze_route_events(occurred_at DESC);
        CREATE TABLE IF NOT EXISTS squeeze_route_worker_state (
          id INTEGER PRIMARY KEY CHECK(id = 1), last_attempt_at TEXT,
          last_success_at TEXT, last_error TEXT, last_route_id TEXT,
          storage_failure_count INTEGER NOT NULL DEFAULT 0,
          last_storage_error_at TEXT, last_storage_error TEXT,
          dropped_scan_count INTEGER NOT NULL DEFAULT 0, last_drop_at TEXT
        );
        CREATE TABLE IF NOT EXISTS squeeze_route_meta (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
    """)
    cursor = await db.execute("PRAGMA table_info(squeeze_route_worker_state)")
    columns = {row["name"] for row in await cursor.fetchall()}
    additions = {
        "storage_failure_count": "INTEGER NOT NULL DEFAULT 0",
        "last_storage_error_at": "TEXT",
        "last_storage_error": "TEXT",
        "dropped_scan_count": "INTEGER NOT NULL DEFAULT 0",
        "last_drop_at": "TEXT",
    }
    for name, ddl in additions.items():
        if name not in columns:
            await db.execute(f"ALTER TABLE squeeze_route_worker_state ADD COLUMN {name} {ddl}")
    await db.commit()


async def migrate_legacy_route_data(db: aiosqlite.Connection, legacy_path: str) -> None:
    cursor = await db.execute(
        "SELECT 1 FROM squeeze_route_meta WHERE key='legacy_radar_imported'"
    )
    if await cursor.fetchone():
        return
    await db.execute("ATTACH DATABASE ? AS legacy_route", (legacy_path,))
    try:
        cursor = await db.execute(
            """SELECT name FROM legacy_route.sqlite_master
               WHERE type='table' AND name IN
               ('squeeze_route_state','squeeze_route_latest',
                'squeeze_route_events','squeeze_route_worker_state')"""
        )
        found = {row["name"] for row in await cursor.fetchall()}
        if len(found) == 4:
            await db.execute(
                "INSERT OR IGNORE INTO squeeze_route_state SELECT * FROM legacy_route.squeeze_route_state"
            )
            await db.execute(
                "INSERT OR IGNORE INTO squeeze_route_latest SELECT * FROM legacy_route.squeeze_route_latest"
            )
            await db.execute(
                "INSERT OR IGNORE INTO squeeze_route_events SELECT * FROM legacy_route.squeeze_route_events"
            )
            await db.execute(
                """INSERT OR IGNORE INTO squeeze_route_worker_state
                   (id,last_attempt_at,last_success_at,last_error,last_route_id)
                   SELECT id,last_attempt_at,last_success_at,last_error,last_route_id
                   FROM legacy_route.squeeze_route_worker_state"""
            )
        await db.execute(
            "INSERT INTO squeeze_route_meta(key,value) VALUES ('legacy_radar_imported',?)",
            (legacy_path,),
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    finally:
        await db.execute("DETACH DATABASE legacy_route")


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return utc_iso(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _inputs(route: Route, expensive: LegSnapshot, cheap: LegSnapshot) -> str:
    return json.dumps(
        {
            "route": asdict(route),
            "expensive": asdict(expensive),
            "cheap": asdict(cheap),
            "source": "public_rest_full_snapshots",
        },
        default=_json_default, separators=(",", ":"),
    )


class SqueezeRouteRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db
        self._lock = asyncio.Lock()

    async def _write(self, operation: Callable[[], Awaitable[None]]) -> None:
        async with self._lock:
            for attempt in range(3):
                try:
                    await operation()
                    await self.db.commit()
                    return
                except BaseException as exc:
                    await self.db.rollback()
                    locked = (
                        isinstance(exc, sqlite3.OperationalError)
                        and ("locked" in str(exc).lower() or "busy" in str(exc).lower())
                    )
                    if not locked or attempt == 2:
                        raise
                    await asyncio.sleep(0.2 * (attempt + 1))

    async def load_tracker(self, route_id: str) -> RouteTracker:
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT state_json FROM squeeze_route_state WHERE route_id=?", (route_id,)
            )
            row = await cursor.fetchone()
        return RouteTracker.from_dict(json.loads(row["state_json"])) if row else RouteTracker(route_id)

    async def save_tracker(self, tracker: RouteTracker, at: datetime) -> None:
        async def write() -> None:
            await self.db.execute(
                """INSERT INTO squeeze_route_state(route_id,state_json,updated_at)
                   VALUES (?,?,?) ON CONFLICT(route_id) DO UPDATE SET
                   state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (tracker.route_id, json.dumps(tracker.to_dict(), separators=(",", ":")), utc_iso(at)),
            )

        await self._write(write)

    async def save_scan(
        self, route: Route, tracker: RouteTracker, evaluation: RouteEvaluation,
        expensive: LegSnapshot, cheap: LegSnapshot, transition: str | None,
        *, storage_failures: int = 0, last_storage_error_at: datetime | None = None,
        last_storage_error: str | None = None, dropped_scans: int = 0,
        last_drop_at: datetime | None = None,
    ) -> None:
        at = utc_iso(evaluation.calculated_at)
        evaluation_json = json.dumps(evaluation.to_dict(), separators=(",", ":"))

        async def write() -> None:
            await self.db.execute(
                """INSERT INTO squeeze_route_state(route_id,state_json,updated_at)
                   VALUES (?,?,?) ON CONFLICT(route_id) DO UPDATE SET
                   state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (route.route_id, json.dumps(tracker.to_dict(), separators=(",", ":")), at),
            )
            await self.db.execute(
                """INSERT INTO squeeze_route_latest(route_id,evaluated_at,evaluation_json)
                   VALUES (?,?,?) ON CONFLICT(route_id) DO UPDATE SET
                   evaluated_at=excluded.evaluated_at,evaluation_json=excluded.evaluation_json""",
                (route.route_id, at, evaluation_json),
            )
            if transition is not None:
                await self.db.execute(
                    """INSERT OR IGNORE INTO squeeze_route_events
                       (id,route_id,phase,occurred_at,rule_version,evaluation_json,inputs_json)
                       VALUES (?,?,?,?,?,?,?)""",
                    (f"{tracker.event_id}:{transition}", route.route_id, transition, at,
                     evaluation.rule_version, evaluation_json, _inputs(route, expensive, cheap)),
                )
            await self.db.execute(
                """INSERT INTO squeeze_route_worker_state
                   (id,last_attempt_at,last_success_at,last_error,last_route_id,
                    storage_failure_count,last_storage_error_at,last_storage_error,
                    dropped_scan_count,last_drop_at)
                   VALUES (1,?,?,NULL,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   last_attempt_at=excluded.last_attempt_at,
                   last_success_at=excluded.last_success_at,
                   last_error=NULL,last_route_id=excluded.last_route_id,
                   storage_failure_count=squeeze_route_worker_state.storage_failure_count
                     + excluded.storage_failure_count,
                   last_storage_error_at=COALESCE(
                     excluded.last_storage_error_at,
                     squeeze_route_worker_state.last_storage_error_at),
                   last_storage_error=COALESCE(
                     excluded.last_storage_error,
                     squeeze_route_worker_state.last_storage_error),
                   dropped_scan_count=squeeze_route_worker_state.dropped_scan_count
                     + excluded.dropped_scan_count,
                   last_drop_at=COALESCE(excluded.last_drop_at,
                     squeeze_route_worker_state.last_drop_at)""",
                (at, at, route.route_id, storage_failures,
                 utc_iso(last_storage_error_at) if last_storage_error_at else None,
                 last_storage_error[:500] if last_storage_error else None,
                 dropped_scans, utc_iso(last_drop_at) if last_drop_at else None),
            )

        await self._write(write)

    async def record_error(self, at: datetime, error: str) -> None:
        async def write() -> None:
            await self.db.execute(
                """INSERT INTO squeeze_route_worker_state
                   (id,last_attempt_at,last_success_at,last_error,last_route_id)
                   VALUES (1,?,NULL,?,NULL) ON CONFLICT(id) DO UPDATE SET
                   last_attempt_at=excluded.last_attempt_at,last_error=excluded.last_error""",
                (utc_iso(at), error[:500]),
            )

        await self._write(write)

    async def status(self, *, enabled: bool) -> dict[str, Any]:
        async with self._lock:
            cursor = await self.db.execute("SELECT * FROM squeeze_route_worker_state WHERE id=1")
            row = await cursor.fetchone()
        return {
            "enabled": enabled,
            "source_capability": "research_only_public_rest",
            "last_attempt_at": row["last_attempt_at"] if row else None,
            "last_success_at": row["last_success_at"] if row else None,
            "last_error": row["last_error"] if row else None,
            "last_route_id": row["last_route_id"] if row else None,
            "storage_failure_count": row["storage_failure_count"] if row else 0,
            "last_storage_error_at": row["last_storage_error_at"] if row else None,
            "last_storage_error": row["last_storage_error"] if row else None,
            "dropped_scan_count": row["dropped_scan_count"] if row else 0,
            "last_drop_at": row["last_drop_at"] if row else None,
            "queue_depth": 0,
            "last_collected_at": None,
        }

    async def list_routes(self, *, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
        async with self._lock:
            cursor = await self.db.execute(
                """SELECT l.route_id,l.evaluated_at,l.evaluation_json,s.state_json
                   FROM squeeze_route_latest l LEFT JOIN squeeze_route_state s USING(route_id)
                   ORDER BY l.evaluated_at DESC,l.route_id LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        return [
            {
                "route_id": row["route_id"],
                "evaluated_at": row["evaluated_at"],
                "evaluation": json.loads(row["evaluation_json"]),
                "state": json.loads(row["state_json"]) if row["state_json"] else None,
            }
            for row in rows
        ]

    async def list_events(
        self, *, limit: int = 50, offset: int = 0, include_inputs: bool = False,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            cursor = await self.db.execute(
                """SELECT * FROM squeeze_route_events
                   ORDER BY occurred_at DESC,id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            item = {
                "id": row["id"], "route_id": row["route_id"], "phase": row["phase"],
                "occurred_at": row["occurred_at"], "rule_version": row["rule_version"],
                "evaluation": json.loads(row["evaluation_json"]),
            }
            if include_inputs:
                item["inputs"] = json.loads(row["inputs_json"])
            result.append(item)
        return result
