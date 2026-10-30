from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite


@dataclass(frozen=True)
class AlertDeliveryState:
    key: str
    last_sent_at: datetime | None = None
    retry_after: datetime | None = None
    retry_attempts: int = 0
    pending_since: datetime | None = None


def _timestamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


class AlertDeliveryStore:
    """Delivery checkpoints on the application's shared database connection."""

    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def load_all(self) -> list[AlertDeliveryState]:
        async with self.db.execute("SELECT * FROM alert_delivery_state") as cursor:
            rows = await cursor.fetchall()
        return [
            AlertDeliveryState(
                key=row["key"],
                last_sent_at=datetime.fromisoformat(row["last_sent_at"]) if row["last_sent_at"] else None,
                retry_after=datetime.fromisoformat(row["retry_after"]) if row["retry_after"] else None,
                retry_attempts=row["retry_attempts"],
                pending_since=datetime.fromisoformat(row["pending_since"]) if row["pending_since"] else None,
            )
            for row in rows
        ]

    async def mark_pending(self, key: str, now: datetime) -> None:
        await self.db.execute(
            """
            INSERT INTO alert_delivery_state (key, pending_since, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              pending_since = excluded.pending_since, updated_at = excluded.updated_at
            """,
            (key, _timestamp(now), _timestamp(now)),
        )
        await self.db.commit()

    async def record(
        self,
        key: str,
        last_sent_at: datetime | None,
        retry_after: datetime | None,
        retry_attempts: int,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO alert_delivery_state
              (key, last_sent_at, retry_after, retry_attempts, pending_since, updated_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            ON CONFLICT(key) DO UPDATE SET
              last_sent_at = excluded.last_sent_at, retry_after = excluded.retry_after,
              retry_attempts = excluded.retry_attempts, pending_since = NULL,
              updated_at = excluded.updated_at
            """,
            (key, _timestamp(last_sent_at), _timestamp(retry_after), retry_attempts,
             _timestamp(datetime.now(UTC))),
        )
        await self.db.commit()

    async def prune(self, older_than: datetime) -> None:
        await self.db.execute(
            "DELETE FROM alert_delivery_state WHERE updated_at < ? AND pending_since IS NULL",
            (_timestamp(older_than),),
        )
        await self.db.commit()
