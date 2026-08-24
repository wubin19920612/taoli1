from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha1
from itertools import combinations
from math import isfinite
from typing import Any

import aiosqlite

from app.models.announcement import AnnouncementKind, ExchangeAnnouncement
from app.models.astro import AstroAlertActionResult
from app.models.market import MarketType
from app.models.new_listing import (
    DEFAULT_NEW_LISTING_EXCHANGES,
    NewListingAlertEvent,
    NewListingAlertLevel,
    NewListingHistoryResult,
    NewListingMonitorStatus,
    NewListingSpreadSample,
    NewListingWatchItem,
)
from app.models.opportunity import Opportunity, OpportunityType
from app.models.pair_spread import normalize_pair_spread_symbol
from app.models.second_level_sampling import SUPPORTED_SECOND_LEVEL_EXCHANGES, SecondLevelMarketSample
from app.models.settings import RiskSettings
from app.services.second_level_sampler import SecondLevelMarketFetcher
from app.services.risk_labels import NEW_LISTING_RISK_LABEL
from app.services.symbol_aliases import ResolvedSymbolAlias, SymbolAliasResolver

logger = logging.getLogger(__name__)

NEW_LISTING_PREWARM_LOOKBACK_HOURS = 2
NEW_LISTING_PREWARM_FUTURE_HOURS = 72
NEW_LISTING_PREWARM_POST_LISTING_HOURS = 2


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _error_message(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{exc.__class__.__name__}: {text}" if text else exc.__class__.__name__


def _positive(value: float | None) -> float | None:
    if value is None or not isfinite(value) or value <= 0:
        return None
    return value


def _risk_labels_json(labels: list[str]) -> str:
    return json.dumps(labels, ensure_ascii=False, separators=(",", ":"))


def _risk_labels_from_json(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _watch_from_row(row: aiosqlite.Row) -> NewListingWatchItem:
    return NewListingWatchItem.model_validate_json(row["payload"])


def _sample_from_row(row: aiosqlite.Row) -> NewListingSpreadSample:
    return NewListingSpreadSample(
        id=row["id"],
        watch_id=row["watch_id"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        symbol=row["symbol"],
        market_type=MarketType(row["market_type"]),
        buy_exchange=row["buy_exchange"],
        sell_exchange=row["sell_exchange"],
        buy_bid=row["buy_bid"],
        buy_ask=row["buy_ask"],
        buy_bid_size=row["buy_bid_size"],
        buy_ask_size=row["buy_ask_size"],
        sell_bid=row["sell_bid"],
        sell_ask=row["sell_ask"],
        sell_bid_size=row["sell_bid_size"],
        sell_ask_size=row["sell_ask_size"],
        buy_price=row["buy_price"],
        sell_price=row["sell_price"],
        raw_spread_pct=row["raw_spread_pct"],
        net_spread_pct=row["net_spread_pct"],
        executable_notional_usdt=row["executable_notional_usdt"],
        buy_latency_ms=row["buy_latency_ms"],
        sell_latency_ms=row["sell_latency_ms"],
        alert_level=row["alert_level"],
        alert_triggered=bool(row["alert_triggered"]),
        no_alert_reason=row["no_alert_reason"],
        risk_labels=_risk_labels_from_json(row["risk_labels_json"]),
    )


def _event_from_row(row: aiosqlite.Row) -> NewListingAlertEvent:
    return NewListingAlertEvent(
        id=row["id"],
        watch_id=row["watch_id"],
        symbol=row["symbol"],
        market_type=MarketType(row["market_type"]),
        level=row["level"],
        buy_exchange=row["buy_exchange"],
        sell_exchange=row["sell_exchange"],
        net_spread_pct=row["net_spread_pct"],
        raw_spread_pct=row["raw_spread_pct"],
        executable_notional_usdt=row["executable_notional_usdt"],
        message=row["message"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class NewListingMonitorRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def upsert_watch_item(self, item: NewListingWatchItem) -> NewListingWatchItem:
        saved = item.model_copy(update={"updated_at": utc_now()})
        await self.db.execute(
            """
            INSERT INTO new_listing_watchlist (id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (
                saved.id,
                saved.model_dump_json(),
                saved.created_at.isoformat(),
                saved.updated_at.isoformat(),
            ),
        )
        await self.db.commit()
        return saved

    async def get_watch_item(self, item_id: str) -> NewListingWatchItem | None:
        cursor = await self.db.execute(
            "SELECT payload FROM new_listing_watchlist WHERE id = ?",
            (item_id,),
        )
        row = await cursor.fetchone()
        return _watch_from_row(row) if row is not None else None

    async def list_watch_items(self) -> list[NewListingWatchItem]:
        cursor = await self.db.execute(
            """
            SELECT payload
            FROM new_listing_watchlist
            ORDER BY updated_at DESC, created_at DESC
            """
        )
        rows = await cursor.fetchall()
        return [_watch_from_row(row) for row in rows]

    async def delete_watch_item(self, item_id: str) -> None:
        await self.db.execute("DELETE FROM new_listing_watchlist WHERE id = ?", (item_id,))
        await self.db.commit()

    async def insert_samples(self, samples: list[NewListingSpreadSample]) -> None:
        if not samples:
            return
        await self.db.executemany(
            """
            INSERT INTO new_listing_spread_samples (
              watch_id, observed_at, symbol, market_type,
              buy_exchange, sell_exchange,
              buy_bid, buy_ask, buy_bid_size, buy_ask_size,
              sell_bid, sell_ask, sell_bid_size, sell_ask_size,
              buy_price, sell_price, raw_spread_pct, net_spread_pct,
              executable_notional_usdt, buy_latency_ms, sell_latency_ms,
              alert_level, alert_triggered, no_alert_reason, risk_labels_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.watch_id,
                    item.observed_at.isoformat(),
                    item.symbol,
                    item.market_type.value,
                    item.buy_exchange,
                    item.sell_exchange,
                    item.buy_bid,
                    item.buy_ask,
                    item.buy_bid_size,
                    item.buy_ask_size,
                    item.sell_bid,
                    item.sell_ask,
                    item.sell_bid_size,
                    item.sell_ask_size,
                    item.buy_price,
                    item.sell_price,
                    item.raw_spread_pct,
                    item.net_spread_pct,
                    item.executable_notional_usdt,
                    item.buy_latency_ms,
                    item.sell_latency_ms,
                    item.alert_level,
                    1 if item.alert_triggered else 0,
                    item.no_alert_reason,
                    _risk_labels_json(item.risk_labels),
                )
                for item in samples
            ],
        )
        await self.db.commit()

    async def create_event(self, event: NewListingAlertEvent) -> NewListingAlertEvent:
        await self.db.execute(
            """
            INSERT INTO new_listing_alert_events (
              id, watch_id, symbol, market_type, level,
              buy_exchange, sell_exchange,
              net_spread_pct, raw_spread_pct, executable_notional_usdt,
              message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.watch_id,
                event.symbol,
                event.market_type.value,
                event.level,
                event.buy_exchange,
                event.sell_exchange,
                event.net_spread_pct,
                event.raw_spread_pct,
                event.executable_notional_usdt,
                event.message,
                event.created_at.isoformat(),
            ),
        )
        await self.db.commit()
        return event

    async def list_samples(
        self,
        *,
        watch_id: str | None = None,
        symbol: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 1000,
    ) -> list[NewListingSpreadSample]:
        clauses: list[str] = []
        params: list[object] = []
        if watch_id:
            clauses.append("watch_id = ?")
            params.append(watch_id)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if start_at:
            clauses.append("observed_at >= ?")
            params.append(_as_utc(start_at).isoformat())
        if end_at:
            clauses.append("observed_at <= ?")
            params.append(_as_utc(end_at).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self.db.execute(
            f"""
            SELECT *
            FROM new_listing_spread_samples
            {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [_sample_from_row(row) for row in rows]

    async def list_events(
        self,
        *,
        watch_id: str | None = None,
        symbol: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 200,
    ) -> list[NewListingAlertEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if watch_id:
            clauses.append("watch_id = ?")
            params.append(watch_id)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if start_at:
            clauses.append("created_at >= ?")
            params.append(_as_utc(start_at).isoformat())
        if end_at:
            clauses.append("created_at <= ?")
            params.append(_as_utc(end_at).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self.db.execute(
            f"""
            SELECT *
            FROM new_listing_alert_events
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [_event_from_row(row) for row in rows]

    async def count_samples(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) AS c FROM new_listing_spread_samples")
        row = await cursor.fetchone()
        return int(row["c"] if row is not None else 0)

    async def count_events(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) AS c FROM new_listing_alert_events")
        row = await cursor.fetchone()
        return int(row["c"] if row is not None else 0)

    async def prune(self, retention_hours: float) -> int:
        cutoff = utc_now() - timedelta(hours=retention_hours)
        sample_cursor = await self.db.execute(
            "DELETE FROM new_listing_spread_samples WHERE observed_at < ?",
            (cutoff.isoformat(),),
        )
        event_cursor = await self.db.execute(
            "DELETE FROM new_listing_alert_events WHERE created_at < ?",
            (cutoff.isoformat(),),
        )
        await self.db.commit()
        deleted = sample_cursor.rowcount if sample_cursor.rowcount is not None else 0
        deleted += event_cursor.rowcount if event_cursor.rowcount is not None else 0
        return deleted


def _announcement_time_candidates(announcement: ExchangeAnnouncement) -> list[datetime]:
    candidates = [announcement.event_time]
    candidates.extend(item.event_time for item in announcement.event_schedule)
    resolved = [item for item in candidates if isinstance(item, datetime)]
    return resolved or [announcement.published_at]


def _market_type_from_announcement(announcement: ExchangeAnnouncement) -> MarketType | None:
    text = f"{announcement.market_type or ''} {announcement.title}".lower()
    if any(token in text for token in ("future", "futures", "perpetual", "contract", "swap", "合约", "合約", "永续", "永續")):
        return MarketType.FUTURE
    if "spot" in text or "现货" in text or "現貨" in text:
        return MarketType.SPOT
    return None


def _prewarm_watch_id(symbol: str, market_type: MarketType) -> str:
    digest = sha1(f"{symbol}:{market_type.value}".encode("utf-8")).hexdigest()[:16]
    return f"new-listing-prewarm-{digest}"


def _prewarm_note(announcement: ExchangeAnnouncement, symbol: str) -> str:
    event_time = _announcement_symbol_event_time(announcement, symbol)
    event_text = f"；事件时间 {event_time.astimezone(UTC).isoformat()}" if event_time is not None else ""
    return (
        f"公告预热：{announcement.exchange.upper()} {announcement.title}"
        f"{event_text}；{announcement.url}"
    )


def _announcement_symbol_event_time(
    announcement: ExchangeAnnouncement,
    symbol: str,
) -> datetime | None:
    return next(
        (
            item.event_time
            for item in announcement.event_schedule
            if normalize_pair_spread_symbol(item.symbol) == symbol
        ),
        announcement.event_time,
    )


def _prewarm_start_at(
    announcement: ExchangeAnnouncement,
    symbol: str,
    now: datetime,
) -> datetime:
    event_time = _announcement_symbol_event_time(announcement, symbol)
    if event_time is None:
        return now
    return _as_utc(event_time) - timedelta(minutes=5)


def _prewarm_stop_at(
    announcement: ExchangeAnnouncement,
    symbol: str,
) -> datetime:
    event_time = _announcement_symbol_event_time(announcement, symbol)
    if event_time is not None:
        return _as_utc(event_time) + timedelta(hours=NEW_LISTING_PREWARM_POST_LISTING_HOURS)
    return _as_utc(announcement.published_at) + timedelta(hours=NEW_LISTING_PREWARM_POST_LISTING_HOURS)


def _prewarm_exchanges(announcement: ExchangeAnnouncement) -> list[str]:
    exchanges = list(DEFAULT_NEW_LISTING_EXCHANGES)
    source_exchange = announcement.exchange.strip().lower()
    if source_exchange in SUPPORTED_SECOND_LEVEL_EXCHANGES:
        exchanges.insert(0, source_exchange)
    seen: set[str] = set()
    unique: list[str] = []
    for exchange in exchanges:
        if exchange in seen:
            continue
        seen.add(exchange)
        unique.append(exchange)
    return unique


def _is_prewarm_window(announcement: ExchangeAnnouncement, now: datetime) -> bool:
    scheduled_times = [announcement.event_time]
    scheduled_times.extend(item.event_time for item in announcement.event_schedule)
    scheduled_times = [item for item in scheduled_times if isinstance(item, datetime)]
    start_at = now - timedelta(hours=NEW_LISTING_PREWARM_LOOKBACK_HOURS)
    end_at = now + timedelta(hours=NEW_LISTING_PREWARM_FUTURE_HOURS)
    if scheduled_times:
        return any(start_at <= _as_utc(item) <= end_at for item in scheduled_times)
    return start_at <= _as_utc(announcement.published_at) <= now


def _is_symbol_prewarm_window(
    announcement: ExchangeAnnouncement,
    symbol: str,
    now: datetime,
) -> bool:
    event_time = _announcement_symbol_event_time(announcement, symbol)
    start_at = now - timedelta(hours=NEW_LISTING_PREWARM_LOOKBACK_HOURS)
    end_at = now + timedelta(hours=NEW_LISTING_PREWARM_FUTURE_HOURS)
    if event_time is not None:
        return start_at <= _as_utc(event_time) <= end_at
    return start_at <= _as_utc(announcement.published_at) <= now


def _is_auto_prewarm_watch(item: NewListingWatchItem) -> bool:
    return item.id.startswith("new-listing-prewarm-")


def _earliest_time(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(_as_utc(left), _as_utc(right))


def _latest_time(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(_as_utc(left), _as_utc(right))


def _legacy_auto_stop_at(item: NewListingWatchItem) -> datetime | None:
    if not _is_auto_prewarm_watch(item) or item.start_at is None:
        return None
    return _as_utc(item.start_at) + timedelta(
        minutes=5,
        hours=NEW_LISTING_PREWARM_POST_LISTING_HOURS,
    )


def _watch_is_active(item: NewListingWatchItem, now: datetime) -> bool:
    if not item.enabled:
        return False
    if item.start_at is not None and _as_utc(item.start_at) > now:
        return False
    if item.stop_at is not None:
        return _as_utc(item.stop_at) > now
    # Watches created before stop_at existed keep the original two-hour post-listing window.
    if (legacy_stop_at := _legacy_auto_stop_at(item)) is not None:
        return legacy_stop_at > now
    return True


class NewListingPrewarmer:
    def __init__(
        self,
        repo: NewListingMonitorRepository,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = repo
        self._now_fn = now_fn or utc_now

    async def backfill_auto_watch_windows(self) -> list[NewListingWatchItem]:
        saved: list[NewListingWatchItem] = []
        for existing in await self.repo.list_watch_items():
            legacy_stop_at = _legacy_auto_stop_at(existing)
            if existing.stop_at is not None or legacy_stop_at is None:
                continue
            saved.append(
                await self.repo.upsert_watch_item(
                    existing.model_copy(update={"stop_at": legacy_stop_at})
                )
            )
        return saved

    async def prewarm_from_announcement(self, announcement: ExchangeAnnouncement) -> list[NewListingWatchItem]:
        if announcement.kind != AnnouncementKind.LISTING:
            return []
        market_type = _market_type_from_announcement(announcement)
        if market_type is None:
            return []
        now = self._now_fn()
        if not _is_prewarm_window(announcement, now):
            return []

        saved: list[NewListingWatchItem] = []
        existing_items = await self.repo.list_watch_items()
        for raw_symbol in announcement.symbols:
            try:
                symbol = normalize_pair_spread_symbol(raw_symbol)
            except ValueError:
                continue
            if not _is_symbol_prewarm_window(announcement, symbol, now):
                continue
            existing = next(
                (
                    item
                    for item in existing_items
                    if item.symbol == symbol and item.market_type == market_type
                ),
                None,
            )
            exchanges = _prewarm_exchanges(announcement)
            note = _prewarm_note(announcement, symbol)
            start_at = _prewarm_start_at(announcement, symbol, now)
            stop_at = _prewarm_stop_at(announcement, symbol)
            if existing is None:
                item = NewListingWatchItem(
                    id=_prewarm_watch_id(symbol, market_type),
                    enabled=True,
                    symbol=symbol,
                    market_type=market_type,
                    exchanges=exchanges,
                    interval_seconds=1,
                    retention_hours=72,
                    normal_threshold_pct=1,
                    strong_threshold_pct=3,
                    extreme_threshold_pct=8,
                    min_executable_notional_usdt=50,
                    depth_validation_notional_usdt=100,
                    allow_low_liquidity_alert=True,
                    normal_consecutive_hits=1,
                    strong_consecutive_hits=1,
                    extreme_consecutive_hits=1,
                    cooldown_seconds=5,
                    buy_fee_pct=0.05,
                    sell_fee_pct=0.05,
                    slippage_buffer_pct=0.10,
                    start_at=start_at,
                    stop_at=stop_at,
                    note=note,
                )
            else:
                merged_exchanges = [*existing.exchanges]
                for exchange in exchanges:
                    if exchange not in merged_exchanges:
                        merged_exchanges.append(exchange)
                updates: dict[str, object] = {}
                if merged_exchanges != existing.exchanges:
                    updates["exchanges"] = merged_exchanges
                if not existing.note:
                    updates["note"] = note
                if _is_auto_prewarm_watch(existing):
                    next_start_at = _earliest_time(existing.start_at, start_at)
                    next_stop_at = _latest_time(existing.stop_at, stop_at)
                    if next_start_at != existing.start_at:
                        updates["start_at"] = next_start_at
                    if next_stop_at != existing.stop_at:
                        updates["stop_at"] = next_stop_at
                if not updates:
                    continue
                item = existing.model_copy(update=updates)
            saved_item = await self.repo.upsert_watch_item(item)
            saved.append(saved_item)
            existing_items.append(saved_item)
        return saved


class NewListingMonitor:
    def __init__(
        self,
        repo: NewListingMonitorRepository,
        fetcher: SecondLevelMarketFetcher | None = None,
        alert_sender: Callable[[str], Awaitable[None]] | None = None,
        risk_settings_loader: Callable[[], Awaitable[RiskSettings]] | None = None,
        astro_alert_handler: Callable[[Opportunity], Awaitable[AstroAlertActionResult]] | None = None,
    ) -> None:
        self.repo = repo
        self.fetcher = fetcher or SecondLevelMarketFetcher()
        self.alert_sender = alert_sender
        self.risk_settings_loader = risk_settings_loader
        self.astro_alert_handler = astro_alert_handler
        self._hits: dict[str, int] = {}
        self._last_sent: dict[str, datetime] = {}
        self._last_run_at: dict[str, datetime] = {}
        self._latest_error: str | None = None
        self._running = False

    def running(self) -> bool:
        return self._running

    async def aclose(self) -> None:
        await self.fetcher.aclose()

    async def run(self, stop_event: asyncio.Event) -> None:
        self._running = True
        prune_counter = 0
        try:
            while not stop_event.is_set():
                try:
                    await self.collect_due()
                    self._latest_error = None
                except Exception as exc:  # noqa: BLE001 - background loop must keep running.
                    self._latest_error = _error_message(exc)
                    logger.exception("new listing monitor cycle failed")
                prune_counter += 1
                if prune_counter >= 120:
                    prune_counter = 0
                    with suppress(Exception):
                        items = await self.repo.list_watch_items()
                        retention_hours = max((item.retention_hours for item in items), default=72.0)
                        await self.repo.prune(retention_hours)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.25)
                except TimeoutError:
                    continue
        finally:
            self._running = False

    async def collect_due(self) -> list[NewListingSpreadSample]:
        now = utc_now()
        items = [
            item
            for item in await self.repo.list_watch_items()
            if _watch_is_active(item, now)
        ]
        due_items = [
            item
            for item in items
            if self._last_run_at.get(item.id) is None
            or (now - self._last_run_at[item.id]).total_seconds() >= item.interval_seconds
        ]
        if not due_items:
            return []
        batches = await asyncio.gather(*(self.collect_watch_item(item) for item in due_items))
        return [sample for batch in batches for sample in batch]

    async def collect_watch_item(self, item: NewListingWatchItem) -> list[NewListingSpreadSample]:
        started = time.perf_counter()
        observed_at = utc_now()
        resolver = SymbolAliasResolver([])
        if self.risk_settings_loader is not None:
            resolver = SymbolAliasResolver((await self.risk_settings_loader()).symbol_aliases)
        aliases_by_exchange = {
            exchange: resolver.resolve(
                exchange=exchange,
                symbol=item.symbol,
                market_type=item.market_type,
            )
            for exchange in item.exchanges
        }
        exchange_samples = await asyncio.gather(
            *(
                self.fetcher.fetch(exchange, aliases_by_exchange[exchange].raw_symbol)
                for exchange in item.exchanges
            ),
            return_exceptions=True,
        )
        market_samples: list[SecondLevelMarketSample] = []
        for exchange, result in zip(item.exchanges, exchange_samples, strict=True):
            if isinstance(result, Exception):
                market_samples.append(
                    SecondLevelMarketSample(
                        observed_at=observed_at,
                        exchange=exchange,
                        symbol=item.symbol,
                        status="error",
                        error=_error_message(result),
                    )
                )
            else:
                market_samples.append(
                    _apply_symbol_alias_to_market_sample(
                        result,
                        alias=aliases_by_exchange[exchange],
                        display_symbol=item.symbol,
                    )
                )

        samples = self._spread_samples(item, market_samples, observed_at=observed_at)
        await self.repo.insert_samples(samples)
        astro_candidate = next((sample for sample in samples if sample.alert_triggered), None)
        for sample in samples:
            if not sample.alert_triggered:
                continue
            message = self._alert_message(sample)
            if self.astro_alert_handler is not None:
                if sample is astro_candidate:
                    message = await self._append_astro_result(sample, message)
                else:
                    message = f"{message}\nAstro: 同轮仅最高净价差路线尝试自动建卡"
            event = NewListingAlertEvent(
                watch_id=item.id,
                symbol=sample.symbol,
                market_type=sample.market_type,
                level=sample.alert_level,
                buy_exchange=sample.buy_exchange,
                sell_exchange=sample.sell_exchange,
                net_spread_pct=sample.net_spread_pct,
                raw_spread_pct=sample.raw_spread_pct,
                executable_notional_usdt=sample.executable_notional_usdt,
                message=message,
                created_at=observed_at,
            )
            await self.repo.create_event(event)
            if self.alert_sender is not None:
                try:
                    await self.alert_sender(event.message)
                except Exception:  # noqa: BLE001 - event history is already saved.
                    logger.exception("new listing alert notification failed")
        self._last_run_at[item.id] = observed_at
        logger.debug(
            "new listing watch collected symbol=%s exchanges=%s samples=%s elapsed=%.3fs",
            item.symbol,
            ",".join(item.exchanges),
            len(samples),
            time.perf_counter() - started,
        )
        return samples

    async def status(self) -> NewListingMonitorStatus:
        watchlist = await self.repo.list_watch_items()
        now = utc_now()
        return NewListingMonitorStatus(
            running=self.running(),
            watch_count=len(watchlist),
            enabled_watch_count=sum(1 for item in watchlist if item.enabled),
            active_watch_count=sum(1 for item in watchlist if _watch_is_active(item, now)),
            sample_count=await self.repo.count_samples(),
            event_count=await self.repo.count_events(),
            latest_error=self._latest_error,
            watchlist=watchlist,
            latest_samples=await self.repo.list_samples(limit=80),
            latest_events=await self.repo.list_events(limit=20),
        )

    async def history(
        self,
        *,
        watch_id: str | None,
        symbol: str | None,
        start_at: datetime,
        end_at: datetime,
        limit: int,
    ) -> NewListingHistoryResult:
        start = _as_utc(start_at)
        end = _as_utc(end_at)
        samples = await self.repo.list_samples(
            watch_id=watch_id,
            symbol=symbol,
            start_at=start,
            end_at=end,
            limit=limit,
        )
        events = await self.repo.list_events(
            watch_id=watch_id,
            symbol=symbol,
            start_at=start,
            end_at=end,
            limit=500,
        )
        chronological = list(reversed(samples))
        max_sample = max(chronological, key=lambda item: item.net_spread_pct, default=None)
        warnings: list[str] = []
        if not samples:
            warnings.append("该时间段没有新币极速秒级记录，无法证明当时实时盘口是否可成交。")
        return NewListingHistoryResult(
            symbol=symbol,
            watch_id=watch_id,
            start_at=start,
            end_at=end,
            sample_count=len(samples),
            event_count=len(events),
            max_raw_spread_pct=max((item.raw_spread_pct for item in samples), default=None),
            max_net_spread_pct=max((item.net_spread_pct for item in samples), default=None),
            max_sample=max_sample,
            samples=chronological,
            events=list(reversed(events)),
            warnings=warnings,
        )

    def _spread_samples(
        self,
        item: NewListingWatchItem,
        market_samples: list[SecondLevelMarketSample],
        *,
        observed_at: datetime,
    ) -> list[NewListingSpreadSample]:
        valid = [sample for sample in market_samples if _leg_bid(sample, item.market_type) and _leg_ask(sample, item.market_type)]
        samples: list[NewListingSpreadSample] = []
        for left, right in combinations(valid, 2):
            sample = _best_direction_sample(item, left, right, observed_at=observed_at)
            if sample is None:
                continue
            self._classify_sample(item, sample)
            samples.append(sample)
        return sorted(samples, key=lambda sample: sample.net_spread_pct, reverse=True)

    def _classify_sample(self, item: NewListingWatchItem, sample: NewListingSpreadSample) -> None:
        sample.risk_labels = _risk_labels(item, sample)
        level = _alert_level(item, sample.net_spread_pct)
        sample.alert_level = level
        blockers = _alert_blockers(item, sample)
        if blockers:
            sample.no_alert_reason = "；".join(blockers)
            self._hits.pop(_hit_key(sample), None)
            return
        if level == "none":
            sample.no_alert_reason = f"净价差 {sample.net_spread_pct:.3f}% 低于普通提醒阈值 {item.normal_threshold_pct:.3f}%"
            self._hits.pop(_hit_key(sample), None)
            return

        key = _hit_key(sample)
        count = self._hits.get(key, 0) + 1
        self._hits[key] = count
        required_hits = _required_hits(item, level)
        if count < required_hits:
            sample.no_alert_reason = f"连续确认中：{count}/{required_hits}"
            return
        now = sample.observed_at
        last_sent = self._last_sent.get(key)
        if last_sent and (now - last_sent).total_seconds() < item.cooldown_seconds:
            remaining = item.cooldown_seconds - int((now - last_sent).total_seconds())
            sample.no_alert_reason = f"冷却中，约 {max(0, remaining)} 秒后可再次提醒"
            return
        sample.alert_triggered = True
        sample.no_alert_reason = None
        self._last_sent[key] = now

    def _alert_message(self, sample: NewListingSpreadSample) -> str:
        level_text = {"normal": "普通", "strong": "强提醒", "extreme": "极端"}.get(sample.alert_level, "提醒")
        notional = (
            f"{sample.executable_notional_usdt:.2f} USDT"
            if sample.executable_notional_usdt is not None
            else "深度未知"
        )
        risks = "、".join(sample.risk_labels) if sample.risk_labels else "无"
        return (
            f"新币极速价差{level_text}｜{sample.symbol} {sample.market_type.value}\n"
            f"方向：{sample.buy_exchange} 买入 / {sample.sell_exchange} 卖出\n"
            f"净价差：{sample.net_spread_pct:+.3f}%（原始 {sample.raw_spread_pct:+.3f}%）\n"
            f"买入 ask：{sample.buy_price:.10g}，卖出 bid：{sample.sell_price:.10g}\n"
            f"可成交金额：{notional}\n"
            f"风险标签：{risks}\n"
            f"时间：{sample.observed_at.astimezone(UTC).isoformat()}"
        )

    async def _append_astro_result(self, sample: NewListingSpreadSample, message: str) -> str:
        blocker = _astro_card_blocker(sample)
        if blocker is not None:
            return f"{message}\nAstro: {blocker}"
        handler = self.astro_alert_handler
        if handler is None:
            return message
        try:
            result = await handler(_opportunity_from_new_listing_sample(sample))
        except Exception as exc:  # noqa: BLE001 - alert event should still be persisted and sent.
            logger.exception("new listing astro auto-create failed")
            return f"{message}\nAstro: 新币自动建卡失败：{_error_message(exc)}"
        return f"{message}\n{result.format_message()}"


def _leg_bid(sample: SecondLevelMarketSample, market_type: MarketType) -> float | None:
    return _positive(sample.spot_bid if market_type == MarketType.SPOT else sample.future_bid)


def _apply_symbol_alias_to_market_sample(
    sample: SecondLevelMarketSample,
    *,
    alias: ResolvedSymbolAlias,
    display_symbol: str,
) -> SecondLevelMarketSample:
    multiplier = alias.price_multiplier

    def scale_price(value: float | None) -> float | None:
        return value * multiplier if value is not None else None

    def scale_size(value: float | None) -> float | None:
        return value / multiplier if value is not None else None

    return sample.model_copy(
        update={
            "symbol": display_symbol,
            "spot_bid": scale_price(sample.spot_bid),
            "spot_ask": scale_price(sample.spot_ask),
            "spot_bid_size": scale_size(sample.spot_bid_size),
            "spot_ask_size": scale_size(sample.spot_ask_size),
            "spot_mid": scale_price(sample.spot_mid),
            "spot_last": scale_price(sample.spot_last),
            "future_bid": scale_price(sample.future_bid),
            "future_ask": scale_price(sample.future_ask),
            "future_bid_size": scale_size(sample.future_bid_size),
            "future_ask_size": scale_size(sample.future_ask_size),
            "future_mid": scale_price(sample.future_mid),
            "future_last": scale_price(sample.future_last),
            "mark_price": scale_price(sample.mark_price),
            "index_price": scale_price(sample.index_price),
        }
    )


def _leg_ask(sample: SecondLevelMarketSample, market_type: MarketType) -> float | None:
    return _positive(sample.spot_ask if market_type == MarketType.SPOT else sample.future_ask)


def _leg_bid_size(sample: SecondLevelMarketSample, market_type: MarketType) -> float | None:
    return _positive(sample.spot_bid_size if market_type == MarketType.SPOT else sample.future_bid_size)


def _leg_ask_size(sample: SecondLevelMarketSample, market_type: MarketType) -> float | None:
    return _positive(sample.spot_ask_size if market_type == MarketType.SPOT else sample.future_ask_size)


def _direction_sample(
    item: NewListingWatchItem,
    buy: SecondLevelMarketSample,
    sell: SecondLevelMarketSample,
    *,
    observed_at: datetime,
) -> NewListingSpreadSample | None:
    buy_ask = _leg_ask(buy, item.market_type)
    sell_bid = _leg_bid(sell, item.market_type)
    if buy_ask is None or sell_bid is None:
        return None
    raw_spread_pct = (sell_bid - buy_ask) / buy_ask * 100
    net_spread_pct = raw_spread_pct - item.buy_fee_pct - item.sell_fee_pct - item.slippage_buffer_pct
    buy_ask_size = _leg_ask_size(buy, item.market_type)
    sell_bid_size = _leg_bid_size(sell, item.market_type)
    buy_depth = buy_ask * buy_ask_size if buy_ask_size is not None else None
    sell_depth = sell_bid * sell_bid_size if sell_bid_size is not None else None
    known_depths = [value for value in (buy_depth, sell_depth) if value is not None]
    executable_notional = min(known_depths) if len(known_depths) == 2 else None
    return NewListingSpreadSample(
        watch_id=item.id,
        observed_at=observed_at,
        symbol=item.symbol,
        market_type=item.market_type,
        buy_exchange=buy.exchange,
        sell_exchange=sell.exchange,
        buy_bid=_leg_bid(buy, item.market_type),
        buy_ask=buy_ask,
        buy_bid_size=_leg_bid_size(buy, item.market_type),
        buy_ask_size=buy_ask_size,
        sell_bid=sell_bid,
        sell_ask=_leg_ask(sell, item.market_type),
        sell_bid_size=sell_bid_size,
        sell_ask_size=_leg_ask_size(sell, item.market_type),
        buy_price=buy_ask,
        sell_price=sell_bid,
        raw_spread_pct=raw_spread_pct,
        net_spread_pct=net_spread_pct,
        executable_notional_usdt=executable_notional,
        buy_latency_ms=buy.latency_ms,
        sell_latency_ms=sell.latency_ms,
    )


def _best_direction_sample(
    item: NewListingWatchItem,
    left: SecondLevelMarketSample,
    right: SecondLevelMarketSample,
    *,
    observed_at: datetime,
) -> NewListingSpreadSample | None:
    candidates = [
        sample
        for sample in (
            _direction_sample(item, left, right, observed_at=observed_at),
            _direction_sample(item, right, left, observed_at=observed_at),
        )
        if sample is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda sample: sample.raw_spread_pct)


def _alert_level(item: NewListingWatchItem, net_spread_pct: float) -> NewListingAlertLevel:
    if net_spread_pct >= item.extreme_threshold_pct:
        return "extreme"
    if net_spread_pct >= item.strong_threshold_pct:
        return "strong"
    if net_spread_pct >= item.normal_threshold_pct:
        return "normal"
    return "none"


def _required_hits(item: NewListingWatchItem, level: NewListingAlertLevel) -> int:
    if level == "extreme":
        return item.extreme_consecutive_hits
    if level == "strong":
        return item.strong_consecutive_hits
    return item.normal_consecutive_hits


def _alert_blockers(item: NewListingWatchItem, sample: NewListingSpreadSample) -> list[str]:
    blockers: list[str] = []
    if (
        sample.executable_notional_usdt is not None
        and sample.executable_notional_usdt < item.min_executable_notional_usdt
    ):
        blockers.append(
            f"可成交金额 {sample.executable_notional_usdt:.2f} USDT 低于最低要求 "
            f"{item.min_executable_notional_usdt:.2f} USDT"
        )
    return blockers


def _risk_labels(item: NewListingWatchItem, sample: NewListingSpreadSample) -> list[str]:
    labels = ["NEW_LISTING"]
    if sample.executable_notional_usdt is None:
        labels.append("DEPTH_UNKNOWN")
    elif sample.executable_notional_usdt < item.min_executable_notional_usdt:
        labels.append("DEPTH_TOO_SMALL")
    elif sample.executable_notional_usdt < item.depth_validation_notional_usdt:
        labels.append("DEPTH_BELOW_TARGET")
    if sample.buy_latency_ms and sample.buy_latency_ms > 3000:
        labels.append("BUY_SLOW_DATA")
    if sample.sell_latency_ms and sample.sell_latency_ms > 3000:
        labels.append("SELL_SLOW_DATA")
    if item.allow_low_liquidity_alert:
        labels.append("LOW_LIQUIDITY_ALLOWED")
    return labels


def _astro_card_blocker(sample: NewListingSpreadSample) -> str | None:
    if sample.market_type != MarketType.FUTURE:
        return "现货跨所新币暂不自动建卡"
    if sample.net_spread_pct <= 0:
        return f"净价差 {sample.net_spread_pct:.3f}% 非正，未自动建卡"
    if sample.executable_notional_usdt is None:
        return "盘口深度未知，未自动建卡"
    if {"BUY_SLOW_DATA", "SELL_SLOW_DATA"}.intersection(sample.risk_labels):
        return "盘口延迟过高，未自动建卡"
    return None


def _depth_notional(price: float | None, size: float | None) -> float | None:
    if price is None or size is None:
        return None
    return price * size


def _opportunity_risk_labels(sample: NewListingSpreadSample) -> list[str]:
    labels = [NEW_LISTING_RISK_LABEL]
    for label in sample.risk_labels:
        if label.upper() == NEW_LISTING_RISK_LABEL:
            continue
        labels.append(label)
    return labels


def _opportunity_from_new_listing_sample(sample: NewListingSpreadSample) -> Opportunity:
    opportunity_type = OpportunityType.FF if sample.market_type == MarketType.FUTURE else OpportunityType.SS
    return Opportunity(
        id=(
            f"new-listing:{sample.watch_id}:{sample.market_type.value}:"
            f"{sample.buy_exchange}->{sample.sell_exchange}"
        ),
        type=opportunity_type,
        symbol=sample.symbol,
        buy_exchange=sample.buy_exchange,
        buy_market_type=sample.market_type,
        buy_raw_symbol=None,
        sell_exchange=sample.sell_exchange,
        sell_market_type=sample.market_type,
        sell_raw_symbol=None,
        open_spread_pct=sample.raw_spread_pct,
        close_spread_pct=0,
        fee_adjusted_open_pct=sample.net_spread_pct,
        spread_width_pct=abs(sample.raw_spread_pct),
        buy_bid=sample.buy_bid or sample.buy_price,
        buy_ask=sample.buy_ask or sample.buy_price,
        sell_bid=sample.sell_bid or sample.sell_price,
        sell_ask=sample.sell_ask or sample.sell_price,
        buy_bid_depth_usdt=_depth_notional(sample.buy_bid, sample.buy_bid_size),
        buy_ask_depth_usdt=_depth_notional(sample.buy_ask, sample.buy_ask_size),
        sell_bid_depth_usdt=_depth_notional(sample.sell_bid, sample.sell_bid_size),
        sell_ask_depth_usdt=_depth_notional(sample.sell_ask, sample.sell_ask_size),
        min_open_depth_usdt=sample.executable_notional_usdt,
        buy_volume_24h_usdt=None,
        sell_volume_24h_usdt=None,
        funding_rate_buy_pct=None,
        funding_rate_sell_pct=None,
        funding_next_rate_buy_pct=None,
        funding_next_rate_sell_pct=None,
        funding_next_time_buy=None,
        funding_next_time_sell=None,
        net_funding_pct=None,
        net_funding_next_pct=None,
        buy_funding_interval_hours=None,
        sell_funding_interval_hours=None,
        net_funding_hourly_pct=None,
        net_funding_daily_pct=None,
        net_funding_next_hourly_pct=None,
        net_funding_next_daily_pct=None,
        mark_index_diff_buy_pct=None,
        mark_index_diff_sell_pct=None,
        risk_labels=_opportunity_risk_labels(sample),
        last_seen_at=sample.observed_at,
    )


def _hit_key(sample: NewListingSpreadSample) -> str:
    return (
        f"{sample.watch_id}:{sample.market_type.value}:"
        f"{sample.buy_exchange}->{sample.sell_exchange}"
    )
