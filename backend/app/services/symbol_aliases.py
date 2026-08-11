from app.models.market import MarketSnapshot
from app.models.settings import SymbolAlias


def _alias_key(alias: SymbolAlias) -> tuple[str, str, str | None]:
    return (
        alias.exchange.lower(),
        alias.symbol.upper(),
        alias.market_type.value if alias.market_type is not None else None,
    )


def _base_from_symbol(symbol: str) -> str:
    return symbol.removesuffix("USDT")


class SymbolAliasResolver:
    def __init__(self, aliases: list[SymbolAlias]) -> None:
        self._by_key = {_alias_key(alias): alias for alias in aliases}

    def alias_for(self, market: MarketSnapshot) -> SymbolAlias | None:
        exchange = market.exchange.lower()
        market_type = market.market_type.value
        symbol = market.symbol.upper()
        return self._by_key.get((exchange, symbol, market_type)) or self._by_key.get(
            (exchange, symbol, None)
        )

    def canonical_symbol_for(self, market: MarketSnapshot) -> str:
        alias = self.alias_for(market)
        return alias.canonical_symbol if alias is not None else market.symbol.upper()


def _scaled(value: float | None, multiplier: float) -> float | None:
    return value * multiplier if value is not None else None


def _scaled_size(value: float | None, multiplier: float) -> float | None:
    return value / multiplier if value is not None else None


def apply_symbol_aliases(
    markets: list[MarketSnapshot],
    aliases: list[SymbolAlias],
) -> list[MarketSnapshot]:
    if not aliases:
        return markets

    resolver = SymbolAliasResolver(aliases)
    normalized: list[MarketSnapshot] = []
    for market in markets:
        alias = resolver.alias_for(market)
        if alias is None:
            normalized.append(market)
            continue
        multiplier = alias.price_multiplier
        canonical = alias.canonical_symbol
        if canonical == market.symbol and multiplier == 1:
            normalized.append(market)
            continue
        normalized.append(
            market.model_copy(
                update={
                    "symbol": canonical,
                    "base": _base_from_symbol(canonical),
                    "bid": market.bid * multiplier,
                    "ask": market.ask * multiplier,
                    "bid_size": _scaled_size(market.bid_size, multiplier),
                    "ask_size": _scaled_size(market.ask_size, multiplier),
                    "mark_price": _scaled(market.mark_price, multiplier),
                    "index_price": _scaled(market.index_price, multiplier),
                    "symbol_alias_original_symbol": market.symbol,
                    "symbol_alias_price_multiplier": multiplier,
                }
            )
        )
    return normalized
