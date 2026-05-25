from app.models.market import MarketType
from app.models.opportunity import Opportunity


def _side_current_rate_pct(
    market_type: MarketType,
    current_rate_pct: float | None,
) -> float | None:
    if market_type == MarketType.SPOT:
        return 0.0
    return current_rate_pct


def _side_next_cycle_rate_pct(
    market_type: MarketType,
    next_rate_pct: float | None,
    current_rate_pct: float | None,
) -> float | None:
    if market_type == MarketType.SPOT:
        return 0.0
    if next_rate_pct is not None:
        return next_rate_pct
    return current_rate_pct


def current_cycle_funding_edge_pct(opportunity: Opportunity) -> float | None:
    if opportunity.net_funding_pct is not None:
        return opportunity.net_funding_pct

    buy_rate = _side_current_rate_pct(
        opportunity.buy_market_type,
        opportunity.funding_rate_buy_pct,
    )
    sell_rate = _side_current_rate_pct(
        opportunity.sell_market_type,
        opportunity.funding_rate_sell_pct,
    )
    if buy_rate is None or sell_rate is None:
        return None
    return sell_rate - buy_rate


def next_cycle_funding_edge_pct(opportunity: Opportunity) -> float | None:
    if opportunity.net_funding_next_pct is not None:
        return opportunity.net_funding_next_pct

    buy_rate = _side_next_cycle_rate_pct(
        opportunity.buy_market_type,
        opportunity.funding_next_rate_buy_pct,
        opportunity.funding_rate_buy_pct,
    )
    sell_rate = _side_next_cycle_rate_pct(
        opportunity.sell_market_type,
        opportunity.funding_next_rate_sell_pct,
        opportunity.funding_rate_sell_pct,
    )
    if buy_rate is not None and sell_rate is not None:
        return sell_rate - buy_rate
    return current_cycle_funding_edge_pct(opportunity)


def funding_edge_pct(opportunity: Opportunity) -> float:
    edge = next_cycle_funding_edge_pct(opportunity)
    return edge if edge is not None else 0.0
