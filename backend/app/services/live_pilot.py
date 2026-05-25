from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.alert import AlertRule
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import LivePilotSettings, RiskSettings
from app.services.alert_engine import AlertMatch, opportunity_matches_rule
from app.services.alert_metrics import combined_open_edge_pct
from app.services.funding_edge import funding_edge_pct
from app.services.risk_labels import apply_risk_labels, has_non_actionable_risk, known_volume_24h_usdt


HYPERLIQUID_EXCHANGE = "hyperliquid"


@dataclass(frozen=True)
class LivePilotSelectionStats:
    total_opportunities: int
    eligible_symbols: int
    skipped_negative_funding: int
    skipped_type: int = 0
    skipped_risk: int = 0


def _normalize_symbol(value: str) -> str:
    return value.upper().replace("-", "").replace("_", "")


def _uses_hyperliquid(opportunity: Opportunity) -> bool:
    exchanges = {opportunity.buy_exchange.lower(), opportunity.sell_exchange.lower()}
    return HYPERLIQUID_EXCHANGE in exchanges


def _is_excluded_type(opportunity: Opportunity, settings: LivePilotSettings) -> bool:
    return settings.exclude_ss and opportunity.type == OpportunityType.SS


def _route_rank(opportunity: Opportunity, settings: LivePilotSettings) -> tuple[int, float, float, float]:
    hyper_rank = 1 if settings.prefer_hyperliquid and _uses_hyperliquid(opportunity) else 0
    return (
        hyper_rank,
        combined_open_edge_pct(opportunity),
        known_volume_24h_usdt(opportunity) or 0.0,
        -opportunity.spread_width_pct,
    )


def _symbol_rank(opportunity: Opportunity) -> tuple[float, float, float]:
    return (
        combined_open_edge_pct(opportunity),
        known_volume_24h_usdt(opportunity) or 0.0,
        -opportunity.spread_width_pct,
    )


def filter_opportunities_by_alert_rules(
    opportunities: list[Opportunity],
    rules: list[AlertRule],
    risk_settings: RiskSettings,
    now: datetime | None = None,
) -> list[Opportunity]:
    active_rules = [rule for rule in rules if rule.enabled]
    if not active_rules:
        return []

    current = now or datetime.now(UTC)
    eligible: list[Opportunity] = []
    seen_ids: set[str] = set()
    for opportunity in opportunities:
        if opportunity.id in seen_ids:
            continue
        if any(opportunity_matches_rule(rule, opportunity, current, risk_settings) for rule in active_rules):
            eligible.append(opportunity)
            seen_ids.add(opportunity.id)
    return eligible


def _select_live_pilot_opportunities_with_stats(
    opportunities: list[Opportunity],
    settings: LivePilotSettings,
    risk_settings: RiskSettings | None = None,
    now: datetime | None = None,
) -> tuple[list[Opportunity], LivePilotSelectionStats]:
    total_opportunities = len(opportunities)
    if not settings.enabled:
        return opportunities, LivePilotSelectionStats(
            total_opportunities=total_opportunities,
            eligible_symbols=len({_normalize_symbol(item.symbol) for item in opportunities}),
            skipped_negative_funding=0,
        )

    by_symbol: dict[str, list[Opportunity]] = {}
    skipped_negative_funding = 0
    skipped_type = 0
    skipped_risk = 0
    for opportunity in opportunities:
        if _is_excluded_type(opportunity, settings):
            skipped_type += 1
            continue
        if has_non_actionable_risk(opportunity):
            skipped_risk += 1
            continue
        risk_checked = (
            apply_risk_labels(opportunity, risk_settings, now=now)
            if risk_settings is not None
            else opportunity
        )
        if has_non_actionable_risk(risk_checked):
            skipped_risk += 1
            continue
        if funding_edge_pct(opportunity) < settings.min_next_funding_edge_pct:
            skipped_negative_funding += 1
            continue
        by_symbol.setdefault(_normalize_symbol(risk_checked.symbol), []).append(risk_checked)

    selected = [
        sorted(symbol_opportunities, key=lambda item: _route_rank(item, settings), reverse=True)[0]
        for symbol_opportunities in by_symbol.values()
    ]
    selected = sorted(
        selected,
        key=_symbol_rank,
        reverse=True,
    )[: settings.max_symbols]
    return selected, LivePilotSelectionStats(
        total_opportunities=total_opportunities,
        eligible_symbols=len(by_symbol),
        skipped_negative_funding=skipped_negative_funding,
        skipped_type=skipped_type,
        skipped_risk=skipped_risk,
    )


def select_live_pilot_opportunities(
    opportunities: list[Opportunity],
    settings: LivePilotSettings,
    risk_settings: RiskSettings | None = None,
    now: datetime | None = None,
) -> list[Opportunity]:
    selected, _ = _select_live_pilot_opportunities_with_stats(
        opportunities,
        settings,
        risk_settings,
        now,
    )
    return selected


def preview_live_pilot_opportunities(
    opportunities: list[Opportunity],
    settings: LivePilotSettings,
    risk_settings: RiskSettings | None = None,
    now: datetime | None = None,
) -> tuple[list[Opportunity], LivePilotSelectionStats]:
    return _select_live_pilot_opportunities_with_stats(
        opportunities,
        settings,
        risk_settings,
        now,
    )


def select_live_pilot_matches(
    matches: list[AlertMatch],
    settings: LivePilotSettings,
    risk_settings: RiskSettings | None = None,
    now: datetime | None = None,
) -> list[AlertMatch]:
    if not settings.enabled:
        return matches

    by_symbol: dict[str, list[AlertMatch]] = {}
    for match in matches:
        if _is_excluded_type(match.opportunity, settings):
            continue
        if has_non_actionable_risk(match.opportunity):
            continue
        risk_checked = (
            apply_risk_labels(match.opportunity, risk_settings, now=now)
            if risk_settings is not None
            else match.opportunity
        )
        if has_non_actionable_risk(risk_checked):
            continue
        if funding_edge_pct(risk_checked) < settings.min_next_funding_edge_pct:
            continue
        by_symbol.setdefault(_normalize_symbol(risk_checked.symbol), []).append(
            AlertMatch(match.rule, risk_checked, match.observations)
        )

    selected = [
        sorted(
            symbol_matches,
            key=lambda item: _route_rank(item.opportunity, settings),
            reverse=True,
        )[0]
        for symbol_matches in by_symbol.values()
    ]
    return sorted(
        selected,
        key=lambda item: _symbol_rank(item.opportunity),
        reverse=True,
    )[: settings.max_symbols]
