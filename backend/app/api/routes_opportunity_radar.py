from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.opportunity_radar import OpportunityRadarPreview, OpportunityRadarSettings
from app.models.settings import RiskSettings
from app.services.data_filters import filter_markets
from app.services.opportunity_radar import build_opportunity_radar_preview

router = APIRouter(prefix="/opportunity-radar")


def _settings_repo(request: Request):
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Settings repository is not ready")
    return repo


@router.get("/settings", response_model=OpportunityRadarSettings)
async def get_opportunity_radar_settings(request: Request) -> OpportunityRadarSettings:
    return await _settings_repo(request).get_opportunity_radar_settings()


@router.put("/settings", response_model=OpportunityRadarSettings)
async def update_opportunity_radar_settings(
    settings: OpportunityRadarSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> OpportunityRadarSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _settings_repo(request).set_opportunity_radar_settings(settings)


@router.get("/preview", response_model=OpportunityRadarPreview)
async def get_opportunity_radar_preview(request: Request) -> OpportunityRadarPreview:
    repo = _settings_repo(request)
    settings = await repo.get_opportunity_radar_settings()
    get_risk_settings = getattr(repo, "get_risk_settings", None)
    risk_settings = await get_risk_settings() if get_risk_settings is not None else RiskSettings()
    markets = filter_markets(request.app.state.snapshot_store.get_markets(), risk_settings)
    return build_opportunity_radar_preview(markets, settings)
