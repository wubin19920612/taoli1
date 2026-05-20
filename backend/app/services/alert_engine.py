from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.alert import AlertRule
from app.models.opportunity import Opportunity
from app.services.risk_labels import known_volume_24h_usdt


@dataclass(frozen=True)
class AlertMatch:
    rule: AlertRule
    opportunity: Opportunity


class AlertEngine:
    def __init__(self) -> None:
        self._hits: dict[str, tuple[int, datetime]] = {}
        self._last_sent: dict[str, datetime] = {}

    def evaluate(
        self,
        opportunities: list[Opportunity],
        rules: list[AlertRule],
        now: datetime | None = None,
    ) -> list[AlertMatch]:
        current = now or datetime.now(UTC)
        matches: list[AlertMatch] = []
        active_keys: set[str] = set()
        for rule in rules:
            if not rule.enabled:
                continue
            for opportunity in opportunities:
                key = f"{rule.id}:{opportunity.id}"
                if not self._matches(rule, opportunity, current):
                    continue
                active_keys.add(key)
                previous_count, _ = self._hits.get(key, (0, current))
                count = previous_count + 1
                self._hits[key] = (count, current)
                if count < rule.consecutive_hits:
                    continue
                last_sent = self._last_sent.get(key)
                if last_sent and (current - last_sent).total_seconds() < rule.cooldown_seconds:
                    continue
                self._last_sent[key] = current
                matches.append(AlertMatch(rule=rule, opportunity=opportunity))
        for key in list(self._hits):
            if key not in active_keys:
                self._hits.pop(key, None)
        return matches

    def _matches(self, rule: AlertRule, opportunity: Opportunity, now: datetime) -> bool:
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
        if opportunity.symbol in rule.exclude_symbols:
            return False
        if opportunity.open_spread_pct < rule.min_open_spread_pct:
            return False
        if opportunity.fee_adjusted_open_pct < rule.min_fee_adjusted_open_pct:
            return False
        min_volume = known_volume_24h_usdt(opportunity)
        if min_volume is not None and min_volume < rule.min_volume_24h_usdt:
            return False
        if (now - opportunity.last_seen_at).total_seconds() > rule.max_data_age_seconds:
            return False
        if set(opportunity.risk_labels).intersection(rule.excluded_risk_labels):
            return False
        return True
