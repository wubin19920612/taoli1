from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from app.models.market_identity import (
    BindingMutation,
    MarketBinding,
    MarketKey,
    MarketValidationSettings,
)


async def initialize_market_identity_schema(db):
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS asset_identities (
            asset_id TEXT PRIMARY KEY, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_identity_bindings (
            market_key TEXT PRIMARY KEY, revision INTEGER NOT NULL,
            identity_status TEXT NOT NULL, asset_id TEXT, payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_market_identity_asset ON market_identity_bindings(asset_id);
        CREATE TABLE IF NOT EXISTS market_identity_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, market_key TEXT NOT NULL,
            old_revision INTEGER NOT NULL, new_revision INTEGER NOT NULL,
            reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_validation_incidents (
            id TEXT PRIMARY KEY, market_key TEXT NOT NULL, reason_code TEXT NOT NULL,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, state TEXT NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_validation_incident_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL,
            state TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_validation_config (
            id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_invalid_opportunities (
            opportunity_id TEXT PRIMARY KEY, reason TEXT NOT NULL, observed_at TEXT NOT NULL
        );
    """)
    await db.commit()


class MarketIdentityRepository:
    def __init__(self, db):
        self.db = db
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _write_transaction(self):
        async with self._lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                await self.db.rollback()
                raise
            else:
                await self.db.commit()

    async def list_bindings(self):
        cursor = await self.db.execute(
            "SELECT payload FROM market_identity_bindings ORDER BY market_key"
        )
        return [MarketBinding.model_validate_json(row[0]) for row in await cursor.fetchall()]

    async def settings(self):
        cursor = await self.db.execute("SELECT payload FROM market_validation_config WHERE id=1")
        row = await cursor.fetchone()
        return (
            MarketValidationSettings.model_validate_json(row[0])
            if row
            else MarketValidationSettings()
        )

    async def save_settings(self, settings):
        async with self._write_transaction():
            await self.db.execute(
                "INSERT INTO market_validation_config VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (settings.model_dump_json(),),
            )
            await self.db.commit()
        return settings

    async def save_binding(self, mutation: BindingMutation, actor="dashboard"):
        async with self._write_transaction():
            key = mutation.binding.market.key
            # A single conditional UPDATE provides cross-process revision safety.
            cursor = await self.db.execute(
                "SELECT revision,payload FROM market_identity_bindings WHERE market_key=?", (key,)
            )
            existing = await cursor.fetchone()
            revision = existing[0] if existing else 0
            if revision != mutation.expected_revision:
                raise ValueError("binding revision changed; reload before saving")
            saved = mutation.binding.model_copy(update={"revision": revision + 1})
            if existing:
                cursor = await self.db.execute(
                    "UPDATE market_identity_bindings SET revision=?,identity_status=?,asset_id=?,payload=? WHERE market_key=? AND revision=?",
                    (
                        saved.revision,
                        saved.identity_status,
                        saved.asset_id,
                        saved.model_dump_json(),
                        key,
                        revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("binding revision changed")
            else:
                cursor = await self.db.execute(
                    "INSERT OR IGNORE INTO market_identity_bindings VALUES(?,?,?,?,?)",
                    (
                        key,
                        saved.revision,
                        saved.identity_status,
                        saved.asset_id,
                        saved.model_dump_json(),
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("binding revision changed")
            if saved.asset_id:
                asset = {
                    "asset_id": saved.asset_id,
                    "display_name": saved.display_name,
                    "asset_kind": saved.asset_kind,
                }
                await self.db.execute(
                    "INSERT OR IGNORE INTO asset_identities VALUES(?,?)",
                    (saved.asset_id, json.dumps(asset)),
                )
            await self.db.execute(
                "INSERT INTO market_identity_audit(market_key,old_revision,new_revision,reason,actor,created_at,payload) VALUES(?,?,?,?,?,?,?)",
                (
                    key,
                    revision,
                    saved.revision,
                    mutation.reason,
                    actor,
                    datetime.now(UTC).isoformat(),
                    json.dumps(
                        {
                            "before": json.loads(existing[1]) if existing else None,
                            "after": saved.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            await self.db.commit()
            return saved

    async def discover(self, markets):
        items = []
        for row in markets:
            try:
                key = MarketKey.from_snapshot(row)
            except ValueError:
                continue
            pending = MarketBinding(
                market=key,
                display_name=row.base,
                asset_kind=row.asset_kind or "crypto",
                quote_currency=row.original_quote_currency or row.quote,
            )
            items.append((key.key, 1, "pending", None, pending.model_dump_json()))
        if items:
            async with self._write_transaction():
                await self.db.executemany(
                    "INSERT OR IGNORE INTO market_identity_bindings VALUES(?,?,?,?,?)", items
                )
                await self.db.commit()

    async def import_catalog(self, bindings):
        """Batch evidence-backed imports; never replace dashboard decisions."""
        from datetime import timedelta

        from app.models.market_identity import utc

        now = datetime.now(UTC)
        async with self._write_transaction():
            cursor = await self.db.execute(
                "SELECT market_key,revision,payload FROM market_identity_bindings"
            )
            existing = {
                row[0]: (row[1], MarketBinding.model_validate_json(row[2]))
                for row in await cursor.fetchall()
            }
            cursor = await self.db.execute(
                "SELECT market_key,actor FROM market_identity_audit WHERE id IN (SELECT MAX(id) FROM market_identity_audit GROUP BY market_key)"
            )
            actors = {row[0]: row[1] for row in await cursor.fetchall()}
            writes, audits = [], []
            for item in bindings:
                key = item.market.key
                revision, old = existing.get(key, (0, None))
                if actors.get(key) not in {None, "official_catalog"}:
                    continue
                if old and old.identity_status in {"conflict", "expired"}:
                    continue
                if old and old.identity_status == "verified":
                    if old.fingerprint != item.fingerprint:
                        item = old.model_copy(
                            update={
                                "identity_status": "conflict",
                                "unit_status": "pending",
                                "evidence": [*old.evidence, *item.evidence][:20],
                            }
                        )
                    elif old.valid_until and utc(old.valid_until) > now + timedelta(days=1):
                        continue
                saved = item.model_copy(update={"revision": revision + 1})
                writes.append(
                    (
                        key,
                        saved.revision,
                        saved.identity_status,
                        saved.asset_id,
                        saved.model_dump_json(),
                    )
                )
                audits.append(
                    (
                        key,
                        revision,
                        saved.revision,
                        "官方固定来源核验或规格冲突",
                        "official_catalog",
                        now.isoformat(),
                        json.dumps(
                            {
                                "before": old.model_dump(mode="json") if old else None,
                                "after": saved.model_dump(mode="json"),
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
                existing[key] = (saved.revision, saved)
            if writes:
                await self.db.executemany(
                    "INSERT INTO market_identity_bindings VALUES(?,?,?,?,?) ON CONFLICT(market_key) DO UPDATE SET revision=excluded.revision,identity_status=excluded.identity_status,asset_id=excluded.asset_id,payload=excluded.payload",
                    writes,
                )
                assets = {
                    item.asset_id: {
                        "asset_id": item.asset_id,
                        "display_name": item.display_name,
                        "asset_kind": item.asset_kind,
                    }
                    for item in bindings
                    if item.asset_id
                }
                await self.db.executemany(
                    "INSERT OR IGNORE INTO asset_identities VALUES(?,?)",
                    [(key, json.dumps(value)) for key, value in assets.items()],
                )
                await self.db.executemany(
                    "INSERT INTO market_identity_audit(market_key,old_revision,new_revision,reason,actor,created_at,payload) VALUES(?,?,?,?,?,?,?)",
                    audits,
                )
                await self.db.commit()
            return len(writes)

    async def audit_events(self, limit=100):
        cursor = await self.db.execute(
            "SELECT * FROM market_identity_audit ORDER BY id DESC LIMIT ?",
            (min(max(limit, 1), 500),),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def invalidate_opportunities(self, identities):
        if not identities:
            return
        now = datetime.now(UTC).isoformat()
        async with self._write_transaction():
            await self.db.executemany(
                "INSERT OR IGNORE INTO market_invalid_opportunities VALUES(?,?,?)",
                [(value, "DIFFERENT_UNDERLYING_ASSET", now) for value in identities],
            )
            await self.db.commit()

    async def record_incident(self, market_key, code, payload, state="open"):
        key = hashlib.sha256(json.dumps([market_key, code]).encode()).hexdigest()[:32]
        now = datetime.now(UTC).isoformat()
        async with self._write_transaction():
            cursor = await self.db.execute(
                "SELECT state FROM market_validation_incidents WHERE id=?", (key,)
            )
            old = await cursor.fetchone()
            if old is None or old[0] != state:
                await self.db.execute(
                    "INSERT INTO market_validation_incident_events(incident_id,state,observed_at,payload) VALUES(?,?,?,?)",
                    (key, state, now, json.dumps(payload, ensure_ascii=False)),
                )
            await self.db.execute(
                "INSERT INTO market_validation_incidents VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,state=excluded.state,occurrences=occurrences+1,payload=excluded.payload",
                (
                    key,
                    market_key,
                    code,
                    now,
                    now,
                    state,
                    1,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            await self.db.commit()

    async def incidents(self, limit=100):
        cursor = await self.db.execute(
            "SELECT * FROM market_validation_incidents ORDER BY last_seen DESC LIMIT ?",
            (min(max(limit, 1), 500),),
        )
        return [dict(row) for row in await cursor.fetchall()]
