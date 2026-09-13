from __future__ import annotations

from hashlib import sha1

from app.models.market import MarketSnapshot
from app.services.funding_research.models import (
    FundingOpportunityType,
    FundingResearchCandidate,
    FundingResearchSettings,
)


FORMULA_FAMILIES: dict[str, str] = {
    "binance": "with_rate_clamp",
    "bitget": "with_rate_clamp",
    "okx": "with_rate_variable_interval",
    "bybit": "bybit_premium_interest_clamp",
    "gate": "gate_indicative",
    "hyperliquid": "hyperliquid_hourly",
    "htx": "htx",
    "aster": "aster",
}

OPPORTUNITY_PRIORITY: tuple[FundingOpportunityType, ...] = (
    "BASIS_AND_FUNDING_ALIGNED",
    "STRONG_FUNDING_NEAR_SETTLEMENT",
    "INTERVAL_MISMATCH",
    "FORMULA_DIVERGENCE",
    "BASIS_CARRY_CONFLICTED",
    "BASIS_MEAN_REVERSION",
    "PURE_FUNDING_SPREAD",
)


def exchange_formula_family(exchange: str) -> str:
    return FORMULA_FAMILIES.get(exchange.strip().lower(), "unknown")


def research_candidate_id(candidate: FundingResearchCandidate) -> str:
    raw = (
        f"{candidate.symbol}:{candidate.long_exchange}:{candidate.short_exchange}:"
        f"{candidate.long_funding_interval_hours}:{candidate.short_funding_interval_hours}:"
        f"{candidate.next_settlement_time}"
    )
    return sha1(raw.encode("utf-8")).hexdigest()[:20]


def _append_unique(
    values: list[FundingOpportunityType],
    reasons: list[str],
    opportunity_type: FundingOpportunityType,
    reason: str,
) -> None:
    if opportunity_type in values:
        return
    values.append(opportunity_type)
    reasons.append(reason)


def classify_funding_candidate(
    candidate: FundingResearchCandidate,
    *,
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
    settings: FundingResearchSettings,
) -> tuple[FundingOpportunityType, list[FundingOpportunityType], list[str]]:
    types: list[FundingOpportunityType] = []
    reasons: list[str] = []
    expected_funding = candidate.expected_net_funding_pct or 0.0
    basis_abs = abs(candidate.basis_diff_pct or 0.0)
    minutes = candidate.minutes_to_settlement

    if expected_funding > 0 and candidate.basis_alignment == "aligned":
        _append_unique(
            types,
            reasons,
            "BASIS_AND_FUNDING_ALIGNED",
            "basis is favorable and funding carry pays the selected direction",
        )
    elif expected_funding > 0 and candidate.basis_alignment == "conflicted":
        _append_unique(
            types,
            reasons,
            "BASIS_CARRY_CONFLICTED",
            "funding carry is positive but basis is against the selected direction",
        )
    elif expected_funding > 0:
        _append_unique(
            types,
            reasons,
            "PURE_FUNDING_SPREAD",
            "funding carry is positive while basis is neutral",
        )

    if (
        expected_funding >= settings.strong_funding_pct
        and basis_abs <= settings.small_basis_threshold_pct
        and minutes is not None
        and minutes <= settings.near_settlement_minutes
    ):
        _append_unique(
            types,
            reasons,
            "STRONG_FUNDING_NEAR_SETTLEMENT",
            "strong carry, small basis, and settlement is near",
        )

    long_interval = candidate.long_funding_interval_hours
    short_interval = candidate.short_funding_interval_hours
    if (
        long_interval is not None
        and short_interval is not None
        and abs(long_interval - short_interval) >= settings.interval_mismatch_min_hours
    ):
        _append_unique(
            types,
            reasons,
            "INTERVAL_MISMATCH",
            "funding intervals differ enough to create event-timing asymmetry",
        )

    if (
        candidate.long_formula_family != candidate.short_formula_family
        and abs(expected_funding) >= settings.formula_divergence_min_funding_pct
    ):
        _append_unique(
            types,
            reasons,
            "FORMULA_DIVERGENCE",
            "exchange funding formula families differ and funding spread is meaningful",
        )

    if candidate.expected_basis_change_pct > 0:
        _append_unique(
            types,
            reasons,
            "BASIS_MEAN_REVERSION",
            "basis gap is expected to partially mean-revert",
        )

    if not types:
        _append_unique(
            types,
            reasons,
            "PURE_FUNDING_SPREAD",
            "no specialized detector matched; keep as generic funding-spread watch item",
        )

    exchange_set = {long_leg.exchange.lower(), short_leg.exchange.lower()}
    if "gate" in exchange_set and "gate" not in candidate.risk_labels:
        reasons.append("route includes Gate")
    if "hyperliquid" in exchange_set and "hyperliquid" not in candidate.risk_labels:
        reasons.append("route includes Hyperliquid")

    primary = next(item for item in OPPORTUNITY_PRIORITY if item in types)
    return primary, types, reasons
