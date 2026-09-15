from datetime import UTC, datetime

from app.models.market import MarketSnapshot
from app.models.opportunity import Opportunity
from app.models.settings import RiskSettings
from app.services.market_sessions import is_opportunity_tradable


def normalize_symbol(value: str) -> str:
    return value.upper().replace("-", "").replace("_", "")


def normalize_exchange(value: str) -> str:
    return value.lower()


def excluded_symbol_set(settings: RiskSettings) -> set[str]:
    return {normalize_symbol(item) for item in settings.excluded_symbols}


def symbol_is_excluded(symbol: str, settings: RiskSettings) -> bool:
    return normalize_symbol(symbol) in excluded_symbol_set(settings)


def ignored_exchange_set(settings: RiskSettings) -> set[str]:
    return {normalize_exchange(item) for item in settings.ignored_exchanges}


def market_is_excluded(market: MarketSnapshot, settings: RiskSettings) -> bool:
    ignored_exchanges = ignored_exchange_set(settings)
    return (
        symbol_is_excluded(market.symbol, settings)
        or normalize_exchange(market.exchange) in ignored_exchanges
    )


def opportunity_is_excluded(
    opportunity: Opportunity,
    settings: RiskSettings,
    now: datetime | None = None,
) -> bool:
    if symbol_is_excluded(opportunity.symbol, settings):
        return True
    ignored_exchanges = ignored_exchange_set(settings)
    return (
        normalize_exchange(opportunity.buy_exchange) in ignored_exchanges
        or normalize_exchange(opportunity.sell_exchange) in ignored_exchanges
        or not is_opportunity_tradable(opportunity, now or datetime.now(UTC))
    )


def filter_markets(markets: list[MarketSnapshot], settings: RiskSettings) -> list[MarketSnapshot]:
    return [market for market in markets if not market_is_excluded(market, settings)]


def filter_opportunities(
    opportunities: list[Opportunity],
    settings: RiskSettings,
    now: datetime | None = None,
) -> list[Opportunity]:
    return [
        item
        for item in opportunities
        if not opportunity_is_excluded(item, settings, now=now)
    ]


def filter_exchange_errors(errors: dict[str, str], settings: RiskSettings) -> dict[str, str]:
    ignored_exchanges = ignored_exchange_set(settings)
    return {
        key: value
        for key, value in errors.items()
        if normalize_exchange(key.split(":", 1)[0]) not in ignored_exchanges
    }
