from __future__ import annotations

import json
from datetime import datetime

import aiosqlite

from app.models.alert import AlertEvent, AlertRule
from app.models.announcement import AnnouncementKind, AnnouncementSettings, ExchangeAnnouncement
from app.models.history import OpportunityHistoryRow
from app.models.index_component import (
    IndexComponent,
    IndexComponentChange,
    IndexComponentSnapshot,
    IndexComponentWatchItem,
)
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.funding_arbitrage import FundingArbitrageSettings
from app.models.phone_alert import PhonePriceAlertEvent, PhonePriceAlertRule
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


def _component_json(components: list[IndexComponent]) -> str:
    return json.dumps(
        [item.model_dump(mode="json", exclude_none=True) for item in components],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _components_from_json(value: str) -> list[IndexComponent]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [
        IndexComponent.model_validate(item)
        for item in parsed
        if isinstance(item, dict)
    ]


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


class PhonePriceAlertRuleRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create(self, rule: PhonePriceAlertRule) -> PhonePriceAlertRule:
        payload = rule.model_dump_json()
        await self.db.execute(
            "INSERT INTO phone_price_alert_rules (id, payload) VALUES (?, ?)",
            (rule.id, payload),
        )
        await self.db.commit()
        return rule

    async def list(self) -> list[PhonePriceAlertRule]:
        cursor = await self.db.execute(
            "SELECT payload FROM phone_price_alert_rules ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        return [PhonePriceAlertRule.model_validate_json(row["payload"]) for row in rows]

    async def get(self, rule_id: str) -> PhonePriceAlertRule | None:
        cursor = await self.db.execute(
            "SELECT payload FROM phone_price_alert_rules WHERE id = ?",
            (rule_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return PhonePriceAlertRule.model_validate_json(row["payload"])

    async def upsert(self, rule: PhonePriceAlertRule) -> PhonePriceAlertRule:
        payload = rule.model_dump_json()
        await self.db.execute(
            """
            INSERT INTO phone_price_alert_rules (id, payload, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = CURRENT_TIMESTAMP
            """,
            (rule.id, payload),
        )
        await self.db.commit()
        return rule

    async def delete(self, rule_id: str) -> None:
        await self.db.execute("DELETE FROM phone_price_alert_rules WHERE id = ?", (rule_id,))
        await self.db.commit()


class PhonePriceAlertEventRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create(self, event: PhonePriceAlertEvent) -> PhonePriceAlertEvent:
        await self.db.execute(
            """
            INSERT INTO phone_price_alert_events (
              id, rule_id, symbol, exchange, market_type, price_field, condition,
              target_price, observed_price, status, message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.rule_id,
                event.symbol,
                event.exchange,
                event.market_type.value,
                event.price_field.value,
                event.condition.value,
                event.target_price,
                event.observed_price,
                event.status,
                event.message,
                event.created_at.isoformat(),
            ),
        )
        await self.db.commit()
        return event

    async def list(self, limit: int = 100) -> list[PhonePriceAlertEvent]:
        cursor = await self.db.execute(
            "SELECT * FROM phone_price_alert_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            PhonePriceAlertEvent(
                id=row["id"],
                rule_id=row["rule_id"],
                symbol=row["symbol"],
                exchange=row["exchange"],
                market_type=MarketType(row["market_type"]),
                price_field=row["price_field"],
                condition=row["condition"],
                target_price=row["target_price"],
                observed_price=row["observed_price"],
                status=row["status"],
                message=row["message"],
                created_at=row["created_at"],
            )
            for row in rows
        ]


class IndexComponentRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def get_snapshot(self, exchange: str, symbol: str) -> IndexComponentSnapshot | None:
        cursor = await self.db.execute(
            """
            SELECT * FROM index_component_snapshots
            WHERE exchange = ? AND symbol = ?
            """,
            (exchange.strip().lower(), symbol.strip().upper()),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._snapshot_from_db(row)

    async def upsert_snapshot(self, snapshot: IndexComponentSnapshot) -> IndexComponentSnapshot:
        await self.db.execute(
            """
            INSERT INTO index_component_snapshots (
              exchange, symbol, component_hash, components_json, source, observed_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(exchange, symbol) DO UPDATE SET
              component_hash = excluded.component_hash,
              components_json = excluded.components_json,
              source = excluded.source,
              observed_at = excluded.observed_at,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                snapshot.exchange,
                snapshot.symbol,
                snapshot.component_hash,
                _component_json(snapshot.components),
                snapshot.source,
                snapshot.observed_at.isoformat(),
            ),
        )
        await self.db.commit()
        return snapshot

    async def create_change(
        self,
        *,
        baseline: IndexComponentSnapshot,
        current: IndexComponentSnapshot,
        added_components: list[IndexComponent],
        removed_components: list[IndexComponent],
        changed_components: list[IndexComponent],
        alert_status: str,
    ) -> IndexComponentChange:
        change = IndexComponentChange(
            exchange=current.exchange,
            symbol=current.symbol,
            old_hash=baseline.component_hash,
            new_hash=current.component_hash,
            old_components=baseline.components,
            new_components=current.components,
            added_components=added_components,
            removed_components=removed_components,
            changed_components=changed_components,
            source=current.source,
            alert_status=alert_status,
            created_at=current.observed_at,
        )
        await self.db.execute(
            """
            INSERT INTO index_component_changes (
              id, exchange, symbol, old_hash, new_hash,
              old_components_json, new_components_json,
              added_components_json, removed_components_json, changed_components_json,
              source, alert_status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change.id,
                change.exchange,
                change.symbol,
                change.old_hash,
                change.new_hash,
                _component_json(change.old_components),
                _component_json(change.new_components),
                _component_json(change.added_components),
                _component_json(change.removed_components),
                _component_json(change.changed_components),
                change.source,
                change.alert_status,
                change.created_at.isoformat(),
            ),
        )
        await self.db.commit()
        return change

    async def update_change_alert_status(self, change_id: str, alert_status: str) -> None:
        await self.db.execute(
            "UPDATE index_component_changes SET alert_status = ? WHERE id = ?",
            (alert_status, change_id),
        )
        await self.db.commit()

    async def list_changes(
        self,
        *,
        symbol: str | None = None,
        exchange: str | None = None,
        limit: int = 100,
    ) -> list[IndexComponentChange]:
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("symbol LIKE ?")
            params.append(f"%{symbol.strip().upper()}%")
        if exchange:
            clauses.append("exchange = ?")
            params.append(exchange.strip().lower())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        cursor = await self.db.execute(
            f"""
            SELECT * FROM index_component_changes
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [self._change_from_db(row) for row in rows]

    async def list_snapshots(
        self,
        *,
        symbol: str | None = None,
        exchange: str | None = None,
        limit: int = 500,
    ) -> list[IndexComponentSnapshot]:
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("symbol LIKE ?")
            params.append(f"%{symbol.strip().upper()}%")
        if exchange:
            clauses.append("exchange = ?")
            params.append(exchange.strip().lower())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        cursor = await self.db.execute(
            f"""
            SELECT * FROM index_component_snapshots
            {where}
            ORDER BY observed_at DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [self._snapshot_from_db(row) for row in rows]

    async def list_watch_items(self) -> list[IndexComponentWatchItem]:
        cursor = await self.db.execute(
            """
            SELECT * FROM index_component_watchlist
            ORDER BY created_at DESC
            """
        )
        rows = await cursor.fetchall()
        return [self._watch_item_from_db(row) for row in rows]

    async def create_watch_item(self, item: IndexComponentWatchItem) -> IndexComponentWatchItem:
        await self.db.execute(
            """
            INSERT INTO index_component_watchlist (id, symbol, note, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
              note = excluded.note
            """,
            (
                item.id,
                item.symbol,
                item.note,
                item.created_at.isoformat(),
            ),
        )
        await self.db.commit()
        return item

    async def delete_watch_item(self, item_id: str) -> None:
        await self.db.execute(
            "DELETE FROM index_component_watchlist WHERE id = ?",
            (item_id,),
        )
        await self.db.commit()

    async def is_symbol_watched(self, symbol: str) -> bool:
        watched_items = await self.list_watch_items()
        if not watched_items:
            return False
        normalized = symbol.strip().upper()
        return any(normalized.startswith(item.symbol) for item in watched_items)

    def _snapshot_from_db(self, row: aiosqlite.Row) -> IndexComponentSnapshot:
        return IndexComponentSnapshot(
            exchange=row["exchange"],
            symbol=row["symbol"],
            component_hash=row["component_hash"],
            components=_components_from_json(row["components_json"]),
            source=row["source"],
            observed_at=row["observed_at"],
        )

    def _change_from_db(self, row: aiosqlite.Row) -> IndexComponentChange:
        return IndexComponentChange(
            id=row["id"],
            exchange=row["exchange"],
            symbol=row["symbol"],
            old_hash=row["old_hash"],
            new_hash=row["new_hash"],
            old_components=_components_from_json(row["old_components_json"]),
            new_components=_components_from_json(row["new_components_json"]),
            added_components=_components_from_json(row["added_components_json"]),
            removed_components=_components_from_json(row["removed_components_json"]),
            changed_components=_components_from_json(row["changed_components_json"]),
            source=row["source"],
            alert_status=row["alert_status"],
            created_at=row["created_at"],
        )

    def _watch_item_from_db(self, row: aiosqlite.Row) -> IndexComponentWatchItem:
        return IndexComponentWatchItem(
            id=row["id"],
            symbol=row["symbol"],
            note=row["note"],
            created_at=row["created_at"],
        )


class AnnouncementRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create_if_new(self, announcement: ExchangeAnnouncement) -> ExchangeAnnouncement | None:
        cursor = await self.db.execute(
            """
            INSERT OR IGNORE INTO exchange_announcements (
              id, exchange, announcement_id, kind, title, url, source, category,
              symbols_json, market_type, event_time, summary,
              published_at, fetched_at, alert_status, event_reminder_status,
              event_reminder_sent_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                announcement.id,
                announcement.exchange,
                announcement.announcement_id,
                announcement.kind.value,
                announcement.title,
                announcement.url,
                announcement.source,
                announcement.category,
                json.dumps(announcement.symbols, ensure_ascii=False, sort_keys=True),
                announcement.market_type,
                _serialize_datetime(announcement.event_time),
                announcement.summary,
                announcement.published_at.isoformat(),
                announcement.fetched_at.isoformat(),
                announcement.alert_status,
                announcement.event_reminder_status,
                _serialize_datetime(announcement.event_reminder_sent_at),
            ),
        )
        await self.db.commit()
        if cursor.rowcount == 0:
            await self._enrich_existing_metadata(announcement)
            return None
        return announcement

    async def _enrich_existing_metadata(self, announcement: ExchangeAnnouncement) -> None:
        symbols_json = json.dumps(announcement.symbols, ensure_ascii=False, sort_keys=True)
        await self.db.execute(
            """
            UPDATE exchange_announcements
            SET
              symbols_json = CASE WHEN ? != '[]' THEN ? ELSE symbols_json END,
              market_type = COALESCE(?, market_type),
              event_time = COALESCE(?, event_time),
              summary = CASE WHEN ? IS NOT NULL THEN ? ELSE summary END,
              event_reminder_status = CASE
                WHEN event_reminder_status = 'not_applicable' AND ? = 'pending' THEN 'pending'
                ELSE event_reminder_status
              END
            WHERE exchange = ? AND source = ? AND announcement_id = ?
            """,
            (
                symbols_json,
                symbols_json,
                announcement.market_type,
                _serialize_datetime(announcement.event_time),
                announcement.summary,
                announcement.summary,
                announcement.event_reminder_status,
                announcement.exchange,
                announcement.source,
                announcement.announcement_id,
            ),
        )
        await self.db.commit()

    async def update_alert_status(self, announcement_id: str, alert_status: str) -> None:
        await self.db.execute(
            "UPDATE exchange_announcements SET alert_status = ? WHERE id = ?",
            (alert_status, announcement_id),
        )
        await self.db.commit()

    async def update_event_reminder_status(
        self,
        announcement_id: str,
        event_reminder_status: str,
        *,
        sent_at: datetime | None = None,
    ) -> None:
        await self.db.execute(
            """
            UPDATE exchange_announcements
            SET event_reminder_status = ?, event_reminder_sent_at = ?
            WHERE id = ?
            """,
            (event_reminder_status, _serialize_datetime(sent_at), announcement_id),
        )
        await self.db.commit()

    async def has_any(self) -> bool:
        cursor = await self.db.execute("SELECT 1 FROM exchange_announcements LIMIT 1")
        row = await cursor.fetchone()
        return row is not None

    async def get_provider_state(self, key: str) -> dict[str, object] | None:
        cursor = await self.db.execute(
            "SELECT payload FROM announcement_provider_state WHERE key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        return payload if isinstance(payload, dict) else None

    async def set_provider_state(self, key: str, payload: dict[str, object]) -> None:
        await self.db.execute(
            """
            INSERT INTO announcement_provider_state (key, payload, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
              payload = excluded.payload,
              updated_at = CURRENT_TIMESTAMP
            """,
            (key, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        await self.db.commit()

    async def list(
        self,
        *,
        exchange: str | None = None,
        kind: AnnouncementKind | str | None = None,
        limit: int = 100,
    ) -> list[ExchangeAnnouncement]:
        clauses: list[str] = []
        params: list[object] = []
        if exchange:
            clauses.append("exchange = ?")
            params.append(exchange.strip().lower())
        if kind:
            kind_value = kind.value if isinstance(kind, AnnouncementKind) else str(kind)
            clauses.append("kind = ?")
            params.append(kind_value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        cursor = await self.db.execute(
            f"""
            SELECT * FROM exchange_announcements
            {where}
            ORDER BY published_at DESC, fetched_at DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [self._announcement_from_db(row) for row in rows]

    async def list_due_event_reminders(
        self,
        *,
        exchanges: set[str],
        reminder_due_before: datetime,
        now: datetime,
        limit: int = 100,
    ) -> list[ExchangeAnnouncement]:
        if not exchanges:
            return []
        placeholders = ",".join("?" for _ in exchanges)
        params: list[object] = [
            now.isoformat(),
            reminder_due_before.isoformat(),
            *sorted(exchanges),
            limit,
        ]
        cursor = await self.db.execute(
            f"""
            SELECT * FROM exchange_announcements
            WHERE event_time IS NOT NULL
              AND event_time > ?
              AND event_time <= ?
              AND event_reminder_status = 'pending'
              AND kind IN ('listing', 'delisting')
              AND exchange IN ({placeholders})
            ORDER BY event_time ASC, published_at DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [self._announcement_from_db(row) for row in rows]

    def _announcement_from_db(self, row: aiosqlite.Row) -> ExchangeAnnouncement:
        symbols = json.loads(row["symbols_json"]) if "symbols_json" in row.keys() else []
        if not isinstance(symbols, list):
            symbols = []
        return ExchangeAnnouncement(
            id=row["id"],
            exchange=row["exchange"],
            announcement_id=row["announcement_id"],
            kind=AnnouncementKind(row["kind"]),
            title=row["title"],
            url=row["url"],
            source=row["source"],
            category=row["category"],
            symbols=symbols,
            market_type=row["market_type"] if "market_type" in row.keys() else None,
            event_time=row["event_time"] if "event_time" in row.keys() else None,
            summary=row["summary"] if "summary" in row.keys() else None,
            published_at=row["published_at"],
            fetched_at=row["fetched_at"],
            alert_status=row["alert_status"],
            event_reminder_status=(
                row["event_reminder_status"] if "event_reminder_status" in row.keys() else "not_applicable"
            ),
            event_reminder_sent_at=(
                row["event_reminder_sent_at"] if "event_reminder_sent_at" in row.keys() else None
            ),
        )


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

    async def get_funding_arbitrage_settings(self) -> FundingArbitrageSettings:
        cursor = await self.db.execute(
            "SELECT payload FROM app_settings WHERE key = ?",
            ("funding_arbitrage",),
        )
        row = await cursor.fetchone()
        if row is None:
            return FundingArbitrageSettings()
        return FundingArbitrageSettings.model_validate(json.loads(row["payload"]))

    async def set_funding_arbitrage_settings(
        self,
        settings: FundingArbitrageSettings,
    ) -> FundingArbitrageSettings:
        await self.db.execute(
            """
            INSERT INTO app_settings (key, payload)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload
            """,
            ("funding_arbitrage", settings.model_dump_json()),
        )
        await self.db.commit()
        return settings

    async def get_announcement_settings(self) -> AnnouncementSettings:
        cursor = await self.db.execute(
            "SELECT payload FROM app_settings WHERE key = ?",
            ("announcements",),
        )
        row = await cursor.fetchone()
        if row is None:
            return AnnouncementSettings()
        return AnnouncementSettings.model_validate(json.loads(row["payload"]))

    async def set_announcement_settings(
        self,
        settings: AnnouncementSettings,
    ) -> AnnouncementSettings:
        await self.db.execute(
            """
            INSERT INTO app_settings (key, payload)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload
            """,
            ("announcements", settings.model_dump_json()),
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
