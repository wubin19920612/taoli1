"""Durable latest attempt for each exact Astro route (not an order ledger)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import aiosqlite


def route_key(pair: dict) -> str:
    # Match _same_route's planned side: name/type encode the market and market
    # types. HL and GC-HL additionally need the DEX on each participating leg.
    # EffectiveHlDex is remote response metadata, not a planned request field.
    identity = {key: pair.get(key) for key in ("name", "type", "buyEx", "sellEx")}
    for exchange, dex in (("buyEx", "aHlDex"), ("sellEx", "bHlDex")):
        if pair.get(exchange) in {"hl", "gc-hl"}:
            identity[dex] = str(pair.get(dex) or "main").strip().lower()
    return json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class AstroCardAttemptRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db
        self._lock = asyncio.Lock()
        self._cleanup_date = None

    async def get(self, key: str) -> dict | None:
        async with self._lock, self.db.execute(
            "SELECT * FROM astro_card_attempts WHERE route_key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._record(row) if row else None

    async def list_attempts(self, limit: int = 100, state: str | None = None) -> list[dict]:
        query = "SELECT * FROM astro_card_attempts"
        parameters: list = []
        if state:
            query += " WHERE state = ?"
            parameters.append(state)
        query += " ORDER BY updated_at DESC, route_key LIMIT ?"
        parameters.append(limit)
        async with self._lock, self.db.execute(query, parameters) as cursor:
            rows = await cursor.fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _record(row) -> dict:
        record = dict(row)
        record["open_enabled"] = bool(record["open_enabled"])
        return record

    async def mark_pending(self, planned: dict, source: str, open_enabled: bool) -> None:
        # AstroPairPlanner constructs only card parameters. Authentication is
        # added separately in AstroSdkClient headers and is never in this dict.
        payload = json.dumps(planned, ensure_ascii=False, sort_keys=True)
        now = datetime.now(UTC)
        async with self._lock:
            try:
                if self._cleanup_date != now.date():
                    await self.db.execute(
                        "DELETE FROM astro_card_attempts WHERE state IN ('created','failed','reconciled') "
                        "AND updated_at < ?",
                        ((now - timedelta(days=30)).isoformat(),),
                    )
                await self.db.execute(
                    """INSERT INTO astro_card_attempts
                       (route_key, pair_name, source, state, open_enabled, request_json,
                        result_message, created_at, updated_at)
                       VALUES (?, ?, ?, 'pending', ?, ?, '', ?, ?)
                       ON CONFLICT(route_key) DO UPDATE SET
                         pair_name=excluded.pair_name, source=excluded.source, state='pending',
                         open_enabled=excluded.open_enabled, request_json=excluded.request_json,
                         result_message='', created_at=excluded.created_at, updated_at=excluded.updated_at""",
                    (route_key(planned), planned["name"], source, open_enabled, payload,
                     now.isoformat(), now.isoformat()),
                )
                await self.db.commit()
                self._cleanup_date = now.date()
            except BaseException:
                await self.db.rollback()
                raise

    async def mark(self, key: str, state: str, message: str) -> None:
        async with self._lock:
            try:
                await self.db.execute(
                    "UPDATE astro_card_attempts SET state=?, result_message=?, updated_at=? WHERE route_key=?",
                    (state, message, datetime.now(UTC).isoformat(), key),
                )
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise
