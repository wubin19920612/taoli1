from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.db.repositories import SettingsRepository
from app.models.settings import AlertMessageTemplateSettings, RiskSettings

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
