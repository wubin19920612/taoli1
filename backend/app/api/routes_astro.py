from fastapi import APIRouter, Depends, HTTPException, Request

from app.db.repositories import SettingsRepository
from app.models.astro import AstroAlertActionResult, AstroCardCreateRequest, AstroPairPlan
from app.models.astro import AstroSdkStatus
from app.models.opportunity import Opportunity
from app.models.orderbook import DepthValidationResult
from app.models.settings import AstroCardSettings, RiskSettings
from app.core.security import dashboard_password_header, verify_dashboard_password
from app.services.astro_client import AstroClientError, AstroSdkClient
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig

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

    settings = await _effective_astro_card_settings(request)
    planner = AstroPairPlanner(
        AstroPlannerConfig.from_card_settings(settings)
    )
    return planner.plan(opportunity)


@router.post("/opportunities/{opportunity_id}/card", response_model=AstroAlertActionResult)
async def create_astro_card_from_opportunity(
    opportunity_id: str,
    request: Request,
    card_request: AstroCardCreateRequest | None = None,
    password: str | None = Depends(dashboard_password_header),
) -> AstroAlertActionResult:
    _require_dashboard_password(request, password)
    opportunity = _find_opportunity(request, opportunity_id)
    settings_repo = _optional_settings_repo(request)
    saved_settings = await _effective_astro_card_settings(request)
    effective_settings = _settings_with_create_overrides(saved_settings, card_request)
    if card_request is not None and card_request.save_as_default:
        saved_settings = saved_settings.model_copy(
            update={
                "max_trade_usdt": effective_settings.max_trade_usdt,
                "leverage": effective_settings.leverage,
                "min_notional": effective_settings.min_notional,
                "max_notional": effective_settings.max_notional,
                "open_enabled": effective_settings.open_enabled,
            }
        )
        if settings_repo is not None:
            await settings_repo.set_astro_card_settings(saved_settings)
    service = getattr(request.app.state, "astro_alert_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Astro submit service is not ready")
    if hasattr(service, "card_settings"):
        service.card_settings = saved_settings
    risk_settings = await _effective_risk_settings(request)
    depth_failure = await _validate_order_book_before_create(
        request,
        opportunity,
        risk_settings,
        effective_settings,
    )
    if depth_failure is not None:
        return AstroAlertActionResult(
            enabled=True,
            status="skipped",
            action="order_book_validation",
            message=_format_depth_validation_message(depth_failure),
            pair_name=opportunity.symbol.removesuffix("USDT"),
            pair_type=str(opportunity.type),
        )
    return await service.handle_manual_create(opportunity, card_request)
