from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.market import MarketSnapshot
from app.models.phone_alert import (
    PhonePriceAlertCondition,
    PhonePriceAlertPriceField,
    PhonePriceAlertRule,
)


@dataclass(frozen=True)
class PhonePriceAlertMatch:
    rule: PhonePriceAlertRule
    market: MarketSnapshot
    observed_price: float
    resolved_price_field: PhonePriceAlertPriceField


class PhonePriceAlertEngine:
    def __init__(self) -> None:
        self._last_sent: dict[str, datetime] = {}

    def evaluate(
        self,
        markets: list[MarketSnapshot],
        rules: list[PhonePriceAlertRule],
        now: datetime | None = None,
    ) -> list[PhonePriceAlertMatch]:
        current = now or datetime.now(UTC)
        matches: list[PhonePriceAlertMatch] = []
        for rule in rules:
            if not rule.enabled:
                continue
            for market in markets:
                if not _market_matches_rule(market, rule):
                    continue
                price_result = _resolve_price(market, rule.price_field)
                if price_result is None:
                    continue
                observed_price, resolved_field = price_result
                if not _price_crosses_threshold(rule, observed_price):
                    continue
                last_sent = self._last_sent.get(rule.id)
                if last_sent and (current - last_sent).total_seconds() < rule.cooldown_seconds:
                    continue
                self._last_sent[rule.id] = current
                matches.append(
                    PhonePriceAlertMatch(
                        rule=rule,
                        market=market,
                        observed_price=observed_price,
                        resolved_price_field=resolved_field,
                    )
                )
                break
        return matches


def _market_matches_rule(market: MarketSnapshot, rule: PhonePriceAlertRule) -> bool:
    if market.symbol.upper() != rule.symbol:
        return False
    if market.market_type != rule.market_type:
        return False
    if rule.exchange and market.exchange.lower() != rule.exchange:
        return False
    return True


def _resolve_price(
    market: MarketSnapshot,
    price_field: PhonePriceAlertPriceField,
) -> tuple[float, PhonePriceAlertPriceField] | None:
    if price_field == PhonePriceAlertPriceField.MARK_PRICE:
        if market.mark_price is not None:
            return market.mark_price, price_field
        return _resolve_price(market, PhonePriceAlertPriceField.MID_PRICE)
    if price_field == PhonePriceAlertPriceField.INDEX_PRICE:
        if market.index_price is not None:
            return market.index_price, price_field
        return _resolve_price(market, PhonePriceAlertPriceField.MID_PRICE)
    if price_field == PhonePriceAlertPriceField.MID_PRICE:
        return (market.bid + market.ask) / 2, price_field
    if price_field == PhonePriceAlertPriceField.BID:
        return market.bid, price_field
    if price_field == PhonePriceAlertPriceField.ASK:
        return market.ask, price_field
    return None


def _price_crosses_threshold(rule: PhonePriceAlertRule, price: float) -> bool:
    if rule.condition == PhonePriceAlertCondition.ABOVE:
        return price >= rule.target_price
    return price <= rule.target_price


def find_phone_price_alert_market(
    markets: list[MarketSnapshot],
    rule: PhonePriceAlertRule,
) -> MarketSnapshot | None:
    return next((market for market in markets if _market_matches_rule(market, rule)), None)


def resolve_phone_price_alert_price(
    market: MarketSnapshot,
    price_field: PhonePriceAlertPriceField,
) -> tuple[float, PhonePriceAlertPriceField] | None:
    return _resolve_price(market, price_field)


def phone_price_crosses_threshold(rule: PhonePriceAlertRule, price: float) -> bool:
    return _price_crosses_threshold(rule, price)


def build_phone_price_alert_message(
    match: PhonePriceAlertMatch,
    observed_at: datetime | None = None,
) -> str:
    direction = ">=" if match.rule.condition == PhonePriceAlertCondition.ABOVE else "<="
    exchange = match.market.exchange
    market_type = match.market.market_type.value
    return (
        f"[Phone price alert] {match.rule.name}\n"
        f"Symbol: {match.market.symbol}\n"
        f"Market: {exchange} {market_type}\n"
        f"Price: {match.observed_price:g} ({match.resolved_price_field.value})\n"
        f"Trigger: {direction} {match.rule.target_price:g}\n"
        f"Observed at: {(observed_at or datetime.now(UTC)).isoformat()}"
    )
