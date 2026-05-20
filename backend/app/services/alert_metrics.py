from dataclasses import dataclass
from datetime import datetime

from app.models.opportunity import Opportunity


@dataclass(frozen=True)
class AlertObservation:
    observed_at: datetime
    open_spread_pct: float
    close_spread_pct: float
    fee_adjusted_open_pct: float
    funding_edge_pct: float
    combined_open_edge_pct: float
    net_funding_pct: float | None
    net_funding_next_pct: float | None


def funding_edge_pct(opportunity: Opportunity) -> float:
    if opportunity.net_funding_next_pct is not None:
        return opportunity.net_funding_next_pct
    if opportunity.net_funding_pct is not None:
        return opportunity.net_funding_pct
    return 0.0


def combined_open_edge_pct(opportunity: Opportunity) -> float:
    return opportunity.fee_adjusted_open_pct + funding_edge_pct(opportunity)


def observe_alert_metrics(opportunity: Opportunity, observed_at: datetime) -> AlertObservation:
    funding_edge = funding_edge_pct(opportunity)
    return AlertObservation(
        observed_at=observed_at,
        open_spread_pct=opportunity.open_spread_pct,
        close_spread_pct=opportunity.close_spread_pct,
        fee_adjusted_open_pct=opportunity.fee_adjusted_open_pct,
        funding_edge_pct=funding_edge,
        combined_open_edge_pct=opportunity.fee_adjusted_open_pct + funding_edge,
        net_funding_pct=opportunity.net_funding_pct,
        net_funding_next_pct=opportunity.net_funding_next_pct,
    )
