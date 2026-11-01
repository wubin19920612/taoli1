import asyncio
import json
import math
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from heapq import heappush, heappushpop
from itertools import count
from pathlib import Path
from statistics import median

from app.db.repositories import PERCENT_SCALE
from app.models.market_identity import MarketKey
from app.models.spread_events import (
    SpreadEvent,
    SpreadEventLeg,
    SpreadEventsParams,
    SpreadEventsResponse,
)

MAX_SCANNED_ROWS = 500_000
FETCH_BATCH_SIZE = 5_000
INTERVAL_ESTIMATE_SAMPLES = 31
QUERY_WAIT_SECONDS = 5.0
_QUERY_SEMAPHORE = asyncio.Semaphore(1)


class SpreadEventsBusyError(Exception):
    pass


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _positive_number(value):
    return value if (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value > 0
    ) else None


def _leg(db, row, identity, side: str, has_bindings: bool) -> SpreadEventLeg:
    # History stores MarketKey + binding revision, not the multipliers themselves.
    # Only the exact historical revision can supply units; never infer from a ticker.
    market = identity.get(f"{side}_market") or {}
    price_multiplier = contract_multiplier = None
    revision = identity.get(f"{side}_revision")
    if has_bindings and market and isinstance(revision, int) and revision > 0:
        try:
            key = MarketKey.model_validate(market)
        except ValueError:
            key = None
        if key and key.exchange == row[f"{side}_exchange"] and (
            key.market_type == row[f"{side}_market_type"]
        ):
            binding_row = db.execute(
                "SELECT payload FROM market_identity_bindings WHERE market_key=? AND revision=?",
                (key.key, revision),
            ).fetchone()
            if binding_row:
                binding = json.loads(binding_row[0])
                # Pending/discovered bindings have default 1, not established units.
                if binding.get("unit_status") == "verified":
                    price_multiplier = _positive_number(binding.get("price_multiplier"))
                    contract_multiplier = _positive_number(binding.get("contract_size"))
    return SpreadEventLeg(
        exchange=row[f"{side}_exchange"], market_type=row[f"{side}_market_type"],
        raw_symbol=market.get("raw_symbol"), dex=market.get("dex"),
        price_multiplier=price_multiplier, contract_size_multiplier=contract_multiplier,
    )


@dataclass(slots=True)
class _Event:
    series_key: str
    start_at: datetime
    end_at: datetime
    peak: float
    peak_at: datetime
    duration_s: float = 0
    mean: float = 0
    samples: int = 0
    end_reason: str = "ongoing"


@dataclass(slots=True)
class _Series:
    active: _Event | None = None
    total: float = 0
    previous: datetime | None = None
    intervals: list[float] = field(default_factory=list)
    max_gap: float = 360


def aggregate_spread_events(rows, params: SpreadEventsParams) -> tuple[list[_Event], int]:
    """Consume time-ordered (series key, observed_at, scaled metric) tuples once.

    Estimate the median of the first 31 positive intervals per series, then freeze.
    Before any interval, assume the default collector cadence of 120s.
    max_gap = max(120s, 3 * estimate). Test each gap BEFORE adding that interval,
    so the current outage cannot raise its own threshold. This is a streaming
    estimate, not the former whole-window median; past events are not reclassified.
    Memory is O(series * 31 + limit), independent of samples/events per series.
    """
    series: dict[str, _Series] = {}
    sort_field = {"peak": "peak", "duration": "duration_s", "start": "start_at"}[params.sort]
    events = []
    sequence = count()

    def finish(state, reason, end_at):
        event = state.active
        if event is None:
            return
        state.active = None
        event.end_at = end_at
        event.duration_s = (end_at - event.start_at).total_seconds()
        event.end_reason = reason
        event.mean = state.total / event.samples
        if event.duration_s >= params.min_duration_s:
            key = (getattr(event, sort_field), event.start_at, event.series_key)
            entry = (key, next(sequence), event)
            if len(events) < params.limit:
                heappush(events, entry)
            else:
                heappushpop(events, entry)

    for key, observed_at, scaled_value in rows:
        state = series.get(key)
        if state is None:
            state = series[key] = _Series()
        at = _utc(observed_at)
        if state.previous is not None:
            interval = (at - state.previous).total_seconds()
            if interval > state.max_gap:
                finish(state, "gap", state.previous)
            if interval > 0 and len(state.intervals) < INTERVAL_ESTIMATE_SAMPLES:
                state.intervals.append(interval)
                state.max_gap = max(120.0, 3 * median(state.intervals))
        value = scaled_value / PERCENT_SCALE
        if value >= params.threshold:
            if state.active is None:
                state.active = _Event(key, at, at, value, at)
                state.total = 0
            state.total += value
            state.active.samples += 1
            if value > state.active.peak:
                state.active.peak, state.active.peak_at = value, at
        else:
            finish(state, "below", at)
        state.previous = at
    for state in series.values():
        finish(state, "ongoing", state.previous)
    return [event for _, _, event in sorted(events, reverse=True)], len(series)


def _query_spread_events(db_path: str | Path, params: SpreadEventsParams) -> SpreadEventsResponse:
    # Open/close in the worker; mode=ro cannot create a missing DB or write to it.
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")  # Consistent snapshot for scan, overflow and selected identities.
        where = "observed_at >= ? AND observed_at <= ?"
        values = [params.start_at.isoformat(), params.end_at.isoformat()]
        if params.symbol:
            where += " AND symbol = ?"
            values.append(params.symbol)
        metric = "fee_adjusted_open_scaled" if params.metric == "fee_adjusted" else "open_spread_scaled"
        scanned_rows = 0
        last_observed_at = None

        def rows():
            nonlocal scanned_rows, last_observed_at
            # Bare timestamp bounds and index order; no identity JSON per sample.
            with closing(db.execute(
                f"SELECT opportunity_id, observed_at, {metric} FROM opportunity_history "
                f"WHERE {where} ORDER BY observed_at ASC LIMIT ?",
                [*values, MAX_SCANNED_ROWS],
            )) as cursor:
                while batch := cursor.fetchmany(FETCH_BATCH_SIZE):
                    for row in batch:
                        scanned_rows += 1
                        last_observed_at = row[1]
                        yield row

        candidates, series_count = aggregate_spread_events(rows(), params)
        truncated = scanned_rows == MAX_SCANNED_ROWS and bool(db.execute(
            f"SELECT EXISTS(SELECT 1 FROM opportunity_history WHERE {where} LIMIT 1 OFFSET ?)",
            [*values, MAX_SCANNED_ROWS],
        ).fetchone()[0])
        has_bindings = bool(db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_identity_bindings'",
        ).fetchone())
        metadata = {}
        events = []
        db.row_factory = sqlite3.Row
        for event in candidates:
            if event.series_key not in metadata:
                # Latest in-window identity once per SELECTED series, never a future row.
                row = db.execute(
                    "SELECT symbol, buy_exchange, buy_market_type, sell_exchange, sell_market_type, "
                    "market_identity_json FROM opportunity_history WHERE opportunity_id=? "
                    "AND observed_at >= ? AND observed_at <= ? ORDER BY observed_at DESC LIMIT 1",
                    (event.series_key, params.start_at.isoformat(), last_observed_at),
                ).fetchone()
                identity = json.loads(row["market_identity_json"] or "{}")
                metadata[event.series_key] = {
                    "symbol": row["symbol"],
                    "buy_leg": _leg(db, row, identity, "buy", has_bindings),
                    "sell_leg": _leg(db, row, identity, "sell", has_bindings),
                }
            events.append(SpreadEvent(**asdict(event), **metadata[event.series_key]))
        return SpreadEventsResponse(
            events=events, scanned_rows=scanned_rows, series_count=series_count,
            truncated=truncated, params=params,
        )


async def query_spread_events(
    db_path: str | Path, params: SpreadEventsParams,
) -> SpreadEventsResponse:
    try:
        await asyncio.wait_for(_QUERY_SEMAPHORE.acquire(), timeout=QUERY_WAIT_SECONDS)
    except TimeoutError as exc:
        raise SpreadEventsBusyError("价差事件查询繁忙，等待超过 5 秒，请稍后重试。") from exc
    worker = asyncio.create_task(asyncio.to_thread(_query_spread_events, db_path, params))

    def completed(task):
        _QUERY_SEMAPHORE.release()
        if not task.cancelled():
            task.exception()  # Retrieve errors even if the HTTP request was cancelled.

    worker.add_done_callback(completed)
    # Request cancellation cannot stop SQLite's thread. Keep the permit until it
    # closes its connection, rather than allowing overlapping background queries.
    return await asyncio.shield(worker)
