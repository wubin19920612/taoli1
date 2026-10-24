from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.settings import RiskSettings
from app.services.data_filters import (
    filter_exchange_errors,
    filter_markets,
    filter_opportunities,
    ignored_exchange_set,
    normalize_exchange,
)

from app.services.pair_spread_query import realtime_cache_stats

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


@router.get("/health/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request, response: Response) -> dict:
    state = request.app.state
    reasons = []
    if state.start_collector:
        tasks = state.task_supervisor.snapshot()
        for name in ("market-collector", "alert-loop"):
            if tasks.get(name, {}).get("state") != "running":
                reasons.append(f"{name} is not running")
        published_at = state.snapshot_store.published_at
        max_age = 3 * state.settings.poll_interval_seconds + 30
        age = (datetime.now(UTC) - published_at).total_seconds() if published_at else None
        if age is None or not 0 <= age < max_age:
            reasons.append("snapshot is missing or stale")
    response.status_code = 503 if reasons else 200
    return {"status": "not_ready" if reasons else "ready", "reasons": reasons}


@router.get("/health/detail")
async def detail(
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> dict:
    state = request.app.state
    verify_dashboard_password(state.settings.dashboard_password, password)
    collector = state.market_collector
    now = datetime.now(UTC)
    exchanges = {}
    if collector is not None:
        states = collector.exchange_states()
        for name in dict.fromkeys([adapter.name for adapter in collector.adapters] + list(states)):
            poll_state = states.get(name, {})
            last_success = poll_state.get("last_success_at")
            exchanges[name] = {
                **poll_state,
                "last_success_at": last_success,
                "data_age_seconds": (
                    max(0.0, (now - last_success).total_seconds()) if last_success else None
                ),
            }
    return {
        "tasks": state.task_supervisor.snapshot(),
        "snapshot": {
            "generation": state.snapshot_store.generation,
            "published_at": state.snapshot_store.published_at,
        },
        "collector_timings": collector.stage_timings.snapshot() if collector else None,
        "exchanges": exchanges,
        "event_loop_lag": state.task_supervisor.loop_lag.snapshot(),
        "realtime_caches": realtime_cache_stats(),
    }
