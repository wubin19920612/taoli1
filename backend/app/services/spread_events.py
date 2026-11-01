import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from heapq import heappush, heappushpop
from itertools import count, pairwise
from statistics import median

import aiosqlite

from app.db.repositories import PERCENT_SCALE
from app.models.spread_events import (
    SpreadEvent,
    SpreadEventLeg,
    SpreadEventsParams,
    SpreadEventsResponse,
)

MAX_SCANNED_ROWS = 500_000


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _leg(row, side: str) -> SpreadEventLeg:
    # Old history has no raw-symbol columns. Never infer an HL DEX from a ticker.
    identity = json.loads(row["market_identity_json"] or "{}")
    market = identity.get(f"{side}_market") or {}
    return SpreadEventLeg(
        exchange=row[f"{side}_exchange"],
        market_type=row[f"{side}_market_type"],
        raw_symbol=market.get("raw_symbol"),
        dex=market.get("dex"),
    )


def aggregate_spread_events(rows, params: SpreadEventsParams) -> tuple[list[SpreadEvent], int]:
    """Aggregate only observed above-threshold samples; do not extrapolate a tail."""
    series = defaultdict(list)
    for row in rows:
        series[row["opportunity_id"]].append(row)
    field = "fee_adjusted_open_scaled" if params.metric == "fee_adjusted" else "open_spread_scaled"
    sort_field = {"peak": "peak", "duration": "duration_s", "start": "start_at"}[params.sort]
    events = []
    sequence = count()

    def finish(event, total, reason, end_at):
        if event is None:
            return
        event.end_at = end_at
        event.duration_s = (end_at - event.start_at).total_seconds()
        event.end_reason = reason
        event.mean = total / event.samples
        if event.duration_s >= params.min_duration_s:
            key = (getattr(event, sort_field), event.start_at, event.series_key)
            entry = (key, next(sequence), event)
            if len(events) < params.limit:
                heappush(events, entry)
            else:
                heappushpop(events, entry)

    for key, points in series.items():
        points.sort(key=lambda row: (row["observed_at"], row["id"]))
        times = [_utc(row["observed_at"]) for row in points]
        intervals = [
            (right - left).total_seconds()
            for left, right in pairwise(times) if right > left
        ]
        max_gap = max(120.0, 3 * median(intervals)) if intervals else 120.0
        active = None
        total = 0.0
        previous = None

        for row, at in zip(points, times):
            if active is not None and (at - previous).total_seconds() > max_gap:
                finish(active, total, "gap", previous)
                active = None
            value = row[field] / PERCENT_SCALE
            if value >= params.threshold:
                if active is None:
                    active = SpreadEvent(
                        series_key=key, symbol=row["symbol"],
                        buy_leg=_leg(row, "buy"), sell_leg=_leg(row, "sell"),
                        start_at=at, end_at=at, duration_s=0,
                        peak=value, peak_at=at, mean=value, samples=0,
                        end_reason="ongoing",
                    )
                    total = 0.0
                total += value
                active.samples += 1
                if value > active.peak:
                    active.peak, active.peak_at = value, at
            elif active is not None:
                finish(active, total, "below", at)
                active = None
            previous = at
        if active is not None:
            finish(active, total, "ongoing", previous)
    return [event for _, _, event in sorted(events, reverse=True)], len(series)


async def query_spread_events(
    db: aiosqlite.Connection, params: SpreadEventsParams,
) -> SpreadEventsResponse:
    # Keep the timestamp column bare so SQLite can use its existing time index.
    where = "observed_at >= ? AND observed_at <= ?"
    values = [params.start_at.isoformat(), params.end_at.isoformat()]
    if params.symbol:
        where += " AND symbol = ?"
        values.append(params.symbol)
    async with db.execute(
        f"""SELECT id, observed_at, opportunity_id, symbol,
                   buy_exchange, buy_market_type, sell_exchange, sell_market_type,
                   open_spread_scaled, fee_adjusted_open_scaled, market_identity_json
            FROM opportunity_history WHERE {where}
            ORDER BY observed_at ASC, id ASC LIMIT ?""",
        [*values, MAX_SCANNED_ROWS],
    ) as cursor:
        rows = await cursor.fetchall()
    truncated = False
    if len(rows) == MAX_SCANNED_ROWS:
        # Probe for overflow without loading a 500001st history row.
        async with db.execute(
            f"SELECT EXISTS(SELECT 1 FROM opportunity_history WHERE {where} LIMIT 1 OFFSET ?)",
            [*values, MAX_SCANNED_ROWS],
        ) as cursor:
            truncated = bool((await cursor.fetchone())[0])
    events, series_count = await asyncio.to_thread(aggregate_spread_events, rows, params)
    return SpreadEventsResponse(
        events=events, scanned_rows=len(rows), series_count=series_count,
        truncated=truncated, params=params,
    )
