from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Callable

import aiosqlite

from app.exchanges.base import utc_now
from app.models.pair_spread import (
    PAIR_SPREAD_FUNDING_RECORD_INTERVAL_SECONDS,
    PairSpreadFundingRecordRequest,
    PairSpreadFundingRecordStatus,
    PairSpreadFundingWatchItem,
    PairSpreadLegQuery,
    PairSpreadRealtimeFundingPoint,
)
from app.services.pair_spread_query import (
    PairSpreadQueryService,
    _append_unique,
    _floor_interval,
    _realtime_cache_key,
    _realtime_funding_point,
    _scale_current_leg,
)

logger = logging.getLogger(__name__)

PAIR_SPREAD_FUNDING_RECORD_RETENTION_HOURS = 720


def pair_spread_funding_record_key(request: PairSpreadFundingRecordRequest) -> str:
    return _realtime_cache_key(
        request.leg1,
        request.leg2,
        leg2_multiplier=request.leg2_multiplier,
        interval_seconds=PAIR_SPREAD_FUNDING_RECORD_INTERVAL_SECONDS,
    )


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class PairSpreadFundingRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def upsert_watch(
        self,
        request: PairSpreadFundingRecordRequest,
        *,
        pair_key: str,
        now: datetime | None = None,
    ) -> PairSpreadFundingWatchItem:
        observed_at = now or utc_now()
        await self.db.execute(
            """
            INSERT INTO pair_spread_funding_watchlist (
              pair_key,
              leg1_exchange, leg1_market_type, leg1_symbol,
              leg2_exchange, leg2_market_type, leg2_symbol,
              leg2_multiplier, interval_seconds, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pair_key) DO UPDATE SET
              leg1_exchange = excluded.leg1_exchange,
              leg1_market_type = excluded.leg1_market_type,
              leg1_symbol = excluded.leg1_symbol,
              leg2_exchange = excluded.leg2_exchange,
              leg2_market_type = excluded.leg2_market_type,
              leg2_symbol = excluded.leg2_symbol,
              leg2_multiplier = excluded.leg2_multiplier,
              interval_seconds = excluded.interval_seconds,
              updated_at = excluded.updated_at
            """,
            (
                pair_key,
                request.leg1.exchange,
                request.leg1.market_type.value,
                request.leg1.symbol,
                request.leg2.exchange,
                request.leg2.market_type.value,
                request.leg2.symbol,
                request.leg2_multiplier,
                PAIR_SPREAD_FUNDING_RECORD_INTERVAL_SECONDS,
                observed_at.isoformat(),
                observed_at.isoformat(),
            ),
        )
        await self.db.commit()
        item = await self.get_watch(pair_key)
        if item is None:
            raise RuntimeError("failed to save pair spread funding watch item")
        return item

    async def delete_watch(self, pair_key: str) -> None:
        await self.db.execute("DELETE FROM pair_spread_funding_watchlist WHERE pair_key = ?", (pair_key,))
        await self.db.commit()

    async def get_watch(self, pair_key: str) -> PairSpreadFundingWatchItem | None:
        rows = await self._watch_rows("WHERE w.pair_key = ?", (pair_key,))
        return self._watch_item_from_row(rows[0]) if rows else None

    async def list_watch_items(self) -> list[PairSpreadFundingWatchItem]:
        rows = await self._watch_rows("", ())
        return [self._watch_item_from_row(row) for row in rows]

    async def upsert_sample(
        self,
        pair_key: str,
        point: PairSpreadRealtimeFundingPoint,
        *,
        now: datetime | None = None,
    ) -> None:
        observed_at = now or utc_now()
        await self.db.execute(
            """
            INSERT INTO pair_spread_funding_samples (
              pair_key, bucket_at, left_rate_pct, right_rate_pct, net_rate_pct,
              source, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pair_key, bucket_at) DO UPDATE SET
              left_rate_pct = excluded.left_rate_pct,
              right_rate_pct = excluded.right_rate_pct,
              net_rate_pct = excluded.net_rate_pct,
              source = excluded.source,
              updated_at = excluded.updated_at
            """,
            (
                pair_key,
                point.bucket_at.isoformat(),
                point.left_rate_pct,
                point.right_rate_pct,
                point.net_rate_pct,
                point.source,
                observed_at.isoformat(),
                observed_at.isoformat(),
            ),
        )
        await self.db.commit()

    async def list_samples(
        self,
        pair_key: str,
        *,
        since: datetime,
        limit: int,
    ) -> list[PairSpreadRealtimeFundingPoint]:
        cursor = await self.db.execute(
            """
            SELECT *
            FROM pair_spread_funding_samples
            WHERE pair_key = ? AND bucket_at >= ?
            ORDER BY bucket_at DESC
            LIMIT ?
            """,
            (pair_key, since.isoformat(), limit),
        )
        rows = await cursor.fetchall()
        return [self._sample_from_row(row) for row in reversed(rows)]

    async def prune(self, retention_hours: int = PAIR_SPREAD_FUNDING_RECORD_RETENTION_HOURS) -> int:
        cutoff = utc_now() - timedelta(hours=retention_hours)
        cursor = await self.db.execute(
            "DELETE FROM pair_spread_funding_samples WHERE bucket_at < ?",
            (cutoff.isoformat(),),
        )
        await self.db.commit()
        return cursor.rowcount if cursor.rowcount is not None else 0

    async def _watch_rows(self, where: str, params: tuple[object, ...]) -> list[aiosqlite.Row]:
        cursor = await self.db.execute(
            f"""
            SELECT
              w.*,
              COUNT(s.bucket_at) AS sample_count,
              MAX(s.bucket_at) AS latest_sample_at
            FROM pair_spread_funding_watchlist w
            LEFT JOIN pair_spread_funding_samples s ON s.pair_key = w.pair_key
            {where}
            GROUP BY w.pair_key
            ORDER BY w.updated_at DESC
            """,
            params,
        )
        return await cursor.fetchall()

    def _watch_item_from_row(self, row: aiosqlite.Row) -> PairSpreadFundingWatchItem:
        return PairSpreadFundingWatchItem(
            pair_key=row["pair_key"],
            leg1=PairSpreadLegQuery(
                exchange=row["leg1_exchange"],
                market_type=row["leg1_market_type"],
                symbol=row["leg1_symbol"],
            ),
            leg2=PairSpreadLegQuery(
                exchange=row["leg2_exchange"],
                market_type=row["leg2_market_type"],
                symbol=row["leg2_symbol"],
            ),
            leg2_multiplier=row["leg2_multiplier"],
            interval_seconds=row["interval_seconds"],
            created_at=_parse_datetime(row["created_at"]) or utc_now(),
            updated_at=_parse_datetime(row["updated_at"]) or utc_now(),
            sample_count=row["sample_count"],
            latest_sample_at=_parse_datetime(row["latest_sample_at"]),
        )

    def _sample_from_row(self, row: aiosqlite.Row) -> PairSpreadRealtimeFundingPoint:
        return PairSpreadRealtimeFundingPoint(
            bucket_at=_parse_datetime(row["bucket_at"]) or utc_now(),
            left_rate_pct=row["left_rate_pct"],
            right_rate_pct=row["right_rate_pct"],
            net_rate_pct=row["net_rate_pct"],
            source=row["source"],
        )


class PairSpreadFundingRecorder:
    def __init__(
        self,
        repo: PairSpreadFundingRepository,
        *,
        service_factory: Callable[[], PairSpreadQueryService] = PairSpreadQueryService,
        interval_seconds: int = PAIR_SPREAD_FUNDING_RECORD_INTERVAL_SECONDS,
        retention_hours: int = PAIR_SPREAD_FUNDING_RECORD_RETENTION_HOURS,
    ) -> None:
        self.repo = repo
        self.service_factory = service_factory
        self.interval_seconds = interval_seconds
        self.retention_hours = retention_hours
        self._latest_error: str | None = None

    async def upsert_watch(
        self,
        request: PairSpreadFundingRecordRequest,
        *,
        hours: int,
        now: datetime | None = None,
    ) -> PairSpreadFundingRecordStatus:
        pair_key = pair_spread_funding_record_key(request)
        item = await self.repo.upsert_watch(request, pair_key=pair_key, now=now)
        warnings = await self.collect_watch(item, now=now)
        status = await self.status_for(request, hours=hours, now=now)
        return status.model_copy(update={"warnings": warnings})

    async def delete_watch(
        self,
        request: PairSpreadFundingRecordRequest,
        *,
        hours: int,
        now: datetime | None = None,
    ) -> PairSpreadFundingRecordStatus:
        await self.repo.delete_watch(pair_spread_funding_record_key(request))
        return await self.status_for(request, hours=hours, now=now)

    async def status_for(
        self,
        request: PairSpreadFundingRecordRequest,
        *,
        hours: int,
        now: datetime | None = None,
    ) -> PairSpreadFundingRecordStatus:
        observed_at = now or utc_now()
        pair_key = pair_spread_funding_record_key(request)
        item = await self.repo.get_watch(pair_key)
        if item is None:
            return PairSpreadFundingRecordStatus(watched=False)
        samples = await self.repo.list_samples(
            pair_key,
            since=observed_at - timedelta(hours=hours),
            limit=max(120, min(20_000, hours * 60 + 10)),
        )
        return PairSpreadFundingRecordStatus(watched=True, item=item, samples=samples)

    async def collect_once(self, *, now: datetime | None = None) -> None:
        items = await self.repo.list_watch_items()
        if not items:
            return
        semaphore = asyncio.Semaphore(4)

        async def collect(item: PairSpreadFundingWatchItem) -> None:
            async with semaphore:
                try:
                    await self.collect_watch(item, now=now)
                except Exception as exc:  # noqa: BLE001 - one pair should not block the rest.
                    logger.exception(
                        "pair spread funding record collection failed for %s",
                        item.pair_key,
                    )

        await asyncio.gather(*(collect(item) for item in items))

    async def collect_watch(
        self,
        item: PairSpreadFundingWatchItem,
        *,
        now: datetime | None = None,
    ) -> list[str]:
        observed_at = now or utc_now()
        warnings: list[str] = []
        service = self.service_factory()
        try:
            current_leg1, current_leg2 = await asyncio.gather(
                service._fetch_current_with_warning(item.leg1, warnings),
                service._fetch_current_with_warning(item.leg2, warnings),
            )
            if current_leg1 is None or current_leg2 is None:
                return warnings
            current = service._build_current_snapshot(
                current_leg1,
                _scale_current_leg(current_leg2, item.leg2_multiplier),
                observed_at,
            )
            point = _realtime_funding_point(
                current,
                _floor_interval(observed_at, item.interval_seconds),
            )
            if point is None:
                _append_unique(warnings, "当前两侧都没有资金费率，未写入分钟记录。")
                return warnings
            await self.repo.upsert_sample(
                item.pair_key,
                point.model_copy(update={"source": "minute_record"}),
                now=observed_at,
            )
            return warnings
        finally:
            close = getattr(service, "aclose", None)
            if close is not None:
                await close()

    async def run(self, stop_event: asyncio.Event) -> None:
        prune_counter = 0
        while not stop_event.is_set():
            started = time.perf_counter()
            try:
                await self.collect_once()
                self._latest_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - background recording should keep retrying.
                self._latest_error = str(exc) or exc.__class__.__name__
                logger.exception("pair spread funding recorder cycle failed")
            prune_counter += 1
            if prune_counter >= 60:
                prune_counter = 0
                with suppress(Exception):
                    await self.repo.prune(self.retention_hours)
            elapsed = time.perf_counter() - started
            timeout = max(1.0, self.interval_seconds - elapsed)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=timeout)
            except TimeoutError:
                continue
