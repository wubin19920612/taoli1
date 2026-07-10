from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from app.models.pair_spread import (
    PAIR_SPREAD_HOUR_OPTIONS,
    PairSpreadLegQuery,
    PairSpreadQueryResult,
    SUPPORTED_PAIR_SPREAD_EXCHANGES,
)
from app.services.pair_spread_query import PairSpreadQueryError, PairSpreadQueryService

router = APIRouter(prefix="/pair-spread")


@router.get("/exchanges", response_model=list[str])
async def list_pair_spread_exchanges() -> list[str]:
    return list(SUPPORTED_PAIR_SPREAD_EXCHANGES)


@router.get("/query", response_model=PairSpreadQueryResult)
async def query_pair_spread(
    request: Request,
    leg1_exchange: str = Query(...),
    leg1_symbol: str = Query(...),
    leg2_exchange: str = Query(...),
    leg2_symbol: str = Query(...),
    hours: int = Query(default=72),
) -> PairSpreadQueryResult:
    if hours not in PAIR_SPREAD_HOUR_OPTIONS:
        allowed = ", ".join(str(value) for value in PAIR_SPREAD_HOUR_OPTIONS)
        raise HTTPException(status_code=422, detail=f"hours must be one of: {allowed}")
    try:
        leg1 = PairSpreadLegQuery(exchange=leg1_exchange, symbol=leg1_symbol)
        leg2 = PairSpreadLegQuery(exchange=leg2_exchange, symbol=leg2_symbol)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    factory = getattr(request.app.state, "pair_spread_query_service_factory", None) or PairSpreadQueryService
    service = factory()
    try:
        return await service.query(leg1, leg2, hours=hours)
    except PairSpreadQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()
