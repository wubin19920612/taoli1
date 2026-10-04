from __future__ import annotations

import asyncio
from datetime import UTC

import aiosqlite

from app.models.pair_spread import MAX_PAIR_SPREAD_PRESETS, PairSpreadPreset


class PairSpreadPresetRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db
        self._write_lock = asyncio.Lock()

    async def list(self) -> list[PairSpreadPreset]:
        cursor = await self.db.execute(
            "SELECT payload FROM pair_spread_presets ORDER BY saved_at DESC LIMIT ?",
            (MAX_PAIR_SPREAD_PRESETS,),
        )
        rows = await cursor.fetchall()
        return [PairSpreadPreset.model_validate_json(row["payload"]) for row in rows]

    async def get(self, preset_id: str) -> PairSpreadPreset | None:
        cursor = await self.db.execute(
            "SELECT payload FROM pair_spread_presets WHERE id = ?",
            (preset_id,),
        )
        row = await cursor.fetchone()
        return PairSpreadPreset.model_validate_json(row["payload"]) if row else None

    async def upsert(self, preset: PairSpreadPreset) -> PairSpreadPreset:
        async with self._write_lock:
            await self._upsert_without_commit(preset)
            await self._prune_without_commit()
            await self.db.commit()
        saved = await self.get(preset.id)
        if saved is None:
            raise RuntimeError("failed to save pair spread preset")
        return saved

    async def merge(self, presets: list[PairSpreadPreset]) -> list[PairSpreadPreset]:
        async with self._write_lock:
            for preset in presets:
                await self._upsert_without_commit(preset)
            await self._prune_without_commit()
            await self.db.commit()
        return await self.list()

    async def delete(self, preset_id: str) -> None:
        async with self._write_lock:
            await self.db.execute(
                "DELETE FROM pair_spread_presets WHERE id = ?",
                (preset_id,),
            )
            await self.db.commit()

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
