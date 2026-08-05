from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from itertools import combinations
from math import isfinite
from typing import Any

import aiosqlite

from app.models.market import MarketType
from app.models.new_listing import (
    NewListingAlertEvent,
    NewListingAlertLevel,
    NewListingHistoryResult,
    NewListingMonitorStatus,
    NewListingSpreadSample,
    NewListingWatchItem,
)
from app.models.second_level_sampling import SecondLevelMarketSample
from app.services.second_level_sampler import SecondLevelMarketFetcher

logger = logging.getLogger(__name__)


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


class NewListingMonitor:
    def __init__(
        self,
        repo: NewListingMonitorRepository,
        fetcher: SecondLevelMarketFetcher | None = None,
        alert_sender: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.repo = repo
        self.fetcher = fetcher or SecondLevelMarketFetcher()
        self.alert_sender = alert_sender
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
        items = [item for item in await self.repo.list_watch_items() if item.enabled]
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
        exchange_samples = await asyncio.gather(
            *(self.fetcher.fetch(exchange, item.symbol) for exchange in item.exchanges),
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
                market_samples.append(result)

        samples = self._spread_samples(item, market_samples, observed_at=observed_at)
        await self.repo.insert_samples(samples)
        for sample in samples:
            if not sample.alert_triggered:
                continue
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
                message=self._alert_message(sample),
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
        return NewListingMonitorStatus(
            running=self.running(),
            watch_count=len(watchlist),
            enabled_watch_count=sum(1 for item in watchlist if item.enabled),
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


def _leg_bid(sample: SecondLevelMarketSample, market_type: MarketType) -> float | None:
    return _positive(sample.spot_bid if market_type == MarketType.SPOT else sample.future_bid)


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


def _hit_key(sample: NewListingSpreadSample) -> str:
    return (
        f"{sample.watch_id}:{sample.market_type.value}:"
        f"{sample.buy_exchange}->{sample.sell_exchange}"
    )
