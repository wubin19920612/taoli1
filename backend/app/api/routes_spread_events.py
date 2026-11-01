from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from app.models.spread_events import SpreadEventsParams, SpreadEventsResponse
from app.services.spread_events import SpreadEventsBusyError, query_spread_events

router = APIRouter()


@router.get("/spread-events", response_model=SpreadEventsResponse)
async def list_spread_events(
    request: Request,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    threshold: float = Query(default=0.3, ge=0, allow_inf_nan=False),
    metric: Literal["fee_adjusted", "raw"] = "fee_adjusted",
    min_duration_s: float = Query(default=0, ge=0, allow_inf_nan=False),
    sort: Literal["peak", "duration", "start"] = "peak",
    limit: int = Query(default=100, ge=1, le=500),
    symbol: str | None = Query(default=None, min_length=1, max_length=160),
) -> SpreadEventsResponse:
    try:
        params = SpreadEventsParams(
            start_at=start_at, end_at=end_at or datetime.now(UTC), threshold=threshold,
            metric=metric, min_duration_s=min_duration_s, sort=sort, limit=limit, symbol=symbol,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = getattr(request.app.state, "history_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="History repository is not ready")
    db_path = request.app.state.settings.sqlite_path
    if db_path == ":memory:":
        raise HTTPException(status_code=503, detail="价差事件查询需要独立只读数据库文件连接")
    try:
        return await query_spread_events(db_path, params)
    except SpreadEventsBusyError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
