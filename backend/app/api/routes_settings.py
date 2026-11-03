from fastapi import APIRouter, HTTPException, Request

from app.db.repositories import SettingsRepository
from app.models.announcement import AnnouncementSettings
from app.models.settings import (
    AlertMessageTemplateSettings,
    AstroAutomationSettings,
    AstroCardSettings,
    FloatingWatchMutation,
    FloatingWatchPositionMutation,
    FloatingWatchSettings,
    RiskSettings,
)

router = APIRouter(prefix="/settings")


def _settings_repo(request: Request) -> SettingsRepository:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Settings repository is not ready")
    return repo


@router.get("/floating-watch", response_model=FloatingWatchSettings)
async def get_floating_watch_settings(request: Request) -> FloatingWatchSettings:
    return await _settings_repo(request).get_floating_watch_settings()


@router.post("/floating-watch/items", response_model=FloatingWatchSettings)
async def mutate_floating_watch_settings(
    mutation: FloatingWatchMutation,
    request: Request,
) -> FloatingWatchSettings:
    try:
        return await _settings_repo(request).mutate_floating_watch_settings(mutation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/floating-watch/positions", response_model=FloatingWatchSettings)
async def mutate_floating_watch_position_settings(
    mutation: FloatingWatchPositionMutation,
    request: Request,
) -> FloatingWatchSettings:
    try:
        return await _settings_repo(request).mutate_floating_watch_position_settings(mutation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/risk", response_model=RiskSettings)
async def get_risk_settings(request: Request) -> RiskSettings:
    return await _settings_repo(request).get_risk_settings()


@router.get("/premium-index-alert-status")
async def premium_index_alert_status(request: Request) -> dict:
    monitor = getattr(request.app.state, "multi_exchange_premium_monitor", None)
    settings = await _settings_repo(request).get_risk_settings()
    notifier = getattr(request.app.state, "feishu_notifier", None)
    running = bool(
        monitor is not None and settings.multi_exchange_premium_alert_enabled
        and request.app.state.feishu_live_send_enabled
        and getattr(getattr(notifier, "config", None), "webhook_url", "")
    )
    return {"running": running, **(monitor.status if monitor else {})}


@router.put("/risk", response_model=RiskSettings)
async def update_risk_settings(
    settings: RiskSettings,
    request: Request,
) -> RiskSettings:
    return await _settings_repo(request).set_risk_settings(settings)


@router.get("/alert-message-template", response_model=AlertMessageTemplateSettings)
async def get_alert_message_template(request: Request) -> AlertMessageTemplateSettings:
    return await _settings_repo(request).get_alert_message_template()


@router.put("/alert-message-template", response_model=AlertMessageTemplateSettings)
async def update_alert_message_template(
    settings: AlertMessageTemplateSettings,
    request: Request,
) -> AlertMessageTemplateSettings:
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
) -> AstroCardSettings:
    return await _settings_repo(request).set_astro_card_settings(settings)


@router.get("/astro-automation", response_model=AstroAutomationSettings)
async def get_astro_automation_settings(request: Request) -> AstroAutomationSettings:
    repo = _settings_repo(request)
    find_settings = getattr(repo, "find_astro_automation_settings", None)
    stored = await find_settings() if find_settings is not None else None
    if stored is None:
        return request.app.state.settings.astro_automation_settings
    return stored


@router.put("/astro-automation", response_model=AstroAutomationSettings)
async def update_astro_automation_settings(
    settings: AstroAutomationSettings,
    request: Request,
) -> AstroAutomationSettings:
    saved = await _settings_repo(request).set_astro_automation_settings(settings)
    service = getattr(request.app.state, "astro_alert_service", None)
    if service is not None and hasattr(service, "alert_auto_create_enabled"):
        service.alert_auto_create_enabled = saved.alert_auto_create
    if service is not None and hasattr(service, "allow_same_name_variants"):
        service.allow_same_name_variants = saved.allow_same_name_variants
    return saved


@router.get("/announcements", response_model=AnnouncementSettings)
async def get_announcement_settings(request: Request) -> AnnouncementSettings:
    return await _settings_repo(request).get_announcement_settings()


@router.put("/announcements", response_model=AnnouncementSettings)
async def update_announcement_settings(
    settings: AnnouncementSettings,
    request: Request,
) -> AnnouncementSettings:
    return await _settings_repo(request).set_announcement_settings(settings)
