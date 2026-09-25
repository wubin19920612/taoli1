from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

import aiosqlite

from .liquidation import LiquidationUpdate
from .models import HourCandle, PositionSample, WatchFeatures, utc_iso


async def initialize_squeeze_schema(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS squeeze_market_identity (
          market_key TEXT PRIMARY KEY, exchange TEXT NOT NULL, market_type TEXT NOT NULL,
          raw_symbol TEXT NOT NULL, dex TEXT NOT NULL DEFAULT '', base_asset TEXT,
          quote_asset TEXT, contract_size REAL, price_multiplier REAL,
          verification_source TEXT NOT NULL, verification_status TEXT NOT NULL,
          verified_at TEXT NOT NULL, metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS squeeze_scan_runs (
          market_key TEXT NOT NULL, bucket_at TEXT NOT NULL, requested_from TEXT NOT NULL,
          requested_to TEXT NOT NULL, received_at TEXT NOT NULL, candle_count INTEGER NOT NULL,
          positioning_count INTEGER NOT NULL, result_status TEXT NOT NULL, error TEXT,
          PRIMARY KEY (market_key, bucket_at)
        );
        CREATE TABLE IF NOT EXISTS squeeze_hourly_candles (
          market_key TEXT NOT NULL, event_time TEXT NOT NULL, close REAL NOT NULL,
          quote_volume REAL NOT NULL, received_at TEXT NOT NULL, available_at TEXT NOT NULL,
          source TEXT NOT NULL, PRIMARY KEY (market_key, event_time)
        );
        CREATE TABLE IF NOT EXISTS squeeze_positioning_samples (
          market_key TEXT NOT NULL, event_time TEXT NOT NULL, raw_open_interest REAL NOT NULL,
          raw_unit TEXT NOT NULL, open_interest_usdt REAL, account_ratio REAL,
          account_ratio_event_time TEXT,
          received_at TEXT NOT NULL, available_at TEXT NOT NULL, source TEXT NOT NULL,
          sampling_period_seconds INTEGER NOT NULL,
          PRIMARY KEY (market_key, source, event_time, sampling_period_seconds)
        );
        CREATE TABLE IF NOT EXISTS squeeze_feature_results (
          market_key TEXT NOT NULL, bucket_at TEXT NOT NULL, calculated_at TEXT NOT NULL,
          status TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY (market_key, bucket_at)
        );
        CREATE TABLE IF NOT EXISTS squeeze_watch_events (
          id TEXT PRIMARY KEY, market_key TEXT NOT NULL, rule_version TEXT NOT NULL,
          first_bucket_at TEXT NOT NULL, last_bucket_at TEXT NOT NULL, stage TEXT NOT NULL,
          expires_at TEXT NOT NULL, cooldown_until TEXT NOT NULL, features_json TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_squeeze_watch_active
          ON squeeze_watch_events(market_key, expires_at DESC);
        CREATE TABLE IF NOT EXISTS squeeze_liquidation_updates (
          identity TEXT PRIMARY KEY, market_key TEXT NOT NULL, event_time TEXT NOT NULL,
          received_at TEXT NOT NULL, side TEXT NOT NULL, cumulative_filled_qty REAL NOT NULL,
          observed_liquidation_notional REAL NOT NULL, source TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_squeeze_liquidation_time
          ON squeeze_liquidation_updates(market_key, event_time DESC);
        CREATE TABLE IF NOT EXISTS squeeze_liquidation_coverage (
          id INTEGER PRIMARY KEY CHECK (id = 1), state TEXT NOT NULL,
          updated_at TEXT NOT NULL, last_message_at TEXT, gap_started_at TEXT,
          last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS squeeze_liquidation_gaps (
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
          ended_at TEXT, reason TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS squeeze_scan_state (
          id INTEGER PRIMARY KEY CHECK (id = 1), last_attempt_at TEXT,
          last_success_at TEXT, last_bucket_at TEXT, last_error TEXT,
          verified_symbols_json TEXT NOT NULL DEFAULT '[]'
        );
    """)
    cursor = await db.execute("PRAGMA table_info(squeeze_positioning_samples)")
    columns = {row["name"] for row in await cursor.fetchall()}
    if "account_ratio_event_time" not in columns:
        await db.execute(
            "ALTER TABLE squeeze_positioning_samples ADD COLUMN account_ratio_event_time TEXT"
        )
    from .route_repository import initialize_route_schema

    await initialize_route_schema(db)
    await db.commit()


def _features_json(features: WatchFeatures) -> str:
    return json.dumps(asdict(features), default=utc_iso, separators=(",", ":"))


class SqueezeRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db
        self._lock = asyncio.Lock()

    async def save_market_identity(
        self, raw_symbol: str, metadata: dict[str, Any], verified_at: datetime
    ) -> None:
        from .models import market_key

        key = market_key(raw_symbol)
        contract_size = metadata.get("contractSize")
        try:
            contract_size = float(contract_size) if contract_size is not None else None
        except (TypeError, ValueError):
            contract_size = None
        if contract_size is not None and contract_size <= 0:
            contract_size = None
        await self.db.execute(
            """INSERT INTO squeeze_market_identity
               (market_key,exchange,market_type,raw_symbol,dex,base_asset,quote_asset,
                contract_size,price_multiplier,verification_source,verification_status,
                verified_at,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(market_key) DO UPDATE SET
                 base_asset=excluded.base_asset,quote_asset=excluded.quote_asset,
                 contract_size=excluded.contract_size,price_multiplier=excluded.price_multiplier,
                 verification_status=excluded.verification_status,verified_at=excluded.verified_at,
                 metadata_json=excluded.metadata_json""",
            (key, "binance", "future", raw_symbol, "", metadata.get("baseAsset"),
             metadata.get("quoteAsset"), contract_size, None,
             "binance_fapi_exchangeInfo", "verified_for_structure", utc_iso(verified_at),
             json.dumps(metadata, separators=(",", ":"))),
        )
        await self.db.commit()

    async def record_scan_market(
        self, market_key: str, bucket_at: datetime, at: datetime,
        *, candle_count: int, positioning_count: int,
        result_status: str, error: str | None = None,
    ) -> None:
        await self.db.execute(
            """INSERT INTO squeeze_scan_runs
               (market_key,bucket_at,requested_from,requested_to,received_at,candle_count,
                positioning_count,result_status,error) VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(market_key,bucket_at) DO UPDATE SET
                 received_at=excluded.received_at,candle_count=excluded.candle_count,
                 positioning_count=excluded.positioning_count,
                 result_status=excluded.result_status,error=excluded.error""",
            (market_key, utc_iso(bucket_at), utc_iso(bucket_at - timedelta(hours=171)),
             utc_iso(bucket_at), utc_iso(at), candle_count, positioning_count,
             result_status, error),
        )
        await self.db.commit()

    async def save_market_data(
        self, candles: list[HourCandle], positioning: list[PositionSample]
    ) -> None:
        async with self._lock:
            await self.db.executemany(
                """INSERT OR IGNORE INTO squeeze_hourly_candles
                   (market_key,event_time,close,quote_volume,received_at,available_at,source)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    (row.market_key, utc_iso(row.event_time), row.close, row.quote_volume,
                     utc_iso(row.received_at), utc_iso(row.available_at), row.source)
                    for row in candles
                ],
            )
            await self.db.executemany(
                """INSERT INTO squeeze_positioning_samples
                   (market_key,event_time,raw_open_interest,raw_unit,open_interest_usdt,
                    account_ratio,account_ratio_event_time,received_at,available_at,source,sampling_period_seconds)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market_key,source,event_time,sampling_period_seconds) DO UPDATE SET
                     account_ratio=COALESCE(excluded.account_ratio,account_ratio),
                     account_ratio_event_time=COALESCE(excluded.account_ratio_event_time,account_ratio_event_time),
                     available_at=CASE WHEN account_ratio IS NULL AND excluded.account_ratio IS NOT NULL
                                       THEN excluded.available_at ELSE available_at END""",
                [
                    (row.market_key, utc_iso(row.event_time), row.raw_open_interest,
                     row.raw_unit, row.open_interest_usdt, row.account_ratio,
                     utc_iso(row.account_ratio_event_time) if row.account_ratio_event_time else None,
                     utc_iso(row.received_at), utc_iso(row.available_at), row.source,
                     row.sampling_period_seconds)
                    for row in positioning
                ],
            )
            await self.db.commit()

    async def load_market_data(
        self, market_key: str, bucket_at: datetime
    ) -> tuple[list[HourCandle], list[PositionSample]]:
        candle_start = utc_iso(bucket_at - timedelta(hours=171))
        cursor = await self.db.execute(
            """SELECT * FROM squeeze_hourly_candles
               WHERE market_key=? AND event_time BETWEEN ? AND ? ORDER BY event_time""",
            (market_key, candle_start, utc_iso(bucket_at)),
        )
        candles = [
            HourCandle(
                row["market_key"], datetime.fromisoformat(row["event_time"]), row["close"],
                row["quote_volume"], datetime.fromisoformat(row["received_at"]),
                datetime.fromisoformat(row["available_at"]), row["source"]
            ) for row in await cursor.fetchall()
        ]
        cursor = await self.db.execute(
            """SELECT * FROM squeeze_positioning_samples
               WHERE market_key=? AND event_time BETWEEN ? AND ? ORDER BY event_time""",
            (market_key, utc_iso(bucket_at - timedelta(hours=25)), utc_iso(bucket_at)),
        )
        positioning = [
            PositionSample(
                row["market_key"], datetime.fromisoformat(row["event_time"]),
                row["raw_open_interest"], row["raw_unit"], row["open_interest_usdt"],
                row["account_ratio"], datetime.fromisoformat(row["received_at"]),
                datetime.fromisoformat(row["available_at"]), row["source"],
                row["sampling_period_seconds"],
                datetime.fromisoformat(row["account_ratio_event_time"])
                if row["account_ratio_event_time"] else None,
            ) for row in await cursor.fetchall()
        ]
        return candles, positioning

    async def save_features(self, features: WatchFeatures, *, max_active: int = 30) -> bool:
        payload = _features_json(features)
        now = features.calculated_at
        async with self._lock:
            await self.db.execute(
                """INSERT INTO squeeze_feature_results
                   (market_key,bucket_at,calculated_at,status,payload) VALUES (?,?,?,?,?)
                   ON CONFLICT(market_key,bucket_at) DO UPDATE SET
                     calculated_at=excluded.calculated_at,status=excluded.status,payload=excluded.payload""",
                (features.market_key, utc_iso(features.bucket_at), utc_iso(now),
                 features.status, payload),
            )
            cursor = await self.db.execute(
                """SELECT * FROM squeeze_watch_events WHERE market_key=? AND expires_at>?
                   ORDER BY first_bucket_at DESC LIMIT 1""",
                (features.market_key, utc_iso(now)),
            )
            active = await cursor.fetchone()
            if active and features.status == "ready":
                await self.db.execute(
                    """UPDATE squeeze_watch_events SET last_bucket_at=?,stage=?,features_json=?,updated_at=?
                       WHERE id=? AND last_bucket_at<=?""",
                    (utc_iso(features.bucket_at), features.stage, payload, utc_iso(now),
                     active["id"], utc_iso(features.bucket_at)),
                )
            elif active is None and features.qualifies:
                cursor = await self.db.execute(
                    "SELECT COUNT(*) AS n FROM squeeze_watch_events WHERE expires_at>?",
                    (utc_iso(now),),
                )
                count = (await cursor.fetchone())["n"]
                if count >= max_active:
                    await self.db.commit()
                    return False
                from .models import RULE_VERSION

                event_id = sha256(
                    f"{RULE_VERSION}|{features.market_key}|{utc_iso(features.bucket_at)}".encode()
                ).hexdigest()[:32]
                await self.db.execute(
                    """INSERT OR IGNORE INTO squeeze_watch_events
                       (id,market_key,rule_version,first_bucket_at,last_bucket_at,stage,
                        expires_at,cooldown_until,features_json,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (event_id, features.market_key, RULE_VERSION, utc_iso(features.bucket_at),
                     utc_iso(features.bucket_at), features.stage,
                     utc_iso(now + timedelta(hours=72)), utc_iso(now + timedelta(hours=48)),
                     payload, utc_iso(now), utc_iso(now)),
                )
            await self.db.commit()
        return True

    async def record_liquidation(self, update: LiquidationUpdate) -> float:
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT cumulative_filled_qty FROM squeeze_liquidation_updates WHERE identity=?",
                (update.identity,),
            )
            previous = await cursor.fetchone()
            previous_qty = previous["cumulative_filled_qty"] if previous else 0.0
            delta_qty = max(0.0, update.cumulative_filled_qty - previous_qty)
            if previous is None:
                await self.db.execute(
                    """INSERT INTO squeeze_liquidation_updates
                       (identity,market_key,event_time,received_at,side,cumulative_filled_qty,
                        observed_liquidation_notional,source) VALUES (?,?,?,?,?,?,?,?)""",
                    (update.identity, update.market_key, utc_iso(update.event_time),
                     utc_iso(update.received_at), update.side, update.cumulative_filled_qty,
                     delta_qty * update.average_price, update.source),
                )
            elif delta_qty > 0:
                await self.db.execute(
                    """UPDATE squeeze_liquidation_updates SET cumulative_filled_qty=?,
                       observed_liquidation_notional=observed_liquidation_notional+?,received_at=?
                       WHERE identity=?""",
                    (update.cumulative_filled_qty, delta_qty * update.average_price,
                     utc_iso(update.received_at), update.identity),
                )
            await self.db.commit()
        return delta_qty * update.average_price

    async def set_coverage(
        self, state: str, at: datetime, *, last_message_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT state,gap_started_at FROM squeeze_liquidation_coverage WHERE id=1"
            )
            previous = await cursor.fetchone()
            if state == "disconnected" and (previous is None or previous["state"] != "disconnected"):
                await self.db.execute(
                    "INSERT INTO squeeze_liquidation_gaps(started_at,reason) VALUES (?,?)",
                    (utc_iso(at), error or "stream_disconnected"),
                )
            if state == "throttled_public_stream" and previous and previous["state"] == "disconnected":
                await self.db.execute(
                    """UPDATE squeeze_liquidation_gaps SET ended_at=?
                       WHERE id=(SELECT MAX(id) FROM squeeze_liquidation_gaps) AND ended_at IS NULL""",
                    (utc_iso(at),),
                )
            await self.db.execute(
                """INSERT INTO squeeze_liquidation_coverage
                   (id,state,updated_at,last_message_at,gap_started_at,last_error)
                   VALUES (1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   state=excluded.state,updated_at=excluded.updated_at,
                   last_message_at=COALESCE(excluded.last_message_at,last_message_at),
                   gap_started_at=excluded.gap_started_at,last_error=excluded.last_error""",
                (state, utc_iso(at), utc_iso(last_message_at) if last_message_at else None,
                 utc_iso(at) if state == "disconnected" else None, error),
            )
            await self.db.commit()

    async def set_scan_state(
        self, at: datetime, *, bucket_at: datetime | None = None,
        symbols: list[str] | None = None, error: str | None = None,
    ) -> None:
        await self.db.execute(
            """INSERT INTO squeeze_scan_state
               (id,last_attempt_at,last_success_at,last_bucket_at,last_error,verified_symbols_json)
               VALUES (1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               last_attempt_at=excluded.last_attempt_at,
               last_success_at=COALESCE(excluded.last_success_at,last_success_at),
               last_bucket_at=COALESCE(excluded.last_bucket_at,last_bucket_at),
               last_error=excluded.last_error,
               verified_symbols_json=CASE WHEN excluded.last_success_at IS NULL
                   THEN verified_symbols_json ELSE excluded.verified_symbols_json END""",
            (utc_iso(at), utc_iso(at) if bucket_at else None,
             utc_iso(bucket_at) if bucket_at else None, error, json.dumps(symbols or [])),
        )
        await self.db.commit()

    async def status(self, *, enabled: bool, now: datetime) -> dict[str, Any]:
        cursor = await self.db.execute("SELECT * FROM squeeze_scan_state WHERE id=1")
        scan = await cursor.fetchone()
        cursor = await self.db.execute("SELECT * FROM squeeze_liquidation_coverage WHERE id=1")
        coverage = await cursor.fetchone()
        cursor = await self.db.execute(
            "SELECT COUNT(*) AS n FROM squeeze_watch_events WHERE expires_at>?", (utc_iso(now),)
        )
        active = (await cursor.fetchone())["n"]
        cursor = await self.db.execute(
            """SELECT side,COUNT(*) AS updates,SUM(observed_liquidation_notional) AS notional
               FROM squeeze_liquidation_updates WHERE event_time>=? GROUP BY side""",
            (utc_iso(now - timedelta(hours=1)),),
        )
        liquidation = {
            row["side"]: {"updates": row["updates"], "observed_notional_usdt": row["notional"]}
            for row in await cursor.fetchall()
        }
        cursor = await self.db.execute(
            "SELECT COUNT(*) AS n FROM squeeze_liquidation_gaps WHERE ended_at IS NULL"
        )
        open_gaps = (await cursor.fetchone())["n"]
        cursor = await self.db.execute(
            """SELECT market_key,bucket_at,requested_from,requested_to,received_at,
                      candle_count,positioning_count,result_status,error
               FROM squeeze_scan_runs WHERE (market_key,bucket_at) IN
               (SELECT market_key,MAX(bucket_at) FROM squeeze_scan_runs GROUP BY market_key)
               ORDER BY market_key LIMIT 5"""
        )
        latest_scans = [dict(row) for row in await cursor.fetchall()]
        return {
            "enabled": enabled,
            "rule_version": "squeeze-watch-s1-v1",
            "last_attempt_at": scan["last_attempt_at"] if scan else None,
            "last_success_at": scan["last_success_at"] if scan else None,
            "last_bucket_at": scan["last_bucket_at"] if scan else None,
            "last_error": scan["last_error"] if scan else None,
            "verified_symbols": json.loads(scan["verified_symbols_json"]) if scan else [],
            "active_watch_count": active,
            "liquidation_coverage": {
                "state": coverage["state"] if coverage else "not_started",
                "updated_at": coverage["updated_at"] if coverage else None,
                "last_message_at": coverage["last_message_at"] if coverage else None,
                "last_error": coverage["last_error"] if coverage else None,
                "open_gaps": open_gaps,
                "public_stream_complete": False,
            },
            "liquidation_observed_last_hour": liquidation,
            "latest_market_scans": latest_scans,
        }

    async def list_events(
        self, *, now: datetime, active_only: bool = False,
        limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]:
        where = "WHERE expires_at>?" if active_only else ""
        params = (utc_iso(now), limit, offset) if active_only else (limit, offset)
        cursor = await self.db.execute(
            f"""SELECT * FROM squeeze_watch_events {where}
                ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""", params,
        )
        return [
            {
                "id": row["id"], "market_key": row["market_key"],
                "rule_version": row["rule_version"], "first_bucket_at": row["first_bucket_at"],
                "last_bucket_at": row["last_bucket_at"], "stage": row["stage"],
                "expires_at": row["expires_at"], "cooldown_until": row["cooldown_until"],
                "features": json.loads(row["features_json"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"],
            }
            for row in await cursor.fetchall()
        ]

    async def prune_samples(self, now: datetime) -> None:
        async with self._lock:
            for table, column, days in (
                ("squeeze_hourly_candles", "event_time", 10),
                ("squeeze_positioning_samples", "event_time", 10),
                ("squeeze_liquidation_updates", "event_time", 7),
                ("squeeze_feature_results", "bucket_at", 14),
                ("squeeze_scan_runs", "bucket_at", 14),
            ):
                await self.db.execute(
                    f"DELETE FROM {table} WHERE {column}<?",
                    (utc_iso(now - timedelta(days=days)),),
                )
            await self.db.commit()
