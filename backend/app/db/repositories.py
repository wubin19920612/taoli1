import json

import aiosqlite

from app.models.alert import AlertEvent, AlertRule
from app.models.settings import RiskSettings


class AlertRuleRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create(self, rule: AlertRule) -> AlertRule:
        payload = rule.model_dump_json()
        await self.db.execute(
            "INSERT INTO alert_rules (id, payload) VALUES (?, ?)",
            (rule.id, payload),
        )
        await self.db.commit()
        return rule

    async def list(self) -> list[AlertRule]:
        cursor = await self.db.execute("SELECT payload FROM alert_rules ORDER BY created_at")
        rows = await cursor.fetchall()
        return [AlertRule.model_validate_json(row["payload"]) for row in rows]

    async def get(self, rule_id: str) -> AlertRule | None:
        cursor = await self.db.execute("SELECT payload FROM alert_rules WHERE id = ?", (rule_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return AlertRule.model_validate_json(row["payload"])

    async def upsert(self, rule: AlertRule) -> AlertRule:
        payload = rule.model_dump_json()
        await self.db.execute(
            """
            INSERT INTO alert_rules (id, payload, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = CURRENT_TIMESTAMP
            """,
            (rule.id, payload),
        )
        await self.db.commit()
        return rule

    async def delete(self, rule_id: str) -> None:
        await self.db.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
        await self.db.commit()


class AlertEventRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create(self, event: AlertEvent) -> AlertEvent:
        await self.db.execute(
            """
            INSERT INTO alert_events (id, rule_id, opportunity_id, symbol, status, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.rule_id,
                event.opportunity_id,
                event.symbol,
                event.status,
                event.message,
                event.created_at.isoformat(),
            ),
        )
        await self.db.commit()
        return event

    async def list(self, limit: int = 100) -> list[AlertEvent]:
        cursor = await self.db.execute(
            "SELECT * FROM alert_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            AlertEvent(
                id=row["id"],
                rule_id=row["rule_id"],
                opportunity_id=row["opportunity_id"],
                symbol=row["symbol"],
                status=row["status"],
                message=row["message"],
                created_at=row["created_at"],
            )
            for row in rows
        ]


class SettingsRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def get_risk_settings(self) -> RiskSettings:
        cursor = await self.db.execute("SELECT payload FROM app_settings WHERE key = ?", ("risk",))
        row = await cursor.fetchone()
        if row is None:
            return RiskSettings()
        return RiskSettings.model_validate(json.loads(row["payload"]))

    async def set_risk_settings(self, settings: RiskSettings) -> RiskSettings:
        await self.db.execute(
            """
            INSERT INTO app_settings (key, payload)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload
            """,
            ("risk", settings.model_dump_json()),
        )
        await self.db.commit()
        return settings
