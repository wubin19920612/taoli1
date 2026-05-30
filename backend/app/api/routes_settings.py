from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.db.repositories import SettingsRepository
from app.models.announcement import AnnouncementSettings
from app.models.settings import (
    AlertMessageTemplateSettings,
    AstroCardSettings,
    LivePilotPreview,
    LivePilotPreviewItem,
    LivePilotSettings,
    RiskSettings,
)
from app.services.alert_metrics import combined_open_edge_pct
from app.services.data_filters import filter_opportunities
from app.services.funding_edge import funding_edge_pct
from app.services.live_pilot import (
    HYPERLIQUID_EXCHANGE,
    filter_opportunities_by_alert_rules,
    preview_live_pilot_opportunities,
)
from app.services.risk_labels import known_volume_24h_usdt

router = APIRouter(prefix="/settings")


def _settings_repo(request: Request) -> SettingsRepository:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Settings repository is not ready")
    return repo


@router.get("/risk", response_model=RiskSettings)
async def get_risk_settings(request: Request) -> RiskSettings:
    return await _settings_repo(request).get_risk_settings()


@router.put("/risk", response_model=RiskSettings)
async def update_risk_settings(
    settings: RiskSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> RiskSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _settings_repo(request).set_risk_settings(settings)


@router.get("/alert-message-template", response_model=AlertMessageTemplateSettings)
async def get_alert_message_template(request: Request) -> AlertMessageTemplateSettings:
    return await _settings_repo(request).get_alert_message_template()


@router.put("/alert-message-template", response_model=AlertMessageTemplateSettings)
async def update_alert_message_template(
    settings: AlertMessageTemplateSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> AlertMessageTemplateSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _settings_repo(request).set_alert_message_template(settings)


@router.get("/astro-card", response_model=AstroCardSettings)
async def get_astro_card_settings(request: Request) -> AstroCardSettings:
    repo = _settings_repo(request)
    find_settings = getattr(repo, "find_astro_card_settings", None)
    stored = await find_settings() if find_settings is not None else await repo.get_astro_card_settings()
    if stored is None:
        return request.app.state.settings.astro_card_settings
    return stored


@router.put("/astro-card", response_model=AstroCardSettings)
async def update_astro_card_settings(
    settings: AstroCardSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> AstroCardSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _settings_repo(request).set_astro_card_settings(settings)


@router.get("/live-pilot", response_model=LivePilotSettings)
async def get_live_pilot_settings(request: Request) -> LivePilotSettings:
    return await _settings_repo(request).get_live_pilot_settings()


@router.get("/live-pilot/preview", response_model=LivePilotPreview)
async def get_live_pilot_preview(request: Request) -> LivePilotPreview:
    repo = _settings_repo(request)
    risk_settings = await repo.get_risk_settings()
    live_pilot_settings = await repo.get_live_pilot_settings()
    opportunities = filter_opportunities(
        request.app.state.snapshot_store.get_opportunities(),
        risk_settings,
    )
    rule_repo = getattr(request.app.state, "alert_rule_repo", None)
    rules = await rule_repo.list() if rule_repo is not None else []
    opportunities = filter_opportunities_by_alert_rules(
        opportunities,
        rules,
        risk_settings,
    )
    selected, stats = preview_live_pilot_opportunities(
        opportunities,
        live_pilot_settings,
        risk_settings,
    )
    notional = live_pilot_settings.notional_per_symbol_usdt
    return LivePilotPreview(
        settings=live_pilot_settings,
        total_opportunities=stats.total_opportunities,
        eligible_symbols=stats.eligible_symbols,
        selected_symbols=len(selected),
        skipped_negative_funding=stats.skipped_negative_funding,
        skipped_type=stats.skipped_type,
        skipped_risk=stats.skipped_risk,
        budget_usdt=len(selected) * notional,
        items=[
            LivePilotPreviewItem(
                opportunity_id=opportunity.id,
                symbol=opportunity.symbol,
                type=opportunity.type.value,
                route=(
                    f"{opportunity.buy_exchange} {opportunity.buy_market_type.value}"
                    f" -> {opportunity.sell_exchange} {opportunity.sell_market_type.value}"
                ),
                buy_exchange=opportunity.buy_exchange,
                sell_exchange=opportunity.sell_exchange,
                uses_hyperliquid=HYPERLIQUID_EXCHANGE
                in {opportunity.buy_exchange.lower(), opportunity.sell_exchange.lower()},
                open_spread_pct=opportunity.open_spread_pct,
                fee_adjusted_open_pct=opportunity.fee_adjusted_open_pct,
                next_funding_edge_pct=funding_edge_pct(opportunity),
                combined_open_edge_pct=combined_open_edge_pct(opportunity),
                volume_24h_usdt=known_volume_24h_usdt(opportunity),
                notional_usdt=notional,
                risk_labels=opportunity.risk_labels,
            )
            for opportunity in selected
        ],
    )


@router.put("/live-pilot", response_model=LivePilotSettings)
async def update_live_pilot_settings(
    settings: LivePilotSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> LivePilotSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _settings_repo(request).set_live_pilot_settings(settings)


@router.get("/announcements", response_model=AnnouncementSettings)
async def get_announcement_settings(request: Request) -> AnnouncementSettings:
    return await _settings_repo(request).get_announcement_settings()


@router.put("/announcements", response_model=AnnouncementSettings)
async def update_announcement_settings(
    settings: AnnouncementSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> AnnouncementSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _settings_repo(request).set_announcement_settings(settings)
