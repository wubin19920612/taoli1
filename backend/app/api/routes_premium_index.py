from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from app.models.premium_index import (
    PREMIUM_INDEX_INTERVAL_OPTIONS,
    PREMIUM_INDEX_MAX_HOURS,
    PREMIUM_INDEX_MIN_HOURS,
    PremiumIndexCurrentSnapshot,
    PremiumIndexMarketQuery,
    PremiumIndexQueryResult,
    SUPPORTED_PREMIUM_INDEX_EXCHANGES,
)
from app.services.premium_index_query import PremiumIndexQueryError, PremiumIndexQueryService

router = APIRouter(prefix="/premium-index")


@router.get("/exchanges", response_model=list[str])
async def list_premium_index_exchanges() -> list[str]:
    return list(SUPPORTED_PREMIUM_INDEX_EXCHANGES)


@router.get("/query", response_model=PremiumIndexQueryResult)
async def query_premium_index(
    request: Request,
    exchange: str = Query(...),
    symbol: str = Query(...),
    hours: int = Query(default=24, ge=PREMIUM_INDEX_MIN_HOURS, le=PREMIUM_INDEX_MAX_HOURS),
    interval_minutes: int = Query(default=1),
) -> PremiumIndexQueryResult:
    if interval_minutes not in PREMIUM_INDEX_INTERVAL_OPTIONS:
        allowed = ", ".join(str(value) for value in PREMIUM_INDEX_INTERVAL_OPTIONS)
        raise HTTPException(status_code=422, detail=f"interval_minutes must be one of: {allowed}")
    try:
        market = PremiumIndexMarketQuery(exchange=exchange, symbol=symbol)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    factory = getattr(request.app.state, "premium_index_query_service_factory", None) or PremiumIndexQueryService
    service = factory()
    try:
        return await service.query(market, hours=hours, interval_minutes=interval_minutes)
    except PremiumIndexQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()


@router.get("/current", response_model=PremiumIndexCurrentSnapshot)
async def get_current_premium_index(
    request: Request,
    exchange: str = Query(...),
    symbol: str = Query(...),
) -> PremiumIndexCurrentSnapshot:
    try:
        market = PremiumIndexMarketQuery(exchange=exchange, symbol=symbol)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    factory = getattr(request.app.state, "premium_index_query_service_factory", None) or PremiumIndexQueryService
    service = factory()
    try:
        return await service.current(market)
    except Exception as exc:  # noqa: BLE001 - keep exchange error visible to the UI.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()
