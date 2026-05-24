from fastapi import APIRouter, Request

from app.models.settings import RiskSettings
from app.services.data_filters import (
    filter_exchange_errors,
    filter_markets,
    filter_opportunities,
    ignored_exchange_set,
    normalize_exchange,
)

router = APIRouter()


async def _risk_settings(request: Request) -> RiskSettings:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        return RiskSettings()
    return await repo.get_risk_settings()


def _exchange_states(request: Request, settings: RiskSettings) -> dict[str, dict[str, object]]:
    collector = getattr(request.app.state, "market_collector", None)
    if collector is None:
        return {}
    states = collector.exchange_states()
    ignored_exchanges = ignored_exchange_set(settings)
    return {
        exchange: state
        for exchange, state in states.items()
        if normalize_exchange(exchange) not in ignored_exchanges
    }


@router.get("/health")
async def health(request: Request) -> dict:
    store = request.app.state.snapshot_store
    settings = await _risk_settings(request)
    return {
        "status": "ok",
        "markets": len(filter_markets(store.get_markets(), settings)),
        "opportunities": len(filter_opportunities(store.get_opportunities(), settings)),
        "exchange_errors": filter_exchange_errors(store.get_exchange_errors(), settings),
        "exchange_states": _exchange_states(request, settings),
    }
