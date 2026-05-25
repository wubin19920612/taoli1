from datetime import UTC, datetime

from app.models.market import MarketType
from app.models.opportunity import Opportunity
from app.models.settings import RiskSettings
from app.services.alert_metrics import combined_open_edge_pct
from app.services.funding_edge import next_cycle_funding_edge_pct

NON_ACTIONABLE_RISK_LABELS = frozenset(
    {
        "LOW_VOLUME",
        "STALE_DATA",
        "HUGE_SPREAD_VERIFY",
        "WIDE_SPREAD",
        "SAME_TICKER_RISK",
        "MISSING_FUNDING",
        "THIN_ORDER_BOOK",
        "EDGE_AFTER_SLIPPAGE_TOO_SMALL",
        "TRANSIENT_SIGNAL",
    }
)


def known_volume_24h_usdt(opportunity: Opportunity) -> float | None:
    known_volumes = [
        volume
        for volume in [opportunity.buy_volume_24h_usdt, opportunity.sell_volume_24h_usdt]
        if volume is not None
    ]
    if not known_volumes:
        return None
    return min(known_volumes)


def normalized_funding_hourly_pct(opportunity: Opportunity) -> float | None:
    buy = opportunity.buy_funding_interval_hours
    sell = opportunity.sell_funding_interval_hours
    if (
        opportunity.funding_rate_buy_pct is None
        or opportunity.funding_rate_sell_pct is None
        or buy is None
        or sell is None
        or buy <= 0
        or sell <= 0
    ):
        return None
    return (opportunity.funding_rate_sell_pct / sell) - (opportunity.funding_rate_buy_pct / buy)


def effective_open_edge_pct(opportunity: Opportunity, settings: RiskSettings) -> float:
    return combined_open_edge_pct(opportunity) - settings.signal_slippage_buffer_pct


def known_open_depth_usdt(opportunity: Opportunity) -> float | None:
    if opportunity.min_open_depth_usdt is not None:
        return opportunity.min_open_depth_usdt
    known_depths = [
        depth
        for depth in [opportunity.buy_ask_depth_usdt, opportunity.sell_bid_depth_usdt]
        if depth is not None
    ]
    if not known_depths:
        return None
    return min(known_depths)


def required_open_depth_usdt(settings: RiskSettings) -> float:
    return max(
        settings.min_top_of_book_depth_usdt,
        settings.signal_validation_notional_usdt * settings.orderbook_depth_safety_multiple,
    )


def has_non_actionable_risk(
    opportunity: Opportunity,
    hidden_labels: set[str] | frozenset[str] | None = None,
) -> bool:
    labels = hidden_labels if hidden_labels is not None else NON_ACTIONABLE_RISK_LABELS
    return bool(labels.intersection(opportunity.risk_labels))


def apply_risk_labels(
    opportunity: Opportunity,
    settings: RiskSettings,
    now: datetime | None = None,
) -> Opportunity:
    current = now or datetime.now(UTC)
    labels: list[str] = []

    min_volume = known_volume_24h_usdt(opportunity)
    if min_volume is not None and min_volume < settings.min_volume_24h_usdt:
        labels.append("LOW_VOLUME")

    age_seconds = (current - opportunity.last_seen_at).total_seconds()
    if age_seconds > settings.stale_after_seconds:
        labels.append("STALE_DATA")

    if opportunity.open_spread_pct >= settings.huge_spread_pct:
        labels.append("HUGE_SPREAD_VERIFY")

    if opportunity.spread_width_pct >= settings.wide_spread_pct:
        labels.append("WIDE_SPREAD")

    if opportunity.symbol.upper() in {item.upper() for item in settings.ticker_collision_symbols}:
        labels.append("SAME_TICKER_RISK")

    next_cycle_funding = next_cycle_funding_edge_pct(opportunity)
    if next_cycle_funding is not None and next_cycle_funding < -settings.funding_against_pct:
        labels.append("FUNDING_AGAINST")

    mark_diffs = [
        abs(value)
        for value in [opportunity.mark_index_diff_buy_pct, opportunity.mark_index_diff_sell_pct]
        if value is not None
    ]
    if any(value >= settings.mark_index_deviation_pct for value in mark_diffs):
        labels.append("MARK_INDEX_DEVIATION")

    if effective_open_edge_pct(opportunity, settings) < settings.min_effective_open_pct:
        labels.append("EDGE_AFTER_SLIPPAGE_TOO_SMALL")

    required_depth = required_open_depth_usdt(settings)
    open_depth = known_open_depth_usdt(opportunity)
    if required_depth > 0 and open_depth is not None and open_depth < required_depth:
        labels.append("THIN_ORDER_BOOK")

    if (
        opportunity.buy_market_type == MarketType.FUTURE
        and opportunity.funding_rate_buy_pct is None
    ) or (
        opportunity.sell_market_type == MarketType.FUTURE
        and opportunity.funding_rate_sell_pct is None
    ):
        labels.append("MISSING_FUNDING")

    return opportunity.model_copy(update={"risk_labels": labels})
