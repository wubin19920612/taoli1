from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request

from app.models.history import OpportunityHistoryRow

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
