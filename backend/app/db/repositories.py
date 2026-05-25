import json
from datetime import datetime

import aiosqlite

from app.models.alert import AlertEvent, AlertRule
from app.models.history import OpportunityHistoryRow
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import AlertMessageTemplateSettings, AstroCardSettings, LivePilotSettings, RiskSettings

PERCENT_SCALE = 10_000
RISK_LABEL_BITS = {
    "LOW_VOLUME": 1 << 0,
    "STALE_DATA": 1 << 1,
    "HUGE_SPREAD_VERIFY": 1 << 2,
    "WIDE_SPREAD": 1 << 3,
    "SAME_TICKER_RISK": 1 << 4,
    "FUNDING_AGAINST": 1 << 5,
    "MARK_INDEX_DEVIATION": 1 << 6,
    "MISSING_FUNDING": 1 << 7,
    "THIN_ORDER_BOOK": 1 << 8,
    "EDGE_AFTER_SLIPPAGE_TOO_SMALL": 1 << 9,
    "TRANSIENT_SIGNAL": 1 << 10,
}
RISK_LABELS_BY_BIT = {value: key for key, value in RISK_LABEL_BITS.items()}


def _scale_percent(value: float | None) -> int | None:
    if value is None:
        return None
    return round(value * PERCENT_SCALE)


def _unscale_percent(value: int | None) -> float | None:
    if value is None:
        return None
    return value / PERCENT_SCALE


def _risk_label_mask(labels: list[str]) -> int:
    mask = 0
    for label in labels:
        mask |= RISK_LABEL_BITS.get(label, 0)
    return mask


def _risk_labels_from_mask(mask: int) -> list[str]:
    return [
        label
        for bit, label in sorted(RISK_LABELS_BY_BIT.items())
        if mask & bit
    ]


def _serialize_datetime(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _deserialize_datetime(value) -> str | None:
    return value


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

    async def get_alert_message_template(self) -> AlertMessageTemplateSettings:
        cursor = await self.db.execute(
            "SELECT payload FROM app_settings WHERE key = ?",
            ("alert_message_template",),
        )
        row = await cursor.fetchone()
        if row is None:
            return AlertMessageTemplateSettings()
        return AlertMessageTemplateSettings.model_validate(json.loads(row["payload"]))

    async def set_alert_message_template(
        self,
        settings: AlertMessageTemplateSettings,
    ) -> AlertMessageTemplateSettings:
        await self.db.execute(
            """
            INSERT INTO app_settings (key, payload)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload
            """,
            ("alert_message_template", settings.model_dump_json()),
        )
        await self.db.commit()
        return settings

    async def get_astro_card_settings(self) -> AstroCardSettings:
        settings = await self.find_astro_card_settings()
        return settings or AstroCardSettings()

    async def find_astro_card_settings(self) -> AstroCardSettings | None:
        cursor = await self.db.execute(
            "SELECT payload FROM app_settings WHERE key = ?",
            ("astro_card",),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return AstroCardSettings.model_validate(json.loads(row["payload"]))

    async def set_astro_card_settings(
        self,
        settings: AstroCardSettings,
    ) -> AstroCardSettings:
        await self.db.execute(
            """
            INSERT INTO app_settings (key, payload)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload
            """,
            ("astro_card", settings.model_dump_json()),
        )
        await self.db.commit()
        return settings

    async def get_live_pilot_settings(self) -> LivePilotSettings:
        cursor = await self.db.execute(
            "SELECT payload FROM app_settings WHERE key = ?",
            ("live_pilot",),
        )
        row = await cursor.fetchone()
        if row is None:
            return LivePilotSettings()
        return LivePilotSettings.model_validate(json.loads(row["payload"]))

    async def set_live_pilot_settings(
        self,
        settings: LivePilotSettings,
    ) -> LivePilotSettings:
        await self.db.execute(
            """
            INSERT INTO app_settings (key, payload)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload
            """,
            ("live_pilot", settings.model_dump_json()),
        )
        await self.db.commit()
        return settings


class OpportunityHistoryRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    def row_from_opportunity(
        self,
        opportunity: Opportunity,
        observed_at,
    ) -> OpportunityHistoryRow:
        return OpportunityHistoryRow(
            observed_at=observed_at,
            opportunity_id=opportunity.id,
            type=opportunity.type,
            symbol=opportunity.symbol,
            buy_exchange=opportunity.buy_exchange,
            buy_market_type=opportunity.buy_market_type,
            sell_exchange=opportunity.sell_exchange,
            sell_market_type=opportunity.sell_market_type,
            open_spread_pct=opportunity.open_spread_pct,
            close_spread_pct=opportunity.close_spread_pct,
            fee_adjusted_open_pct=opportunity.fee_adjusted_open_pct,
            spread_width_pct=opportunity.spread_width_pct,
            funding_rate_buy_pct=opportunity.funding_rate_buy_pct,
            funding_rate_sell_pct=opportunity.funding_rate_sell_pct,
            net_funding_pct=opportunity.net_funding_pct,
            buy_volume_24h_usdt=opportunity.buy_volume_24h_usdt,
            sell_volume_24h_usdt=opportunity.sell_volume_24h_usdt,
            funding_next_rate_buy_pct=opportunity.funding_next_rate_buy_pct,
            funding_next_rate_sell_pct=opportunity.funding_next_rate_sell_pct,
            funding_next_time_buy=opportunity.funding_next_time_buy,
            funding_next_time_sell=opportunity.funding_next_time_sell,
            net_funding_next_pct=opportunity.net_funding_next_pct,
            buy_funding_interval_hours=opportunity.buy_funding_interval_hours,
            sell_funding_interval_hours=opportunity.sell_funding_interval_hours,
            net_funding_hourly_pct=opportunity.net_funding_hourly_pct,
            net_funding_daily_pct=opportunity.net_funding_daily_pct,
            net_funding_next_hourly_pct=opportunity.net_funding_next_hourly_pct,
            net_funding_next_daily_pct=opportunity.net_funding_next_daily_pct,
            risk_labels=opportunity.risk_labels,
        )

    async def insert_many(self, rows: list[OpportunityHistoryRow]) -> int:
        if not rows:
            return 0
        await self.db.executemany(
            """
            INSERT INTO opportunity_history (
              observed_at, opportunity_id, type, symbol,
              buy_exchange, buy_market_type, sell_exchange, sell_market_type,
              open_spread_scaled, close_spread_scaled, fee_adjusted_open_scaled,
              spread_width_scaled, funding_rate_buy_scaled, funding_rate_sell_scaled,
              funding_next_rate_buy_scaled, funding_next_rate_sell_scaled,
              net_funding_scaled, net_funding_next_scaled,
              buy_funding_interval_hours, sell_funding_interval_hours,
              net_funding_hourly_scaled, net_funding_daily_scaled,
              net_funding_next_hourly_scaled, net_funding_next_daily_scaled,
              funding_next_time_buy, funding_next_time_sell,
              buy_volume_24h_usdt, sell_volume_24h_usdt, risk_label_mask
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.observed_at.isoformat(),
                    row.opportunity_id,
                    row.type,
                    row.symbol,
                    row.buy_exchange,
                    row.buy_market_type,
                    row.sell_exchange,
                    row.sell_market_type,
                    _scale_percent(row.open_spread_pct),
                    _scale_percent(row.close_spread_pct),
                    _scale_percent(row.fee_adjusted_open_pct),
                    _scale_percent(row.spread_width_pct),
                    _scale_percent(row.funding_rate_buy_pct),
                    _scale_percent(row.funding_rate_sell_pct),
                    _scale_percent(row.funding_next_rate_buy_pct),
                    _scale_percent(row.funding_next_rate_sell_pct),
                    _scale_percent(row.net_funding_pct),
                    _scale_percent(row.net_funding_next_pct),
                    row.buy_funding_interval_hours,
                    row.sell_funding_interval_hours,
                    _scale_percent(row.net_funding_hourly_pct),
                    _scale_percent(row.net_funding_daily_pct),
                    _scale_percent(row.net_funding_next_hourly_pct),
                    _scale_percent(row.net_funding_next_daily_pct),
                    _serialize_datetime(row.funding_next_time_buy),
                    _serialize_datetime(row.funding_next_time_sell),
                    row.buy_volume_24h_usdt,
                    row.sell_volume_24h_usdt,
                    _risk_label_mask(row.risk_labels),
                )
                for row in rows
            ],
        )
        await self.db.commit()
        return len(rows)

    async def list(
        self,
        symbol: str | None = None,
        opportunity_id: str | None = None,
        type: str | None = None,
        since=None,
        limit: int = 1000,
    ) -> list[OpportunityHistoryRow]:
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if opportunity_id:
            clauses.append("opportunity_id = ?")
            params.append(opportunity_id)
        if type:
            clauses.append("type = ?")
            params.append(type)
        if since:
            clauses.append("observed_at >= ?")
            params.append(since.isoformat())

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        cursor = await self.db.execute(
            f"""
            SELECT * FROM opportunity_history
            {where}
            ORDER BY observed_at DESC, open_spread_scaled DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [self._row_from_db(row) for row in rows]

    async def list_before(
        self,
        opportunity_id: str,
        before: datetime,
        limit: int = 1,
    ) -> "list[OpportunityHistoryRow]":
        cursor = await self.db.execute(
            """
            SELECT * FROM opportunity_history
            WHERE opportunity_id = ? AND observed_at <= ?
            ORDER BY observed_at DESC, open_spread_scaled DESC
            LIMIT ?
            """,
            (opportunity_id, before.isoformat(), limit),
        )
        rows = await cursor.fetchall()
        return [self._row_from_db(row) for row in rows]

    async def prune_before(self, cutoff) -> int:
        cursor = await self.db.execute(
            "DELETE FROM opportunity_history WHERE observed_at < ?",
            (cutoff.isoformat(),),
        )
        deleted = cursor.rowcount if cursor.rowcount is not None else 0
        await self.db.commit()
        return deleted

    async def vacuum(self) -> None:
        await self.db.execute("VACUUM")

    def _row_from_db(self, row: aiosqlite.Row) -> OpportunityHistoryRow:
        return OpportunityHistoryRow(
            observed_at=row["observed_at"],
            opportunity_id=row["opportunity_id"],
            type=OpportunityType(row["type"]),
            symbol=row["symbol"],
            buy_exchange=row["buy_exchange"],
            buy_market_type=MarketType(row["buy_market_type"]),
            sell_exchange=row["sell_exchange"],
            sell_market_type=MarketType(row["sell_market_type"]),
            open_spread_pct=_unscale_percent(row["open_spread_scaled"]) or 0,
            close_spread_pct=_unscale_percent(row["close_spread_scaled"]) or 0,
            fee_adjusted_open_pct=_unscale_percent(row["fee_adjusted_open_scaled"]) or 0,
            spread_width_pct=_unscale_percent(row["spread_width_scaled"]) or 0,
            funding_rate_buy_pct=_unscale_percent(row["funding_rate_buy_scaled"]),
            funding_rate_sell_pct=_unscale_percent(row["funding_rate_sell_scaled"]),
            funding_next_rate_buy_pct=_unscale_percent(row["funding_next_rate_buy_scaled"]),
            funding_next_rate_sell_pct=_unscale_percent(row["funding_next_rate_sell_scaled"]),
            net_funding_pct=_unscale_percent(row["net_funding_scaled"]),
            net_funding_next_pct=_unscale_percent(row["net_funding_next_scaled"]),
            buy_funding_interval_hours=row["buy_funding_interval_hours"],
            sell_funding_interval_hours=row["sell_funding_interval_hours"],
            net_funding_hourly_pct=_unscale_percent(row["net_funding_hourly_scaled"]),
            net_funding_daily_pct=_unscale_percent(row["net_funding_daily_scaled"]),
            net_funding_next_hourly_pct=_unscale_percent(row["net_funding_next_hourly_scaled"]),
            net_funding_next_daily_pct=_unscale_percent(row["net_funding_next_daily_scaled"]),
            funding_next_time_buy=_deserialize_datetime(row["funding_next_time_buy"]),
            funding_next_time_sell=_deserialize_datetime(row["funding_next_time_sell"]),
            buy_volume_24h_usdt=row["buy_volume_24h_usdt"],
            sell_volume_24h_usdt=row["sell_volume_24h_usdt"],
            risk_labels=_risk_labels_from_mask(row["risk_label_mask"]),
        )
