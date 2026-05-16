from datetime import UTC, datetime

from app.models.market import MarketType
from app.models.opportunity import Opportunity
from app.models.settings import RiskSettings

NON_ACTIONABLE_RISK_LABELS = frozenset(
    {
        "LOW_VOLUME",
        "STALE_DATA",
        "HUGE_SPREAD_VERIFY",
        "WIDE_SPREAD",
        "SAME_TICKER_RISK",
        "MARK_INDEX_DEVIATION",
        "MISSING_FUNDING",
    }
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

    min_volume = min(
        opportunity.buy_volume_24h_usdt or 0,
        opportunity.sell_volume_24h_usdt or 0,
    )
    if min_volume < settings.min_volume_24h_usdt:
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

    if (
        opportunity.net_funding_pct is not None
        and opportunity.net_funding_pct < -settings.funding_against_pct
    ):
        labels.append("FUNDING_AGAINST")

    mark_diffs = [
        abs(value)
        for value in [opportunity.mark_index_diff_buy_pct, opportunity.mark_index_diff_sell_pct]
        if value is not None
    ]
    if any(value >= settings.mark_index_deviation_pct for value in mark_diffs):
        labels.append("MARK_INDEX_DEVIATION")

    if (
        opportunity.buy_market_type == MarketType.FUTURE
        and opportunity.funding_rate_buy_pct is None
    ) or (
        opportunity.sell_market_type == MarketType.FUTURE
        and opportunity.funding_rate_sell_pct is None
    ):
        labels.append("MISSING_FUNDING")

    return opportunity.model_copy(update={"risk_labels": labels})
