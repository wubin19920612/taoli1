from datetime import UTC, datetime, timedelta

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
    PairSpreadFundingHistoryResult,
    PairSpreadDiagnosticResult,
    PairSpreadLegQuery,
    PairSpreadQueryResult,
    SUPPORTED_PAIR_SPREAD_EXCHANGES,
    SUPPORTED_SYMBOL_SPREAD_EXCHANGES,
    SymbolSpreadQueryResult,
)
from app.models.settings import AlertMessageTemplateSettings
from app.services.pair_spread_funding_recorder import PairSpreadFundingRecorder
from app.services.pair_spread_diagnostics import build_pair_spread_diagnostic
from app.services.pair_spread_query import PairSpreadQueryError, PairSpreadQueryService
from app.services.symbol_aliases import (
    ResolvedSymbolAlias,
    SymbolAliasResolver,
    apply_pair_spread_funding_aliases,
    apply_pair_spread_symbol_aliases,
)

router = APIRouter(prefix="/pair-spread")


def _funding_recorder(request: Request) -> PairSpreadFundingRecorder:
    recorder = getattr(request.app.state, "pair_spread_funding_recorder", None)
    if recorder is None:
        raise HTTPException(status_code=503, detail="Pair spread funding recorder is not ready")
    return recorder


async def _symbol_alias_resolver(request: Request) -> SymbolAliasResolver:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        return SymbolAliasResolver([])
    settings = await repo.get_risk_settings()
    return SymbolAliasResolver(settings.symbol_aliases)


def _resolved_leg(
    resolver: SymbolAliasResolver,
    *,
    exchange: str,
    symbol: str,
    market_type: MarketType,
) -> tuple[PairSpreadLegQuery, ResolvedSymbolAlias]:
    alias = resolver.resolve(exchange=exchange, symbol=symbol, market_type=market_type)
    return (
        PairSpreadLegQuery(
            exchange=exchange,
            symbol=alias.raw_symbol,
            market_type=market_type,
        ),
        alias,
    )


def _display_funding_status(
    status: PairSpreadFundingRecordStatus,
    *,
    leg1_alias: ResolvedSymbolAlias,
    leg2_alias: ResolvedSymbolAlias,
) -> PairSpreadFundingRecordStatus:
    if status.item is None:
        return status
    return status.model_copy(
        update={
            "item": status.item.model_copy(
                update={
                    "leg1": status.item.leg1.model_copy(update={"symbol": leg1_alias.canonical_symbol}),
                    "leg2": status.item.leg2.model_copy(update={"symbol": leg2_alias.canonical_symbol}),
                }
            )
        }
    )


async def _funding_request_from_payload(
    request: Request,
    payload: PairSpreadFundingRecordRequest,
) -> tuple[PairSpreadFundingRecordRequest, ResolvedSymbolAlias, ResolvedSymbolAlias]:
    resolver = await _symbol_alias_resolver(request)
    leg1, leg1_alias = _resolved_leg(
        resolver,
        exchange=payload.leg1.exchange,
        symbol=payload.leg1.symbol,
        market_type=payload.leg1.market_type,
    )
    leg2, leg2_alias = _resolved_leg(
        resolver,
        exchange=payload.leg2.exchange,
        symbol=payload.leg2.symbol,
        market_type=payload.leg2.market_type,
    )
    return (
        PairSpreadFundingRecordRequest(
            leg1=leg1,
            leg2=leg2,
            leg2_multiplier=payload.leg2_multiplier,
        ),
        leg1_alias,
        leg2_alias,
    )


def _funding_record_request_from_params(
    *,
    resolver: SymbolAliasResolver,
    leg1_exchange: str,
    leg1_symbol: str,
    leg1_market_type: MarketType,
    leg2_exchange: str,
    leg2_symbol: str,
    leg2_market_type: MarketType,
    leg2_multiplier: float,
) -> tuple[PairSpreadFundingRecordRequest, ResolvedSymbolAlias, ResolvedSymbolAlias]:
    try:
        leg1, leg1_alias = _resolved_leg(
            resolver,
            exchange=leg1_exchange,
            symbol=leg1_symbol,
            market_type=leg1_market_type,
        )
        leg2, leg2_alias = _resolved_leg(
            resolver,
            exchange=leg2_exchange,
            symbol=leg2_symbol,
            market_type=leg2_market_type,
        )
        return (
            PairSpreadFundingRecordRequest(
                leg1=leg1,
                leg2=leg2,
                leg2_multiplier=leg2_multiplier,
            ),
            leg1_alias,
            leg2_alias,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/exchanges", response_model=list[str])
async def list_pair_spread_exchanges() -> list[str]:
    return list(SUPPORTED_PAIR_SPREAD_EXCHANGES)


@router.get("/funding-records/watchlist", response_model=list[PairSpreadFundingWatchItem])
async def list_pair_spread_funding_records(request: Request) -> list[PairSpreadFundingWatchItem]:
    resolver = await _symbol_alias_resolver(request)
    items = await _funding_recorder(request).repo.list_watch_items()
    return [
        item.model_copy(
            update={
                "leg1": item.leg1.model_copy(
                    update={
                        "symbol": resolver.resolve(
                            exchange=item.leg1.exchange,
                            symbol=item.leg1.symbol,
                            market_type=item.leg1.market_type,
                        ).canonical_symbol
                    }
                ),
                "leg2": item.leg2.model_copy(
                    update={
                        "symbol": resolver.resolve(
                            exchange=item.leg2.exchange,
                            symbol=item.leg2.symbol,
                            market_type=item.leg2.market_type,
                        ).canonical_symbol
                    }
                ),
            }
        )
        for item in items
    ]


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
    resolver = await _symbol_alias_resolver(request)
    record_request, leg1_alias, leg2_alias = _funding_record_request_from_params(
        resolver=resolver,
        leg1_exchange=leg1_exchange,
        leg1_symbol=leg1_symbol,
        leg1_market_type=leg1_market_type,
        leg2_exchange=leg2_exchange,
        leg2_symbol=leg2_symbol,
        leg2_market_type=leg2_market_type,
        leg2_multiplier=leg2_multiplier,
    )
    status = await _funding_recorder(request).status_for(record_request, hours=hours, now=end_at)
    return _display_funding_status(status, leg1_alias=leg1_alias, leg2_alias=leg2_alias)


@router.post("/funding-records/watch", response_model=PairSpreadFundingRecordStatus)
async def start_pair_spread_funding_record(
    payload: PairSpreadFundingRecordRequest,
    request: Request,
    hours: int = Query(default=72, ge=PAIR_SPREAD_MIN_HOURS, le=PAIR_SPREAD_MAX_HOURS),
    password: str | None = Depends(dashboard_password_header),
) -> PairSpreadFundingRecordStatus:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    record_request, leg1_alias, leg2_alias = await _funding_request_from_payload(request, payload)
    status = await _funding_recorder(request).upsert_watch(record_request, hours=hours)
    return _display_funding_status(status, leg1_alias=leg1_alias, leg2_alias=leg2_alias)


@router.delete("/funding-records/watch", response_model=PairSpreadFundingRecordStatus)
async def stop_pair_spread_funding_record(
    payload: PairSpreadFundingRecordRequest,
    request: Request,
    hours: int = Query(default=72, ge=PAIR_SPREAD_MIN_HOURS, le=PAIR_SPREAD_MAX_HOURS),
    password: str | None = Depends(dashboard_password_header),
) -> PairSpreadFundingRecordStatus:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    record_request, leg1_alias, leg2_alias = await _funding_request_from_payload(request, payload)
    status = await _funding_recorder(request).delete_watch(record_request, hours=hours)
    return _display_funding_status(status, leg1_alias=leg1_alias, leg2_alias=leg2_alias)


def _diagnostic_historical_interval_seconds(requested: int) -> int:
    if requested <= 60:
        return 60
    if requested <= 300:
        return 300
    return 900


def _symbol_spread_exchanges_from_query(value: str | None) -> list[str] | None:
    if value is None:
        return None
    exchanges = [item.strip().lower() for item in value.split(",") if item.strip()]
    return list(dict.fromkeys(exchanges)) or None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
    resolver = await _symbol_alias_resolver(request)
    try:
        leg1, leg1_alias = _resolved_leg(
            resolver,
            exchange=leg1_exchange,
            symbol=leg1_symbol,
            market_type=leg1_market_type,
        )
        leg2, leg2_alias = _resolved_leg(
            resolver,
            exchange=leg2_exchange,
            symbol=leg2_symbol,
            market_type=leg2_market_type,
        )
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
        aliased_result = apply_pair_spread_symbol_aliases(
            result,
            leg1_alias=leg1_alias,
            leg2_alias=leg2_alias,
        )
        diagnostic = build_pair_spread_diagnostic(
            aliased_result,
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


@router.get("/funding-history", response_model=PairSpreadFundingHistoryResult)
async def query_pair_spread_funding_history(
    request: Request,
    leg1_exchange: str = Query(...),
    leg1_symbol: str = Query(...),
    leg1_market_type: MarketType = Query(default=MarketType.FUTURE),
    leg2_exchange: str = Query(...),
    leg2_symbol: str = Query(...),
    leg2_market_type: MarketType = Query(default=MarketType.FUTURE),
    hours: int = Query(default=72, ge=PAIR_SPREAD_MIN_HOURS, le=PAIR_SPREAD_MAX_HOURS),
    leg2_multiplier: float = Query(default=1.0, gt=0),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
) -> PairSpreadFundingHistoryResult:
    resolver = await _symbol_alias_resolver(request)
    try:
        leg1, leg1_alias = _resolved_leg(
            resolver,
            exchange=leg1_exchange,
            symbol=leg1_symbol,
            market_type=leg1_market_type,
        )
        leg2, leg2_alias = _resolved_leg(
            resolver,
            exchange=leg2_exchange,
            symbol=leg2_symbol,
            market_type=leg2_market_type,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    end = _as_utc(end_at) if end_at is not None else datetime.now(UTC)
    start = _as_utc(start_at) if start_at is not None else end - timedelta(hours=hours)
    if start > end:
        raise HTTPException(status_code=422, detail="开始时间不能晚于结束时间")
    duration_hours = (end - start).total_seconds() / 3600
    if duration_hours > PAIR_SPREAD_MAX_HOURS:
        raise HTTPException(status_code=422, detail=f"资金费率统计时间跨度不能超过 {PAIR_SPREAD_MAX_HOURS} 小时")

    factory = getattr(request.app.state, "pair_spread_query_service_factory", None) or PairSpreadQueryService
    service = factory()
    try:
        result = await service.query_funding_history(
            leg1,
            leg2,
            start=start,
            end=end,
        )
        return apply_pair_spread_funding_aliases(
            result,
            leg1_alias=leg1_alias,
            leg2_alias=leg2_alias,
        )
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
    resolver = await _symbol_alias_resolver(request)
    try:
        leg1, leg1_alias = _resolved_leg(
            resolver,
            exchange=leg1_exchange,
            symbol=leg1_symbol,
            market_type=leg1_market_type,
        )
        leg2, leg2_alias = _resolved_leg(
            resolver,
            exchange=leg2_exchange,
            symbol=leg2_symbol,
            market_type=leg2_market_type,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    factory = getattr(request.app.state, "pair_spread_query_service_factory", None) or PairSpreadQueryService
    service = factory()
    try:
        result = await service.query(
            leg1,
            leg2,
            hours=hours,
            interval_minutes=interval_minutes,
            interval_seconds=resolved_interval_seconds,
            leg2_multiplier=leg2_multiplier,
            now=end_at,
            include_current=include_current,
        )
        return apply_pair_spread_symbol_aliases(
            result,
            leg1_alias=leg1_alias,
            leg2_alias=leg2_alias,
        )
    except PairSpreadQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()


@router.get("/symbol-query", response_model=SymbolSpreadQueryResult)
async def query_symbol_spread(
    request: Request,
    symbol: str = Query(...),
    market_type: MarketType = Query(default=MarketType.FUTURE),
    base_exchange: str = Query(default="binance"),
    exchanges: str | None = Query(default=None),
    hours: int = Query(default=24, ge=PAIR_SPREAD_MIN_HOURS, le=PAIR_SPREAD_MAX_HOURS),
    interval_seconds: int = Query(
        default=60,
        ge=PAIR_SPREAD_MIN_INTERVAL_SECONDS,
        le=PAIR_SPREAD_MAX_INTERVAL_SECONDS,
    ),
    end_at: datetime | None = Query(default=None),
    include_current: bool = Query(default=True),
) -> SymbolSpreadQueryResult:
    allowed = set(SUPPORTED_SYMBOL_SPREAD_EXCHANGES)
    normalized_base_exchange = base_exchange.strip().lower()
    if normalized_base_exchange not in allowed:
        allowed_text = ", ".join(SUPPORTED_SYMBOL_SPREAD_EXCHANGES)
        raise HTTPException(status_code=422, detail=f"base_exchange must be one of: {allowed_text}")
    requested_exchanges = _symbol_spread_exchanges_from_query(exchanges)
    if requested_exchanges is not None:
        unsupported = [exchange for exchange in requested_exchanges if exchange not in allowed]
        if unsupported:
            allowed_text = ", ".join(SUPPORTED_SYMBOL_SPREAD_EXCHANGES)
            raise HTTPException(
                status_code=422,
                detail=f"unsupported exchanges: {', '.join(unsupported)}; allowed: {allowed_text}",
            )

    resolver = await _symbol_alias_resolver(request)
    normalized_scope = requested_exchanges or list(SUPPORTED_SYMBOL_SPREAD_EXCHANGES)
    normalized_exchanges = list(dict.fromkeys([normalized_base_exchange, *normalized_scope]))
    aliases_by_exchange = {
        exchange: resolver.resolve(
            exchange=exchange,
            symbol=symbol,
            market_type=market_type,
        )
        for exchange in normalized_exchanges
    }
    display_symbol = next(iter(aliases_by_exchange.values())).canonical_symbol
    legs_by_exchange = {
        exchange: PairSpreadLegQuery(
            exchange=exchange,
            symbol=alias.raw_symbol,
            market_type=market_type,
        )
        for exchange, alias in aliases_by_exchange.items()
    }
    price_multipliers_by_exchange = {
        exchange: alias.price_multiplier
        for exchange, alias in aliases_by_exchange.items()
    }

    factory = getattr(request.app.state, "pair_spread_query_service_factory", None) or PairSpreadQueryService
    service = factory()
    try:
        return await service.query_symbol_spreads(
            symbol,
            market_type=market_type,
            base_exchange=normalized_base_exchange,
            exchanges=requested_exchanges,
            hours=hours,
            interval_seconds=interval_seconds,
            now=end_at,
            include_current=include_current,
            legs_by_exchange=legs_by_exchange,
            price_multipliers_by_exchange=price_multipliers_by_exchange,
            display_symbol=display_symbol,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PairSpreadQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()
