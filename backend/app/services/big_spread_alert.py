"""Independent fast alert eligibility; AlertRule is only the delivery envelope."""

import logging
from datetime import UTC, datetime, timedelta
from math import isfinite

from app.models.alert import AlertRule
from app.models.opportunity import Opportunity
from app.models.settings import RiskSettings, normalize_mute_symbol
from app.services.alert_engine import AlertEngine, AlertMatch, EPSILON
from app.services.alert_metrics import observe_alert_metrics
from app.services.data_filters import ignored_exchange_set, symbol_is_excluded
from app.services.risk_labels import known_volume_24h_usdt

BIG_SPREAD_RULE_ID = "big_spread"
MAX_BIG_SPREAD_KEYS = 5000
logger = logging.getLogger(__name__)


def delivery_rule(rules: list[AlertRule], settings: RiskSettings) -> AlertRule:
    active = [rule for rule in rules if rule.enabled]
    return AlertRule(
        id=BIG_SPREAD_RULE_ID, name="大价差提醒",
        types=sorted({kind for rule in active for kind in rule.types}) if active else ["FF"],
        exclude_exchanges=sorted({ex.lower() for rule in active for ex in rule.exclude_exchanges}),
        min_volume_24h_usdt=min((r.min_volume_24h_usdt for r in active), default=100_000),
        # With multiple rules, use the strictest freshness boundary.
        max_data_age_seconds=min((r.max_data_age_seconds for r in active), default=600),
        min_open_spread_pct=settings.big_spread_alert_pct,
        favorable_funding_open_spread_pct=None,
        consecutive_hits=settings.big_spread_alert_hits,
        cooldown_seconds=settings.big_spread_alert_cooldown_seconds,
        excluded_risk_labels=[], suppress_sf_negative_funding=False,
        severity="warning",
    )


def big_spread_is_muted(opportunity: Opportunity, settings: RiskSettings, now: datetime) -> bool:
    symbol = normalize_mute_symbol(opportunity.symbol)
    buy, sell = opportunity.buy_exchange.lower(), opportunity.sell_exchange.lower()
    for mute in settings.big_spread_alert_mutes:
        if mute.symbol != symbol or not mute.is_active(now):
            continue
        def matches(first: str, second: str) -> bool:
            return (mute.buy_exchange in {"*", first}
                    and mute.sell_exchange in {"*", second})
        if matches(buy, sell) or (mute.both_directions and matches(sell, buy)):
            return True
    return False


def big_spread_eligible(
    opportunity: Opportunity, rule: AlertRule, settings: RiskSettings, now: datetime,
) -> bool:
    if not settings.big_spread_alert_enabled or big_spread_is_muted(opportunity, settings, now):
        return False
    if opportunity.identity_enforced and (
        opportunity.validation is None
        or not opportunity.validation.capabilities.strategy_alert
        or opportunity.validation.expires_at <= now
    ):
        return False
    if opportunity.type not in rule.types or symbol_is_excluded(opportunity.symbol, settings):
        return False
    excluded = ignored_exchange_set(settings) | set(rule.exclude_exchanges)
    if {opportunity.buy_exchange.lower(), opportunity.sell_exchange.lower()} & excluded:
        return False
    volume = known_volume_24h_usdt(opportunity)
    return (
        isfinite(opportunity.open_spread_pct)
        and opportunity.open_spread_pct + EPSILON >= settings.big_spread_alert_pct
        and volume is not None and isfinite(volume) and volume >= rule.min_volume_24h_usdt
        and (now - opportunity.last_seen_at).total_seconds() <= rule.max_data_age_seconds
    )


class BigSpreadAlertEngine(AlertEngine):
    """Delivery retries share the normal engine; hit and escalation state is bounded."""

    def __init__(self) -> None:
        super().__init__()
        self._last_spread: dict[str, float] = {}
        self._seen: dict[str, datetime] = {}
        self._active: set[str] = set()
        self._capacity_warning_logged = False

    def restore(self, states, now):
        self._last_spread.clear()
        self._active.clear()
        self._capacity_warning_logged = False
        self._seen = {state.key: now for state in states}
        unknown = super().restore(states, now)
        # Settings have not loaded yet: preserve the longest supported cooldown.
        self._prune(now, 604800)
        return unknown

    def _prune(self, now: datetime, cooldown: int, *, reserve: int = 0) -> None:
        cutoff = now - timedelta(seconds=max(cooldown, 3600))
        removable = {
            key for key in self._seen
            if key not in self._active
            and (key not in self._last_sent
                 or (now - self._last_sent[key]).total_seconds() >= cooldown)
            and self._retry_after.get(key, now) <= now
        }
        expired = {key for key in removable if self._seen[key] < cutoff}
        overflow = len(self._seen) - len(expired) - MAX_BIG_SPREAD_KEYS + reserve
        if overflow > 0:
            expired.update(sorted(removable - expired, key=self._seen.__getitem__)[:overflow])
        for key in expired:
            for state in (self._seen, self._hits, self._observations, self._last_sent,
                          self._last_spread, self._retry_after, self._retry_attempts):
                state.pop(key, None)

    def evaluate(self, opportunities, rules, now=None, risk_settings=None) -> list[AlertMatch]:
        current = now or datetime.now(UTC)
        settings = risk_settings or RiskSettings()
        rule = delivery_rule(rules, settings)
        candidates = {}
        for item in opportunities:
            if big_spread_eligible(item, rule, settings, current):
                candidates.setdefault(f"{BIG_SPREAD_RULE_ID}:{item.id}", item)
        self._active = self._seen.keys() & candidates.keys()
        self._prune(current, rule.cooldown_seconds)
        active = set()
        matches = []
        for key, item in candidates.items():
            if key not in self._seen:
                if len(self._seen) >= MAX_BIG_SPREAD_KEYS:
                    self._prune(current, rule.cooldown_seconds, reserve=1)
                if len(self._seen) >= MAX_BIG_SPREAD_KEYS:
                    if not self._capacity_warning_logged:
                        logger.warning("big spread alert capacity reached; new keys paused to preserve cooldowns")
                        self._capacity_warning_logged = True
                    continue
                self._capacity_warning_logged = False
            active.add(key)
            self._active.add(key)
            self._seen[key] = current
            count = min(self._hits.get(key, (0, current))[0] + 1, rule.consecutive_hits)
            self._hits[key] = (count, current)
            if count < rule.consecutive_hits:
                continue
            last_sent = self._last_sent.get(key)
            if last_sent is not None and (current - last_sent).total_seconds() < rule.cooldown_seconds:
                last_spread = self._last_spread.get(key)
                # Restored cooldowns have no price baseline: wait out the cooldown.
                if last_spread is None or item.open_spread_pct + EPSILON < last_spread * 1.5:
                    continue
            retry_after = self._retry_after.get(key)
            if retry_after is not None and current < retry_after:
                continue
            matches.append(AlertMatch(rule, item, [observe_alert_metrics(item, current)]))
        for key in list(self._hits):
            if key not in active:
                self._hits.pop(key, None)
        self._prune(current, rule.cooldown_seconds)
        return [match for match in matches if f"{BIG_SPREAD_RULE_ID}:{match.opportunity.id}" in self._seen]

    def record_delivery_result(self, match, status, now=None) -> None:
        current = now or datetime.now(UTC)
        super().record_delivery_result(match, status, now=current)
        key = f"{BIG_SPREAD_RULE_ID}:{match.opportunity.id}"
        self._seen[key] = current
        if status == "sent":
            self._last_spread[key] = match.opportunity.open_spread_pct
        self._prune(current, match.rule.cooldown_seconds)
