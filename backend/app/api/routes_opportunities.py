from fastapi import APIRouter, Query, Request

from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import DEFAULT_HIDDEN_RISK_LABELS, RiskSettings
from app.services.data_filters import filter_markets, filter_opportunities
from app.services.risk_labels import has_non_actionable_risk, known_volume_24h_usdt

router = APIRouter()


def _parse_csv(value: str | None, default: list[str]) -> list[str]:
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


async def _risk_settings(request: Request) -> RiskSettings:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        return RiskSettings()
    return await repo.get_risk_settings()


@router.get("/opportunities", response_model=list[Opportunity])
async def list_opportunities(
    request: Request,
    type: str | None = Query(default=None),
    exclude_types: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    exchange: str | None = Query(default=None),
    min_open_spread_pct: float | None = Query(default=None),
    include_risky: bool = Query(default=False),
    hidden_risk_labels: str | None = Query(default=None),
    min_volume_24h_k: float | None = Query(default=None, ge=0),
) -> list[Opportunity]:
    settings = await _risk_settings(request)
    opportunities = filter_opportunities(
        request.app.state.snapshot_store.get_opportunities(),
        settings,
    )
    if type:
        opportunities = [item for item in opportunities if item.type == type]
    allowed_types = {item.value for item in OpportunityType}
    excluded_types = {
        item.upper()
        for item in _parse_csv(exclude_types, [])
        if item.upper() in allowed_types
    }
    if excluded_types:
        opportunities = [
            item for item in opportunities if item.type.value not in excluded_types
        ]
    if symbol:
        wanted = symbol.upper().replace("-", "").replace("_", "")
        opportunities = [item for item in opportunities if wanted in item.symbol]
    if exchange:
        wanted_exchange = exchange.lower()
        opportunities = [
            item
            for item in opportunities
            if item.buy_exchange.lower() == wanted_exchange
            or item.sell_exchange.lower() == wanted_exchange
        ]
    if min_open_spread_pct is not None:
        opportunities = [
            item for item in opportunities if item.open_spread_pct >= min_open_spread_pct
        ]
    if min_volume_24h_k is not None and min_volume_24h_k > 0:
        min_volume = min_volume_24h_k * 1000

        def has_enough_known_volume(item: Opportunity) -> bool:
            known_volume = known_volume_24h_usdt(item)
            return known_volume is None or known_volume >= min_volume

        opportunities = [
            item
            for item in opportunities
            if has_enough_known_volume(item)
        ]
    if not include_risky:
        hidden_labels = set(_parse_csv(hidden_risk_labels, DEFAULT_HIDDEN_RISK_LABELS))
        opportunities = [
            item for item in opportunities if not has_non_actionable_risk(item, hidden_labels)
        ]
    return opportunities


@router.get("/markets")
async def list_markets(
    request: Request,
    market_type: MarketType | None = Query(default=None),
    exchange: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
) -> list[dict]:
    settings = await _risk_settings(request)
    markets = filter_markets(request.app.state.snapshot_store.get_markets(), settings)
    if market_type:
        markets = [item for item in markets if item.market_type == market_type]
    if exchange:
        wanted_exchange = exchange.lower()
        markets = [item for item in markets if item.exchange.lower() == wanted_exchange]
    if symbol:
        wanted_symbol = symbol.upper().replace("-", "").replace("_", "")
        markets = [item for item in markets if wanted_symbol in item.symbol]
    return [item.model_dump(mode="json") for item in markets]
