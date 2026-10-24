from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC

import aiosqlite

from app.models.pair_spread import MAX_PAIR_SPREAD_PRESETS, PairSpreadPreset


class PairSpreadPresetBusyError(RuntimeError):
    pass


class PairSpreadPresetRepository:
    """Use a dedicated connection for file-backed production databases."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db
        self._write_lock = asyncio.Lock()

    async def list(self) -> list[PairSpreadPreset]:
        async with self._write_lock:
            return await self._list_without_lock()

    async def _list_without_lock(self) -> list[PairSpreadPreset]:
        async with self.db.execute(
            "SELECT payload FROM pair_spread_presets ORDER BY saved_at DESC LIMIT ?",
            (MAX_PAIR_SPREAD_PRESETS,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [PairSpreadPreset.model_validate_json(row["payload"]) for row in rows]

    async def get(self, preset_id: str) -> PairSpreadPreset | None:
        async with self._write_lock:
            return await self._get_without_lock(preset_id)

    async def _get_without_lock(self, preset_id: str) -> PairSpreadPreset | None:
        async with self.db.execute(
            "SELECT payload FROM pair_spread_presets WHERE id = ?",
            (preset_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return PairSpreadPreset.model_validate_json(row["payload"]) if row else None

    async def upsert(self, preset: PairSpreadPreset) -> PairSpreadPreset:
        async with self._write_lock:
            async with self._transaction():
                await self._upsert_without_commit(preset)
                await self._prune_without_commit()
                saved = await self._get_without_lock(preset.id)
                if saved is None:
                    raise RuntimeError("failed to save pair spread preset")
            return saved

    async def merge(self, presets: list[PairSpreadPreset]) -> list[PairSpreadPreset]:
        async with self._write_lock:
            async with self._transaction():
                for preset in presets:
                    await self._upsert_without_commit(preset)
                await self._prune_without_commit()
            return await self._list_without_lock()

    async def delete(self, preset_id: str) -> None:
        async with self._write_lock, self._transaction():
            await self.db.execute(
                "DELETE FROM pair_spread_presets WHERE id = ?",
                (preset_id,),
            )

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        try:
            # Acquire the writer before reading, avoiding a stale WAL snapshot upgrade.
            await self.db.execute("BEGIN IMMEDIATE")
            yield
            await self.db.commit()
        except BaseException as exc:
            await self.db.rollback()
            if (
                isinstance(exc, sqlite3.OperationalError)
                and (getattr(exc, "sqlite_errorcode", 0) & 0xFF)
                in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
            ):
                raise PairSpreadPresetBusyError("preset database is busy") from exc
            raise

    async def _upsert_without_commit(self, preset: PairSpreadPreset) -> None:
        saved_at = preset.saved_at.astimezone(UTC).isoformat()
        await self.db.execute(
            """
            INSERT INTO pair_spread_presets (id, payload, saved_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload = excluded.payload,
              saved_at = excluded.saved_at,
              updated_at = CURRENT_TIMESTAMP
            WHERE excluded.saved_at >= pair_spread_presets.saved_at
            """,
            (preset.id, preset.model_dump_json(by_alias=True), saved_at),
        )

    async def _prune_without_commit(self) -> None:
        await self.db.execute(
            """
            DELETE FROM pair_spread_presets
            WHERE id NOT IN (
              SELECT id
              FROM pair_spread_presets
              ORDER BY saved_at DESC
              LIMIT ?
            )
            """,
            (MAX_PAIR_SPREAD_PRESETS,),
        )
