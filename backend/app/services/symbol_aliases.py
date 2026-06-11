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
        self._by_key = {_alias_key(alias): alias.canonical_symbol for alias in aliases}

    def canonical_symbol_for(self, market: MarketSnapshot) -> str:
        exchange = market.exchange.lower()
        market_type = market.market_type.value
        symbol = market.symbol.upper()
        return (
            self._by_key.get((exchange, symbol, market_type))
            or self._by_key.get((exchange, symbol, None))
            or symbol
        )


def apply_symbol_aliases(
    markets: list[MarketSnapshot],
    aliases: list[SymbolAlias],
) -> list[MarketSnapshot]:
    if not aliases:
        return markets

    resolver = SymbolAliasResolver(aliases)
    normalized: list[MarketSnapshot] = []
    for market in markets:
        canonical = resolver.canonical_symbol_for(market)
        if canonical == market.symbol:
            normalized.append(market)
            continue
        normalized.append(
            market.model_copy(
                update={
                    "symbol": canonical,
                    "base": _base_from_symbol(canonical),
                }
            )
        )
    return normalized
