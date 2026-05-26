from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.funding_arbitrage import FundingArbitragePreview, FundingArbitrageSettings
from app.models.settings import RiskSettings
from app.services.data_filters import filter_markets
from app.services.funding_arbitrage import build_funding_arbitrage_preview

router = APIRouter(prefix="/funding-arbitrage")


def _settings_repo(request: Request):
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Settings repository is not ready")
    return repo


async def _risk_settings(request: Request) -> RiskSettings:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        return RiskSettings()
    get_settings = getattr(repo, "get_risk_settings", None)
    if get_settings is None:
        return RiskSettings()
    return await get_settings()


@router.get("/settings", response_model=FundingArbitrageSettings)
async def get_funding_arbitrage_settings(request: Request) -> FundingArbitrageSettings:
    repo = _settings_repo(request)
    return await repo.get_funding_arbitrage_settings()


@router.put("/settings", response_model=FundingArbitrageSettings)
async def update_funding_arbitrage_settings(
    settings: FundingArbitrageSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> FundingArbitrageSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    repo = _settings_repo(request)
    return await repo.set_funding_arbitrage_settings(settings)


@router.get("/preview", response_model=FundingArbitragePreview)
async def get_funding_arbitrage_preview(request: Request) -> FundingArbitragePreview:
    repo = _settings_repo(request)
    settings = await repo.get_funding_arbitrage_settings()
    risk_settings = await _risk_settings(request)
    markets = filter_markets(
        request.app.state.snapshot_store.get_markets(),
        risk_settings,
    )
    return build_funding_arbitrage_preview(markets, settings)
