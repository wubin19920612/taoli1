from datetime import UTC, datetime

import aiosqlite

from app.models.pair_monitor import (
    PairMonitorPoint,
    PairMonitorPriceField,
    PairMonitorRule,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class PairMonitorRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create_rule(self, rule: PairMonitorRule) -> PairMonitorRule:
        now = _utc_now()
        stored = rule.model_copy(update={"created_at": now, "updated_at": now})
        await self.db.execute(
            """
            INSERT INTO pair_monitor_rules (id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                stored.id,
                stored.model_dump_json(),
                stored.created_at.isoformat(),
                stored.updated_at.isoformat(),
            ),
        )
        await self.db.commit()
        return stored

    async def upsert_rule(self, rule: PairMonitorRule) -> PairMonitorRule:
        existing = await self.get_rule(rule.id)
        now = _utc_now()
        stored = rule.model_copy(
            update={
                "created_at": existing.created_at if existing is not None else rule.created_at,
                "updated_at": now,
            }
        )
        await self.db.execute(
            """
            INSERT INTO pair_monitor_rules (id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (
                stored.id,
                stored.model_dump_json(),
                stored.created_at.isoformat(),
                stored.updated_at.isoformat(),
            ),
        )
        await self.db.commit()
        return stored

    async def get_rule(self, rule_id: str) -> PairMonitorRule | None:
        cursor = await self.db.execute(
            "SELECT payload FROM pair_monitor_rules WHERE id = ?",
            (rule_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return PairMonitorRule.model_validate_json(row["payload"])

    async def list_rules(self) -> list[PairMonitorRule]:
        cursor = await self.db.execute(
            "SELECT payload FROM pair_monitor_rules ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        return [PairMonitorRule.model_validate_json(row["payload"]) for row in rows]

    async def delete_rule(self, rule_id: str) -> None:
        await self.db.execute("DELETE FROM pair_monitor_points WHERE rule_id = ?", (rule_id,))
        await self.db.execute("DELETE FROM pair_monitor_rules WHERE id = ?", (rule_id,))
        await self.db.commit()

    async def upsert_point(self, point: PairMonitorPoint) -> PairMonitorPoint:
        await self.db.execute(
            """
            INSERT INTO pair_monitor_points (
              rule_id, observed_at, bucket_at,
              leg1_price, leg2_price, spread_abs, spread_pct,
              leg1_funding_rate_pct, leg2_funding_rate_pct,
              leg1_funding_next_rate_pct, leg2_funding_next_rate_pct,
              leg1_funding_next_time, leg2_funding_next_time,
              leg1_volume_24h_usdt, leg2_volume_24h_usdt,
              leg1_price_field, leg2_price_field,
              leg1_market_timestamp, leg2_market_timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rule_id, bucket_at) DO UPDATE SET
              observed_at = excluded.observed_at,
              leg1_price = excluded.leg1_price,
              leg2_price = excluded.leg2_price,
              spread_abs = excluded.spread_abs,
              spread_pct = excluded.spread_pct,
              leg1_funding_rate_pct = excluded.leg1_funding_rate_pct,
              leg2_funding_rate_pct = excluded.leg2_funding_rate_pct,
              leg1_funding_next_rate_pct = excluded.leg1_funding_next_rate_pct,
              leg2_funding_next_rate_pct = excluded.leg2_funding_next_rate_pct,
              leg1_funding_next_time = excluded.leg1_funding_next_time,
              leg2_funding_next_time = excluded.leg2_funding_next_time,
              leg1_volume_24h_usdt = excluded.leg1_volume_24h_usdt,
              leg2_volume_24h_usdt = excluded.leg2_volume_24h_usdt,
              leg1_price_field = excluded.leg1_price_field,
              leg2_price_field = excluded.leg2_price_field,
              leg1_market_timestamp = excluded.leg1_market_timestamp,
              leg2_market_timestamp = excluded.leg2_market_timestamp
            """,
            self._point_values(point),
        )
        await self.db.commit()
        return point

    async def latest_point(self, rule_id: str) -> PairMonitorPoint | None:
        cursor = await self.db.execute(
            """
            SELECT * FROM pair_monitor_points
            WHERE rule_id = ?
            ORDER BY bucket_at DESC
            LIMIT 1
            """,
            (rule_id,),
        )
        row = await cursor.fetchone()
        return self._point_from_db(row) if row is not None else None

    async def list_points(
        self,
        rule_id: str,
        *,
        since: datetime | None = None,
        limit: int = 10_000,
    ) -> list[PairMonitorPoint]:
        clauses = ["rule_id = ?"]
        params: list[object] = [rule_id]
        if since is not None:
            clauses.append("bucket_at >= ?")
            params.append(since.isoformat())
        params.append(limit)
        cursor = await self.db.execute(
            f"""
            SELECT * FROM pair_monitor_points
            WHERE {' AND '.join(clauses)}
            ORDER BY bucket_at ASC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [self._point_from_db(row) for row in rows]

    async def prune_points_before(self, rule_id: str, cutoff: datetime) -> int:
        cursor = await self.db.execute(
            "DELETE FROM pair_monitor_points WHERE rule_id = ? AND bucket_at < ?",
            (rule_id, cutoff.isoformat()),
        )
        deleted = cursor.rowcount if cursor.rowcount is not None else 0
        await self.db.commit()
        return deleted

    async def vacuum(self) -> None:
        await self.db.execute("VACUUM")

    def _point_values(self, point: PairMonitorPoint) -> tuple[object, ...]:
        return (
            point.rule_id,
            point.observed_at.isoformat(),
            point.bucket_at.isoformat(),
            point.leg1_price,
            point.leg2_price,
            point.spread_abs,
            point.spread_pct,
            point.leg1_funding_rate_pct,
            point.leg2_funding_rate_pct,
            point.leg1_funding_next_rate_pct,
            point.leg2_funding_next_rate_pct,
            _serialize_datetime(point.leg1_funding_next_time),
            _serialize_datetime(point.leg2_funding_next_time),
            point.leg1_volume_24h_usdt,
            point.leg2_volume_24h_usdt,
            point.leg1_price_field.value,
            point.leg2_price_field.value,
            _serialize_datetime(point.leg1_market_timestamp),
            _serialize_datetime(point.leg2_market_timestamp),
        )

    def _point_from_db(self, row: aiosqlite.Row) -> PairMonitorPoint:
        return PairMonitorPoint(
            rule_id=row["rule_id"],
            observed_at=row["observed_at"],
            bucket_at=row["bucket_at"],
            leg1_price=row["leg1_price"],
            leg2_price=row["leg2_price"],
            spread_abs=row["spread_abs"],
            spread_pct=row["spread_pct"],
            leg1_funding_rate_pct=row["leg1_funding_rate_pct"],
            leg2_funding_rate_pct=row["leg2_funding_rate_pct"],
            leg1_funding_next_rate_pct=row["leg1_funding_next_rate_pct"],
            leg2_funding_next_rate_pct=row["leg2_funding_next_rate_pct"],
            leg1_funding_next_time=row["leg1_funding_next_time"],
            leg2_funding_next_time=row["leg2_funding_next_time"],
            leg1_volume_24h_usdt=row["leg1_volume_24h_usdt"],
            leg2_volume_24h_usdt=row["leg2_volume_24h_usdt"],
            leg1_price_field=PairMonitorPriceField(row["leg1_price_field"]),
            leg2_price_field=PairMonitorPriceField(row["leg2_price_field"]),
            leg1_market_timestamp=row["leg1_market_timestamp"],
            leg2_market_timestamp=row["leg2_market_timestamp"],
        )
