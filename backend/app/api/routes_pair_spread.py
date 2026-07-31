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
    PairSpreadDiagnosticResult,
    PairSpreadLegQuery,
    PairSpreadQueryResult,
    SUPPORTED_PAIR_SPREAD_EXCHANGES,
)
from app.models.settings import AlertMessageTemplateSettings
from app.services.pair_spread_funding_recorder import PairSpreadFundingRecorder
from app.services.pair_spread_diagnostics import build_pair_spread_diagnostic
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


def _diagnostic_historical_interval_seconds(requested: int) -> int:
    if requested <= 60:
        return 60
    if requested <= 300:
        return 300
    return 900


@router.get("/diagnostics", response_model=PairSpreadDiagnosticResult)
async def diagnose_pair_spread(
    request: Request,
    leg1_exchange: str = Query(...),
    leg1_symbol: str = Query(...),
    leg1_market_type: MarketType = Query(default=MarketType.FUTURE),
    leg2_exchange: str = Query(...),
    leg2_symbol: str = Query(...),
    leg2_market_type: MarketType = Query(default=MarketType.FUTURE),
    hours: int = Query(default=24, ge=PAIR_SPREAD_MIN_HOURS, le=PAIR_SPREAD_MAX_HOURS),
    threshold_pct: float = Query(default=1.0, ge=0, le=100_000),
    interval_seconds: int = Query(
        default=60,
        ge=PAIR_SPREAD_MIN_INTERVAL_SECONDS,
        le=PAIR_SPREAD_MAX_INTERVAL_SECONDS,
    ),
    leg2_multiplier: float = Query(default=1.0, gt=0),
    end_at: datetime | None = Query(default=None),
) -> PairSpreadDiagnosticResult:
    try:
        leg1 = PairSpreadLegQuery(exchange=leg1_exchange, symbol=leg1_symbol, market_type=leg1_market_type)
        leg2 = PairSpreadLegQuery(exchange=leg2_exchange, symbol=leg2_symbol, market_type=leg2_market_type)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    historical_interval_seconds = _diagnostic_historical_interval_seconds(interval_seconds)
    factory = getattr(request.app.state, "pair_spread_query_service_factory", None) or PairSpreadQueryService
    service = factory()
    try:
        result = await service.query(
            leg1,
            leg2,
            hours=hours,
            interval_minutes=historical_interval_seconds // 60,
            interval_seconds=historical_interval_seconds,
            leg2_multiplier=leg2_multiplier,
            now=end_at,
            include_current=False,
        )
        rule_repo = getattr(request.app.state, "alert_rule_repo", None)
        event_repo = getattr(request.app.state, "alert_event_repo", None)
        settings_repo = getattr(request.app.state, "settings_repo", None)
        rules = await rule_repo.list() if rule_repo is not None else []
        events = await event_repo.list(limit=500) if event_repo is not None else []
        template = (
            await settings_repo.get_alert_message_template()
            if settings_repo is not None
            else AlertMessageTemplateSettings()
        )
        diagnostic = build_pair_spread_diagnostic(
            result,
            threshold_pct=threshold_pct,
            requested_interval_seconds=interval_seconds,
            interval_seconds=historical_interval_seconds,
            rules=rules,
            events=events,
            suppress_when_card_conditions_fail=template.suppress_when_card_conditions_fail,
        )
        if historical_interval_seconds != interval_seconds:
            diagnostic.notes.insert(
                0,
                (
                    f"诊断使用 {historical_interval_seconds // 60} 分钟历史 K 线；"
                    f"你选择的 {interval_seconds} 秒周期只支持实时采样，不能回溯历史分钟数据。"
                ),
            )
        return diagnostic
    except PairSpreadQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()


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
