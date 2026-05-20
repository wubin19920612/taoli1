from fastapi import APIRouter, Request

from app.models.settings import RiskSettings
from app.services.data_filters import (
    filter_exchange_errors,
    filter_markets,
    filter_opportunities,
)

router = APIRouter()


async def _risk_settings(request: Request) -> RiskSettings:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        return RiskSettings()
    return await repo.get_risk_settings()


@router.get("/health")
async def health(request: Request) -> dict:
    store = request.app.state.snapshot_store
    settings = await _risk_settings(request)
    return {
        "status": "ok",
        "markets": len(filter_markets(store.get_markets(), settings)),
        "opportunities": len(filter_opportunities(store.get_opportunities(), settings)),
        "exchange_errors": filter_exchange_errors(store.get_exchange_errors(), settings),
    }
