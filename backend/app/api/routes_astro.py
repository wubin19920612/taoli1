from fastapi import APIRouter, Depends, HTTPException, Request

from app.db.repositories import SettingsRepository
from app.models.astro import (
    AstroAlertActionResult,
    AstroCardCreateRequest,
    AstroCardRouteVariant,
    AstroInstrumentCardCreateRequest,
    AstroInstrumentRouteRequest,
    AstroPairPlan,
)
from app.models.astro import AstroSdkStatus
from app.models.astro_preadd import (
    PREADD_EXCHANGES,
    AstroPreaddPreview,
    AstroPreaddRunRequest,
    AstroPreaddRunResult,
    AstroPreaddSettings,
)
from app.models.instrument import INSTRUMENT_LOOKUP_EXCHANGES
from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import Opportunity
from app.models.orderbook import DepthValidationResult
from app.models.pair_spread import normalize_pair_spread_symbol
from app.models.settings import AstroCardSettings, FeeSettings, RiskSettings
from app.core.security import dashboard_password_header, verify_dashboard_password
from app.services.astro_alerts import READ_ONLY_ASTRO_MESSAGE, read_only_astro_exchanges
from app.services.astro_client import AstroClientError, AstroSdkClient
from app.services.data_filters import ignored_exchange_set, symbol_is_excluded
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig
from app.services.astro_preadd import AstroPreaddService
from app.services.instrument_spreads import instrument_market_age_seconds
from app.services.market_labels import astro_exchange_route_variants
from app.services.spread_engine import Mode, build_directional_opportunity

router = APIRouter(prefix="/astro")


def _astro_client(request: Request) -> AstroSdkClient:
    client = getattr(request.app.state, "astro_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Astro client is not ready")
    return client


def _require_dashboard_password(request: Request, password: str | None) -> None:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)


def _find_opportunity(request: Request, opportunity_id: str):
    store = getattr(request.app.state, "snapshot_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Snapshot store is not ready")

    opportunity = next(
        (item for item in store.get_opportunities() if item.id == opportunity_id),
        None,
    )
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opportunity


def _manual_override_warning(message: str) -> str:
    return f"{message}；本次为人工建卡，仅作风险提示，未拦截创建"


def _unique_warnings(warnings: list[str]) -> list[str]:
    return list(dict.fromkeys(warnings))


def _instrument_opportunity_type(route: AstroInstrumentRouteRequest) -> Mode:
    if route.buy_market_type == MarketType.FUTURE and route.sell_market_type == MarketType.FUTURE:
        return "FF"
    if route.buy_market_type == MarketType.SPOT and route.sell_market_type == MarketType.SPOT:
        return "SS"
    if route.buy_market_type == MarketType.SPOT and route.sell_market_type == MarketType.FUTURE:
        return "SF"
    return "SF"


def _latest_instrument_market(
    markets: list[MarketSnapshot],
    *,
    symbol: str,
    exchange: str,
    market_type: MarketType,
) -> MarketSnapshot | None:
    matching = [
        market
        for market in markets
        if market.symbol.upper() == symbol
        and market.exchange.lower() == exchange
        and market.market_type == market_type
    ]
    return max(matching, key=lambda market: market.timestamp, default=None)


async def _find_instrument_opportunity(
    request: Request,
    route: AstroInstrumentRouteRequest,
) -> tuple[Opportunity, list[str]]:
    try:
        symbol = normalize_pair_spread_symbol(route.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="请输入有效标的，例如 BTC 或 BTCUSDT") from exc
    buy_exchange = route.buy_exchange.strip().lower()
    sell_exchange = route.sell_exchange.strip().lower()
    allowed_exchanges = set(INSTRUMENT_LOOKUP_EXCHANGES)
    if buy_exchange not in allowed_exchanges or sell_exchange not in allowed_exchanges:
        raise HTTPException(status_code=422, detail="交易所不在标的查询支持范围内")
    if buy_exchange == sell_exchange and route.buy_market_type == route.sell_market_type:
        raise HTTPException(status_code=422, detail="买入侧和卖出侧不能是同一个市场")

    risk_settings, warnings = await _manual_risk_settings(request)
    ignored_exchanges = ignored_exchange_set(risk_settings)
    selected_ignored = sorted({buy_exchange, sell_exchange} & ignored_exchanges)
    if selected_ignored:
        warnings.append(
            _manual_override_warning(
                f"{'、'.join(selected_ignored)} 已在全局忽略交易所列表"
            )
        )

    store = getattr(request.app.state, "snapshot_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Snapshot store is not ready")
    markets = store.get_all_markets()
    buy_market = _latest_instrument_market(
        markets,
        symbol=symbol,
        exchange=buy_exchange,
        market_type=route.buy_market_type,
    )
    sell_market = _latest_instrument_market(
        markets,
        symbol=symbol,
        exchange=sell_exchange,
        market_type=route.sell_market_type,
    )
    missing: list[str] = []
    if buy_market is None:
        missing.append(f"买入侧 {buy_exchange} {route.buy_market_type.value}")
    if sell_market is None:
        missing.append(f"卖出侧 {sell_exchange} {route.sell_market_type.value}")
    if missing:
        raise HTTPException(status_code=404, detail=f"实时行情不存在：{'，'.join(missing)}")

    stale: list[str] = []
    for side, exchange, market in (
        ("买入侧", buy_exchange, buy_market),
        ("卖出侧", sell_exchange, sell_market),
    ):
        age_seconds = instrument_market_age_seconds(market)
        if age_seconds > risk_settings.stale_after_seconds:
            stale.append(
                f"{side} {exchange} {market.market_type.value} 行情已过期 {age_seconds:.1f} 秒"
            )
    if stale:
        warnings.append(_manual_override_warning("；".join(stale)))

    mode = _instrument_opportunity_type(route)
    fees = FeeSettings()
    opportunity = build_directional_opportunity(
        buy_market,
        sell_market,
        mode=mode,
        buy_fee_pct=(
            fees.spot_fee_pct
            if buy_market.market_type == MarketType.SPOT
            else fees.future_fee_pct
        ),
        sell_fee_pct=(
            fees.spot_fee_pct
            if sell_market.market_type == MarketType.SPOT
            else fees.future_fee_pct
        ),
        safety_slippage_pct=fees.safety_slippage_pct,
    )
    return opportunity, warnings


def _settings_repo(request: Request) -> SettingsRepository:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Settings repository is not ready")
    return repo


def _optional_settings_repo(request: Request) -> SettingsRepository | None:
    return getattr(request.app.state, "settings_repo", None)


def _settings_with_create_overrides(
    settings: AstroCardSettings,
    card_request: AstroCardCreateRequest | None,
) -> AstroCardSettings:
    if card_request is None:
        return settings
    updates = {
        key: value
        for key, value in {
            "max_trade_usdt": card_request.max_trade_usdt,
            "leverage": card_request.leverage,
            "min_notional": card_request.min_notional,
            "max_notional": card_request.max_notional,
            "open_enabled": card_request.open_enabled,
            "card_variant": card_request.card_variant,
        }.items()
        if value is not None
    }
    if not updates:
        return settings
    return settings.model_copy(update=updates)


async def _effective_astro_card_settings(request: Request) -> AstroCardSettings:
    repo = _optional_settings_repo(request)
    if repo is None:
        return request.app.state.settings.astro_card_settings
    find_settings = getattr(repo, "find_astro_card_settings", None)
    stored = await find_settings() if find_settings is not None else await repo.get_astro_card_settings()
    if stored is None:
        return request.app.state.settings.astro_card_settings
    return stored


async def _effective_risk_settings(request: Request) -> RiskSettings:
    repo = _optional_settings_repo(request)
    if repo is None:
        return RiskSettings()
    return await repo.get_risk_settings()


async def _manual_risk_settings(request: Request) -> tuple[RiskSettings, list[str]]:
    try:
        return await _effective_risk_settings(request), []
    except Exception:  # noqa: BLE001 - manual writes may continue with a visible warning.
        return RiskSettings(), [
            _manual_override_warning(
                "读取全局风险设置失败，无法完成黑名单、忽略交易所和行情时效校验"
            )
        ]


async def _manual_astro_card_settings(
    request: Request,
) -> tuple[AstroCardSettings, list[str]]:
    try:
        return await _effective_astro_card_settings(request), []
    except Exception:  # noqa: BLE001 - manual writes may continue with a visible warning.
        return request.app.state.settings.astro_card_settings, [
            _manual_override_warning(
                "读取 Astro 建卡设置失败，已使用应用默认建卡设置"
            )
        ]


async def _build_astro_preview(
    request: Request,
    opportunity: Opportunity,
    manual_warnings: list[str] | None = None,
) -> AstroPairPlan:
    settings, settings_warnings = await _manual_astro_card_settings(request)
    planner = AstroPairPlanner(AstroPlannerConfig.from_card_settings(settings))
    plan = planner.plan(opportunity, allow_manual_override=True)
    route_variants = [
        AstroCardRouteVariant(
            card_variant="gc" if any(exchange.startswith("gc-") for exchange in (buy, sell)) else "non_gc",
            buy_exchange=buy,
            sell_exchange=sell,
        )
        for buy, sell in astro_exchange_route_variants(
            str(plan.pair.get("buyEx", "")), str(plan.pair.get("sellEx", ""))
        )
    ] if plan.pair else []
    preview_warning = (
        "系统当前处于 dry-run 模式；点击确认也不会写入 Astro。"
        if request.app.state.settings.astro_dry_run_only
        else "当前仅为预览；点击确认创建后会实际写入 Astro。"
    )
    plan = plan.model_copy(
        update={
            "card_variant": settings.card_variant,
            "route_variants": route_variants,
            "warnings": [
                preview_warning,
                *(manual_warnings or []),
                *settings_warnings,
                *(
                    warning
                    for warning in plan.warnings
                    if not warning.startswith("Dry-run only:")
                ),
            ]
        }
    )
    risk_settings, risk_warnings = await _manual_risk_settings(request)
    plan = plan.model_copy(
        update={"warnings": _unique_warnings([*plan.warnings, *risk_warnings])}
    )
    if read_only_astro_exchanges(opportunity):
        plan = plan.model_copy(
            update={
                "can_submit": False,
                "blockers": _unique_warnings(
                    [*plan.blockers, READ_ONLY_ASTRO_MESSAGE]
                ),
            }
        )
    if not symbol_is_excluded(opportunity.symbol, risk_settings):
        return plan.model_copy(update={"warnings": _unique_warnings(plan.warnings)})
    return plan.model_copy(
        update={
            "warnings": _unique_warnings(
                [
                    _manual_override_warning(
                        f"{opportunity.symbol} 已在全局黑名单"
                    ),
                    *plan.warnings,
                ]
            ),
        }
    )


def _signed_pct(value: float) -> str:
    return f"{value:+.3f}%"


def _format_depth_validation_message(result: DepthValidationResult) -> str:
    details = "；".join(result.blockers) if result.blockers else "深度校验未通过"
    metrics: list[str] = [f"验证金额 {result.target_notional_usdt:.2f} USDT"]
    if result.price_band_pct is not None:
        metrics.append(f"价格带 {result.price_band_pct:.3f}%")
    if result.required_depth_usdt is not None:
        metrics.append(f"要求深度 {result.required_depth_usdt:.2f} USDT")
    if result.min_depth_usdt is not None:
        metrics.append(f"最小价格带深度 {result.min_depth_usdt:.2f} USDT")
    if result.executable_open_pct is not None:
        metrics.append(f"实际可成交开仓价差 {_signed_pct(result.executable_open_pct)}")
    if result.cost_pct is not None:
        metrics.append(f"成本修正 {_signed_pct(-result.cost_pct)}")
    if result.funding_edge_pct is not None:
        metrics.append(f"资金费边际 {_signed_pct(result.funding_edge_pct)}")
    if result.slippage_buffer_pct is not None:
        metrics.append(f"滑点缓冲 {_signed_pct(-result.slippage_buffer_pct)}")
    if result.effective_executable_edge_pct is not None:
        metrics.append(f"实际可成交有效收益 {_signed_pct(result.effective_executable_edge_pct)}")
    return f"订单簿校验未通过：{details}（{'，'.join(metrics)}）"


async def _validate_order_book_before_create(
    request: Request,
    opportunity: Opportunity,
    risk_settings: RiskSettings,
    card_settings: AstroCardSettings,
) -> DepthValidationResult | None:
    validator = getattr(request.app.state, "orderbook_validator", None)
    if validator is None:
        return None
    result = await validator.validate(
        opportunity,
        risk_settings=risk_settings,
        card_settings=card_settings,
    )
    return None if result.passed else result


@router.get("/status", response_model=AstroSdkStatus)
async def get_astro_status(request: Request) -> AstroSdkStatus:
    client = _astro_client(request)
    return AstroSdkStatus.model_validate(client.status(request.app.state.settings.astro_dry_run_only))


def _preadd_service(request: Request) -> AstroPreaddService:
    service = getattr(request.app.state, "astro_preadd_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="预建卡片服务还没有准备好")
    return service


@router.get("/preadd/exchanges", response_model=list[str])
async def preadd_exchanges() -> list[str]:
    return list(PREADD_EXCHANGES)


@router.get("/preadd/settings", response_model=AstroPreaddSettings)
async def preadd_settings(request: Request) -> AstroPreaddSettings:
    return await _settings_repo(request).get_astro_preadd_settings()


@router.put("/preadd/settings", response_model=AstroPreaddSettings)
async def save_preadd_settings(
    settings: AstroPreaddSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> AstroPreaddSettings:
    _require_dashboard_password(request, password)
    return await _settings_repo(request).set_astro_preadd_settings(settings)


@router.get("/preadd/preview", response_model=AstroPreaddPreview)
async def preview_preadd(request: Request) -> AstroPreaddPreview:
    return await _preadd_service(request).preview()


@router.post("/preadd/run", response_model=AstroPreaddRunResult)
async def run_preadd(
    request: Request,
    payload: AstroPreaddRunRequest | None = None,
    password: str | None = Depends(dashboard_password_header),
) -> AstroPreaddRunResult:
    _require_dashboard_password(request, password)
    return await _preadd_service(request).run(
        payload.candidate_ids if payload else None,
        payload.card_variant if payload else None,
    )


@router.get("/pairs")
async def list_astro_pairs(
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> list[dict]:
    _require_dashboard_password(request, password)
    client = _astro_client(request)
    try:
        return await client.list_pairs()
    except AstroClientError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/preview/{opportunity_id}", response_model=AstroPairPlan)
async def preview_astro_pair(opportunity_id: str, request: Request) -> AstroPairPlan:
    opportunity = _find_opportunity(request, opportunity_id)
    return await _build_astro_preview(request, opportunity)


@router.post("/instrument/preview", response_model=AstroPairPlan)
async def preview_astro_instrument_pair(
    route: AstroInstrumentRouteRequest,
    request: Request,
) -> AstroPairPlan:
    opportunity, warnings = await _find_instrument_opportunity(request, route)
    return await _build_astro_preview(request, opportunity, warnings)


async def _create_astro_card(
    request: Request,
    opportunity: Opportunity,
    card_request: AstroCardCreateRequest | None,
    manual_warnings: list[str] | None = None,
) -> AstroAlertActionResult:
    if read_only_astro_exchanges(opportunity):
        raise HTTPException(
            status_code=422,
            detail=READ_ONLY_ASTRO_MESSAGE,
        )
    risk_settings, risk_warnings = await _manual_risk_settings(request)
    warnings = [*(manual_warnings or []), *risk_warnings]
    if symbol_is_excluded(opportunity.symbol, risk_settings):
        warnings.append(
            _manual_override_warning(f"{opportunity.symbol} 已在全局黑名单")
        )
    settings_repo = _optional_settings_repo(request)
    saved_settings, settings_warnings = await _manual_astro_card_settings(request)
    warnings.extend(settings_warnings)
    effective_settings = _settings_with_create_overrides(saved_settings, card_request)
    settings_for_service = saved_settings if not settings_warnings else None
    if card_request is not None and card_request.save_as_default:
        requested_default_settings = saved_settings.model_copy(
            update={
                "max_trade_usdt": effective_settings.max_trade_usdt,
                "leverage": effective_settings.leverage,
                "min_notional": effective_settings.min_notional,
                "max_notional": effective_settings.max_notional,
                "open_enabled": effective_settings.open_enabled,
                "card_variant": effective_settings.card_variant,
            }
        )
        if settings_repo is not None:
            try:
                await settings_repo.set_astro_card_settings(requested_default_settings)
                settings_for_service = requested_default_settings
            except Exception:  # noqa: BLE001 - the requested card can still be created.
                warnings.append(
                    _manual_override_warning(
                        "保存 Astro 默认建卡设置失败，本次卡片仍使用当前填写的参数"
                    )
                )
        else:
            settings_for_service = requested_default_settings
    service = getattr(request.app.state, "astro_alert_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Astro submit service is not ready")
    if hasattr(service, "card_settings") and settings_for_service is not None:
        service.card_settings = settings_for_service
    depth_failure = await _validate_order_book_before_create(
        request,
        opportunity,
        risk_settings,
        effective_settings,
    )
    result = await service.handle_manual_create(
        opportunity,
        card_request,
        card_settings=effective_settings,
    )
    if depth_failure is not None:
        warnings.append(
            _manual_override_warning(_format_depth_validation_message(depth_failure))
        )
    return result.model_copy(
        update={
            "warnings": _unique_warnings([
                *result.warnings,
                *warnings,
            ])
        }
    )


@router.post("/opportunities/{opportunity_id}/card", response_model=AstroAlertActionResult)
async def create_astro_card_from_opportunity(
    opportunity_id: str,
    request: Request,
    card_request: AstroCardCreateRequest | None = None,
    password: str | None = Depends(dashboard_password_header),
) -> AstroAlertActionResult:
    _require_dashboard_password(request, password)
    opportunity = _find_opportunity(request, opportunity_id)
    return await _create_astro_card(request, opportunity, card_request)


@router.post("/instrument/card", response_model=AstroAlertActionResult)
async def create_astro_card_from_instrument(
    payload: AstroInstrumentCardCreateRequest,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> AstroAlertActionResult:
    _require_dashboard_password(request, password)
    opportunity, warnings = await _find_instrument_opportunity(request, payload.route)
    allowed_drift_pct = max(0.02, abs(payload.expected_open_spread_pct) * 0.1)
    actual_drift_pct = abs(opportunity.open_spread_pct - payload.expected_open_spread_pct)
    if actual_drift_pct > allowed_drift_pct:
        warnings.append(
            _manual_override_warning(
                "预览后可成交价差变化过大："
                f"预览 {payload.expected_open_spread_pct:+.3f}%，"
                f"当前 {opportunity.open_spread_pct:+.3f}%"
            )
        )
    return await _create_astro_card(request, opportunity, payload.card, warnings)
