from datetime import datetime

from app.models.market import MarketSnapshot, MarketType
from app.services.funding_research.models import FundingFormulaEstimate


def clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def estimate_okx_with_rate_funding_pct(
    *,
    premium_pct: float,
    interval_hours: float,
    interest_pct: float = 0.01,
    inner_clamp_pct: float = 0.05,
    cap_pct: float | None = None,
    floor_pct: float | None = None,
) -> float:
    """Estimate OKX withRate funding under the 8 / N period adjustment."""
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")
    period_adjustment = 8 / interval_hours
    adjusted = (
        premium_pct
        + clamp(interest_pct - premium_pct, -inner_clamp_pct, inner_clamp_pct)
    ) / period_adjustment
    if cap_pct is not None and floor_pct is not None:
        adjusted = clamp(adjusted, floor_pct, cap_pct)
    return adjusted


def estimate_snapshot_funding(
    snapshot: MarketSnapshot,
    *,
    formula_funding_pct: float | None = None,
    formula_version: str | None = None,
) -> FundingFormulaEstimate:
    if snapshot.market_type == MarketType.SPOT:
        return FundingFormulaEstimate(
            funding_rate_pct=0.0,
            source="predicted",
            interval_hours=None,
            next_time=None,
            reason="spot leg has no funding",
        )
    if formula_funding_pct is not None:
        return FundingFormulaEstimate(
            funding_rate_pct=formula_funding_pct,
            source="formula",
            formula_version=formula_version,
            interval_hours=snapshot.funding_interval_hours,
            next_time=snapshot.funding_next_time,
        )
    if snapshot.funding_next_rate_pct is not None:
        return FundingFormulaEstimate(
            funding_rate_pct=snapshot.funding_next_rate_pct,
            source="predicted",
            interval_hours=snapshot.funding_interval_hours,
            next_time=snapshot.funding_next_time,
        )
    if snapshot.funding_rate_pct is not None:
        return FundingFormulaEstimate(
            funding_rate_pct=snapshot.funding_rate_pct,
            source="fallback_current",
            interval_hours=snapshot.funding_interval_hours,
            next_time=snapshot.funding_next_time,
            reason="next funding missing, using current funding",
        )
    return FundingFormulaEstimate(
        funding_rate_pct=None,
        source="missing",
        interval_hours=snapshot.funding_interval_hours,
        next_time=snapshot.funding_next_time,
        reason="funding data missing",
    )


def minutes_until(target: datetime | None, now: datetime) -> float | None:
    if target is None:
        return None
    if target.tzinfo is None and now.tzinfo is not None:
        target = target.replace(tzinfo=now.tzinfo)
    if now.tzinfo is None and target.tzinfo is not None:
        now = now.replace(tzinfo=target.tzinfo)
    return (target - now).total_seconds() / 60
