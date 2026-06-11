from datetime import UTC, datetime, timedelta
from math import sqrt

from fastapi import APIRouter, Query, Request

from app.models.history import (
    OpportunityHistoryPoint,
    OpportunityHistoryRow,
    OpportunityHistoryStats,
    OpportunitySpreadStats,
)

router = APIRouter()


@router.get("/history/opportunities", response_model=list[OpportunityHistoryRow])
async def list_opportunity_history(
    request: Request,
    symbol: str | None = Query(default=None),
    opportunity_id: str | None = Query(default=None),
    type: str | None = Query(default=None),
    hours: float = Query(default=24, gt=0, le=24 * 30),
    limit: int = Query(default=1000, ge=1, le=10_000),
) -> list[OpportunityHistoryRow]:
    repo = getattr(request.app.state, "history_repo", None)
    if repo is None:
        return []
    since = datetime.now(UTC) - timedelta(hours=hours)
    normalized_symbol = symbol.upper().replace("-", "").replace("_", "") if symbol else None
    normalized_type = type.upper() if type else None
    return await repo.list(
        symbol=normalized_symbol,
        opportunity_id=opportunity_id,
        type=normalized_type,
        since=since,
        limit=limit,
    )


def _percentile(sorted_values: list[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _field_stats(rows: list[OpportunityHistoryRow], field: str) -> OpportunitySpreadStats:
    values = [
        value
        for row in rows
        if isinstance(value := getattr(row, field), int | float)
    ]
    if not values:
        return OpportunitySpreadStats()
    sorted_values = sorted(float(value) for value in values)
    mean = sum(sorted_values) / len(sorted_values)
    variance = sum((value - mean) ** 2 for value in sorted_values) / len(sorted_values)
    stddev = sqrt(variance)
    current_value = getattr(rows[-1], field)
    current = float(current_value) if isinstance(current_value, int | float) else None
    z_score = (current - mean) / stddev if current is not None and stddev > 0 else None
    return OpportunitySpreadStats(
        min=sorted_values[0],
        max=sorted_values[-1],
        mean=mean,
        median=_percentile(sorted_values, 0.5),
        p05=_percentile(sorted_values, 0.05),
        p95=_percentile(sorted_values, 0.95),
        current=current,
        z_score=z_score,
    )


def _sample_rows(rows: list[OpportunityHistoryRow], limit: int) -> list[OpportunityHistoryRow]:
    if len(rows) <= limit:
        return rows
    if limit <= 1:
        return [rows[-1]]
    indexes = {
        round(index * (len(rows) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [row for index, row in enumerate(rows) if index in indexes]


def build_opportunity_history_stats(
    rows: list[OpportunityHistoryRow],
    point_limit: int,
    symbol: str | None = None,
    opportunity_id: str | None = None,
    type: str | None = None,
) -> OpportunityHistoryStats:
    chronological_rows = sorted(rows, key=lambda row: row.observed_at)
    latest = chronological_rows[-1] if chronological_rows else None
    points = [
        OpportunityHistoryPoint(
            observed_at=row.observed_at,
            open_spread_pct=row.open_spread_pct,
            close_spread_pct=row.close_spread_pct,
            fee_adjusted_open_pct=row.fee_adjusted_open_pct,
            funding_rate_buy_pct=row.funding_rate_buy_pct,
            funding_rate_sell_pct=row.funding_rate_sell_pct,
            funding_next_rate_buy_pct=row.funding_next_rate_buy_pct,
            funding_next_rate_sell_pct=row.funding_next_rate_sell_pct,
            funding_next_time_buy=row.funding_next_time_buy,
            funding_next_time_sell=row.funding_next_time_sell,
            net_funding_pct=row.net_funding_pct,
            net_funding_next_pct=row.net_funding_next_pct,
        )
        for row in _sample_rows(chronological_rows, point_limit)
    ]
    return OpportunityHistoryStats(
        symbol=latest.symbol if latest is not None else symbol,
        opportunity_id=latest.opportunity_id if latest is not None else opportunity_id,
        type=latest.type if latest is not None else type,
        count=len(chronological_rows),
        first_seen_at=chronological_rows[0].observed_at if chronological_rows else None,
        last_seen_at=latest.observed_at if latest is not None else None,
        latest=latest,
        open_spread_pct=_field_stats(chronological_rows, "open_spread_pct"),
        close_spread_pct=_field_stats(chronological_rows, "close_spread_pct"),
        fee_adjusted_open_pct=_field_stats(chronological_rows, "fee_adjusted_open_pct"),
        net_funding_pct=_field_stats(chronological_rows, "net_funding_pct"),
        net_funding_next_pct=_field_stats(chronological_rows, "net_funding_next_pct"),
        points=points,
    )


@router.get("/history/opportunities/stats", response_model=OpportunityHistoryStats)
async def get_opportunity_history_stats(
    request: Request,
    symbol: str | None = Query(default=None),
    opportunity_id: str | None = Query(default=None),
    type: str | None = Query(default=None),
    hours: float = Query(default=24 * 7, gt=0, le=24 * 30),
    point_limit: int = Query(default=360, ge=1, le=2000),
) -> OpportunityHistoryStats:
    repo = getattr(request.app.state, "history_repo", None)
    normalized_symbol = symbol.upper().replace("-", "").replace("_", "") if symbol else None
    normalized_type = type.upper() if type else None
    if repo is None:
        return build_opportunity_history_stats(
            [],
            point_limit,
            symbol=normalized_symbol,
            opportunity_id=opportunity_id,
            type=normalized_type,
        )
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = await repo.list(
        symbol=normalized_symbol,
        opportunity_id=opportunity_id,
        type=normalized_type,
        since=since,
        limit=10_000,
    )
    return build_opportunity_history_stats(
        rows,
        point_limit,
        symbol=normalized_symbol,
        opportunity_id=opportunity_id,
        type=normalized_type,
    )
