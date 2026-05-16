from fastapi import APIRouter, Query, Request

from app.models.market import MarketType
from app.models.opportunity import Opportunity
from app.services.risk_labels import has_non_actionable_risk

router = APIRouter()


@router.get("/opportunities", response_model=list[Opportunity])
async def list_opportunities(
    request: Request,
    type: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    exchange: str | None = Query(default=None),
    min_open_spread_pct: float | None = Query(default=None),
    include_risky: bool = Query(default=False),
) -> list[Opportunity]:
    opportunities = request.app.state.snapshot_store.get_opportunities()
    if type:
        opportunities = [item for item in opportunities if item.type == type]
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
    if not include_risky:
        opportunities = [item for item in opportunities if not has_non_actionable_risk(item)]
    return opportunities


@router.get("/markets")
async def list_markets(
    request: Request,
    market_type: MarketType | None = Query(default=None),
    exchange: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
) -> list[dict]:
    markets = request.app.state.snapshot_store.get_markets()
    if market_type:
        markets = [item for item in markets if item.market_type == market_type]
    if exchange:
        wanted_exchange = exchange.lower()
        markets = [item for item in markets if item.exchange.lower() == wanted_exchange]
    if symbol:
        wanted_symbol = symbol.upper().replace("-", "").replace("_", "")
        markets = [item for item in markets if wanted_symbol in item.symbol]
    return [item.model_dump(mode="json") for item in markets]
