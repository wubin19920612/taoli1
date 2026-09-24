from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.models.alert import AlertRule
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import RiskSettings
from app.services.alert_metrics import (
    AlertObservation,
    combined_open_edge_pct,
    observe_alert_metrics,
)
from app.services.funding_edge import funding_edge_pct
from app.services.risk_labels import effective_open_edge_pct, known_volume_24h_usdt

MAX_ALERTS_PER_SYMBOL = 3
MAX_ALERTS_PER_EVALUATION = 10
DELIVERY_RETRY_SECONDS = 60
EPSILON = 1e-9


@dataclass(frozen=True)
class AlertMatch:
    rule: AlertRule
    opportunity: Opportunity
    observations: list[AlertObservation]


class AlertEngine:
    def __init__(self) -> None:
        self._hits: dict[str, tuple[int, datetime]] = {}
        self._last_sent: dict[str, datetime] = {}
        self._retry_after: dict[str, datetime] = {}
        self._observations: dict[str, list[AlertObservation]] = {}

    def evaluate(
        self,
        opportunities: list[Opportunity],
        rules: list[AlertRule],
        now: datetime | None = None,
        risk_settings: RiskSettings | None = None,
    ) -> list[AlertMatch]:
        current = now or datetime.now(UTC)
        settings = risk_settings or RiskSettings()
        matches: list[AlertMatch] = []
        active_keys: set[str] = set()
        for rule in rules:
            if not rule.enabled:
                continue
            for opportunity in opportunities:
                key = f"{rule.id}:{opportunity.id}"
                if suppresses_sf_negative_funding(rule, opportunity):
                    continue
                if not self._matches(rule, opportunity, current, settings):
                    continue
                active_keys.add(key)
                observations = self._observations.setdefault(key, [])
                observations.append(observe_alert_metrics(opportunity, current))
                keep_count = max(rule.consecutive_hits, 1)
                if len(observations) > keep_count:
                    del observations[: len(observations) - keep_count]
                previous_count, _ = self._hits.get(key, (0, current))
                count = previous_count + 1
                self._hits[key] = (count, current)
                if count < rule.consecutive_hits:
                    continue
                if not observations_are_stable(observations, settings):
                    continue
                last_sent = self._last_sent.get(key)
                if last_sent and (current - last_sent).total_seconds() < rule.cooldown_seconds:
                    continue
                retry_after = self._retry_after.get(key)
                if retry_after is not None and current < retry_after:
                    continue
                self._retry_after.pop(key, None)
                matches.append(
                    AlertMatch(
                        rule=rule,
                        opportunity=opportunity,
                        observations=list(observations),
                    )
                )
        for key in list(self._hits):
            if key not in active_keys:
                self._hits.pop(key, None)
                self._observations.pop(key, None)
        return _limit_matches_per_symbol(matches)

    def record_delivery_result(
        self,
        match: AlertMatch,
        status: str,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(UTC)
        key = f"{match.rule.id}:{match.opportunity.id}"
        if status == "sent":
            self._last_sent[key] = current
            self._retry_after.pop(key, None)
        elif status in {"failed", "muted"}:
            self._retry_after[key] = current + timedelta(
                seconds=min(DELIVERY_RETRY_SECONDS, match.rule.cooldown_seconds)
            )
        else:
            raise ValueError(f"Unsupported alert delivery status: {status}")

    def _matches(
        self,
        rule: AlertRule,
        opportunity: Opportunity,
        now: datetime,
        settings: RiskSettings,
    ) -> bool:
        return opportunity_matches_rule(rule, opportunity, now, settings)


def suppresses_sf_negative_funding(rule: AlertRule, opportunity: Opportunity) -> bool:
    return (
        rule.suppress_sf_negative_funding
        and opportunity.type == OpportunityType.SF
        and funding_edge_pct(opportunity) < 0
    )


def required_open_spread_pct(rule: AlertRule, opportunity: Opportunity) -> float:
    relaxed = rule.favorable_funding_open_spread_pct
    if relaxed is None or opportunity.open_spread_pct <= 0:
        return rule.min_open_spread_pct

    sell_rate = opportunity.funding_next_rate_sell_pct
    if sell_rate is None:
        sell_rate = opportunity.funding_rate_sell_pct
    if sell_rate is None or opportunity.sell_market_type != MarketType.FUTURE:
        return rule.min_open_spread_pct

    if opportunity.type in (OpportunityType.SF, OpportunityType.FF) and sell_rate > 0:
        return min(rule.min_open_spread_pct, relaxed)
    if opportunity.type == OpportunityType.FF and opportunity.buy_market_type == MarketType.FUTURE:
        buy_rate = opportunity.funding_next_rate_buy_pct
        if buy_rate is None:
            buy_rate = opportunity.funding_rate_buy_pct
        if buy_rate is None:
            return rule.min_open_spread_pct
        buy_interval = opportunity.buy_funding_interval_hours
        sell_interval = opportunity.sell_funding_interval_hours
        if (buy_interval is None) != (sell_interval is None):
            return rule.min_open_spread_pct
        if buy_interval is not None and sell_interval is not None:
            if buy_interval <= 0 or sell_interval <= 0:
                return rule.min_open_spread_pct
            buy_rate /= buy_interval
            sell_rate /= sell_interval
        if buy_rate < sell_rate:
            return min(rule.min_open_spread_pct, relaxed)
    return rule.min_open_spread_pct


def opportunity_matches_rule(
    rule: AlertRule,
    opportunity: Opportunity,
    now: datetime,
    settings: RiskSettings,
) -> bool:
    if opportunity.type not in rule.types:
        return False
    if rule.include_exchanges:
        exchanges = {opportunity.buy_exchange, opportunity.sell_exchange}
        if not exchanges.intersection(set(rule.include_exchanges)):
            return False
    if opportunity.buy_exchange in rule.exclude_exchanges or opportunity.sell_exchange in rule.exclude_exchanges:
        return False
    if rule.include_symbols and opportunity.symbol not in rule.include_symbols:
        return False
    if opportunity.open_spread_pct + EPSILON < required_open_spread_pct(rule, opportunity):
        return False
    if effective_open_edge_pct(opportunity, settings) + EPSILON < rule.min_fee_adjusted_open_pct:
        return False
    if effective_open_edge_pct(opportunity, settings) + EPSILON < settings.min_effective_open_pct:
        return False
    min_volume = known_volume_24h_usdt(opportunity)
    if min_volume is not None and min_volume < rule.min_volume_24h_usdt:
        return False
    if (now - opportunity.last_seen_at).total_seconds() > rule.max_data_age_seconds:
        return False
    return not set(opportunity.risk_labels).intersection(rule.excluded_risk_labels)


def observations_are_stable(observations: list[AlertObservation], settings: RiskSettings) -> bool:
    if len(observations) < 2:
        return True
    if settings.max_open_spread_decay_pct >= 100:
        return True
    peak = max(item.open_spread_pct for item in observations)
    if peak <= 0:
        return True
    latest = observations[-1].open_spread_pct
    minimum_retained = peak * (1 - settings.max_open_spread_decay_pct / 100)
    return latest + EPSILON >= minimum_retained


def _alert_rank(match: AlertMatch) -> tuple[float, float, float, float]:
    opportunity = match.opportunity
    min_volume = known_volume_24h_usdt(opportunity) or 0.0
    volume_millions = min_volume / 1_000_000
    return (
        combined_open_edge_pct(opportunity),
        opportunity.open_spread_pct,
        volume_millions,
        opportunity.spread_width_pct * -1,
    )


def _limit_matches_per_symbol(matches: list[AlertMatch]) -> list[AlertMatch]:
    grouped: dict[str, list[AlertMatch]] = {}
    for match in matches:
        grouped.setdefault(match.opportunity.symbol.upper(), []).append(match)

    limited: list[AlertMatch] = []
    for symbol_matches in grouped.values():
        limited.extend(
            sorted(symbol_matches, key=_alert_rank, reverse=True)[:MAX_ALERTS_PER_SYMBOL]
        )
    return sorted(limited, key=_alert_rank, reverse=True)[:MAX_ALERTS_PER_EVALUATION]
