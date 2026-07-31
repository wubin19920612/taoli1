from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.market import MarketType
from app.models.pair_spread import (
    PAIR_SPREAD_MAX_INTERVAL_SECONDS,
    PAIR_SPREAD_MAX_HOURS,
    PAIR_SPREAD_MIN_INTERVAL_SECONDS,
    PAIR_SPREAD_MIN_HOURS,
    PAIR_SPREAD_INTERVAL_OPTIONS,
    PairSpreadFundingRecordRequest,
    PairSpreadFundingRecordStatus,
    PairSpreadFundingWatchItem,
    PairSpreadLegQuery,
    PairSpreadQueryResult,
    SUPPORTED_PAIR_SPREAD_EXCHANGES,
)
from app.services.pair_spread_funding_recorder import PairSpreadFundingRecorder
from app.services.pair_spread_query import PairSpreadQueryError, PairSpreadQueryService

router = APIRouter(prefix="/pair-spread")


def _funding_recorder(request: Request) -> PairSpreadFundingRecorder:
    recorder = getattr(request.app.state, "pair_spread_funding_recorder", None)
    if recorder is None:
        raise HTTPException(status_code=503, detail="Pair spread funding recorder is not ready")
    return recorder


def _funding_record_request_from_params(
    *,
    leg1_exchange: str,
    leg1_symbol: str,
    leg1_market_type: MarketType,
    leg2_exchange: str,
    leg2_symbol: str,
    leg2_market_type: MarketType,
    leg2_multiplier: float,
) -> PairSpreadFundingRecordRequest:
    try:
        return PairSpreadFundingRecordRequest(
            leg1=PairSpreadLegQuery(exchange=leg1_exchange, symbol=leg1_symbol, market_type=leg1_market_type),
            leg2=PairSpreadLegQuery(exchange=leg2_exchange, symbol=leg2_symbol, market_type=leg2_market_type),
            leg2_multiplier=leg2_multiplier,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/exchanges", response_model=list[str])
async def list_pair_spread_exchanges() -> list[str]:
    return list(SUPPORTED_PAIR_SPREAD_EXCHANGES)


@router.get("/funding-records/watchlist", response_model=list[PairSpreadFundingWatchItem])
async def list_pair_spread_funding_records(request: Request) -> list[PairSpreadFundingWatchItem]:
    return await _funding_recorder(request).repo.list_watch_items()


@router.get("/funding-records/status", response_model=PairSpreadFundingRecordStatus)
async def get_pair_spread_funding_record_status(
    request: Request,
    leg1_exchange: str = Query(...),
    leg1_symbol: str = Query(...),
    leg1_market_type: MarketType = Query(default=MarketType.FUTURE),
    leg2_exchange: str = Query(...),
    leg2_symbol: str = Query(...),
    leg2_market_type: MarketType = Query(default=MarketType.FUTURE),
    hours: int = Query(default=72, ge=PAIR_SPREAD_MIN_HOURS, le=PAIR_SPREAD_MAX_HOURS),
    leg2_multiplier: float = Query(default=1.0, gt=0),
    end_at: datetime | None = Query(default=None),
) -> PairSpreadFundingRecordStatus:
    record_request = _funding_record_request_from_params(
        leg1_exchange=leg1_exchange,
        leg1_symbol=leg1_symbol,
        leg1_market_type=leg1_market_type,
        leg2_exchange=leg2_exchange,
        leg2_symbol=leg2_symbol,
        leg2_market_type=leg2_market_type,
        leg2_multiplier=leg2_multiplier,
    )
    return await _funding_recorder(request).status_for(record_request, hours=hours, now=end_at)


@router.post("/funding-records/watch", response_model=PairSpreadFundingRecordStatus)
async def start_pair_spread_funding_record(
    payload: PairSpreadFundingRecordRequest,
    request: Request,
    hours: int = Query(default=72, ge=PAIR_SPREAD_MIN_HOURS, le=PAIR_SPREAD_MAX_HOURS),
    password: str | None = Depends(dashboard_password_header),
) -> PairSpreadFundingRecordStatus:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _funding_recorder(request).upsert_watch(payload, hours=hours)


@router.delete("/funding-records/watch", response_model=PairSpreadFundingRecordStatus)
async def stop_pair_spread_funding_record(
    payload: PairSpreadFundingRecordRequest,
    request: Request,
    hours: int = Query(default=72, ge=PAIR_SPREAD_MIN_HOURS, le=PAIR_SPREAD_MAX_HOURS),
    password: str | None = Depends(dashboard_password_header),
) -> PairSpreadFundingRecordStatus:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _funding_recorder(request).delete_watch(payload, hours=hours)


@router.get("/query", response_model=PairSpreadQueryResult)
async def query_pair_spread(
    request: Request,
    leg1_exchange: str = Query(...),
    leg1_symbol: str = Query(...),
    leg1_market_type: MarketType = Query(default=MarketType.FUTURE),
    leg2_exchange: str = Query(...),
    leg2_symbol: str = Query(...),
    leg2_market_type: MarketType = Query(default=MarketType.FUTURE),
    hours: int = Query(default=72, ge=PAIR_SPREAD_MIN_HOURS, le=PAIR_SPREAD_MAX_HOURS),
    interval_minutes: int = Query(default=1),
    interval_seconds: int | None = Query(default=None),
    leg2_multiplier: float = Query(default=1.0, gt=0),
    end_at: datetime | None = Query(default=None),
    include_current: bool = Query(default=True),
) -> PairSpreadQueryResult:
    if interval_seconds is None and interval_minutes not in PAIR_SPREAD_INTERVAL_OPTIONS:
        allowed = ", ".join(str(value) for value in PAIR_SPREAD_INTERVAL_OPTIONS)
        raise HTTPException(status_code=422, detail=f"interval_minutes must be one of: {allowed}")
    resolved_interval_seconds = interval_seconds if interval_seconds is not None else interval_minutes * 60
    if not PAIR_SPREAD_MIN_INTERVAL_SECONDS <= resolved_interval_seconds <= PAIR_SPREAD_MAX_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=422,
            detail=(
                "interval_seconds must be between "
                f"{PAIR_SPREAD_MIN_INTERVAL_SECONDS} and {PAIR_SPREAD_MAX_INTERVAL_SECONDS}"
            ),
        )
    try:
        leg1 = PairSpreadLegQuery(exchange=leg1_exchange, symbol=leg1_symbol, market_type=leg1_market_type)
        leg2 = PairSpreadLegQuery(exchange=leg2_exchange, symbol=leg2_symbol, market_type=leg2_market_type)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    factory = getattr(request.app.state, "pair_spread_query_service_factory", None) or PairSpreadQueryService
    service = factory()
    try:
        return await service.query(
            leg1,
            leg2,
            hours=hours,
            interval_minutes=interval_minutes,
            interval_seconds=resolved_interval_seconds,
            leg2_multiplier=leg2_multiplier,
            now=end_at,
            include_current=include_current,
        )
    except PairSpreadQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()
