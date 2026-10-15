"""Frozen direct alias lookup from 16a1185; other alias behavior is unchanged."""
from app.models.settings import SymbolAlias
from app.services.symbol_aliases import KNOWN_SYMBOL_ALIASES, SymbolAliasResolver, _alias_key


class ReferenceAliasResolver(SymbolAliasResolver):
    def __init__(self, aliases: list[SymbolAlias]) -> None:
        self._by_key = {
            _alias_key(alias): alias
            for alias in (*KNOWN_SYMBOL_ALIASES, *aliases)
        }

    def _direct_alias(
        self,
        *,
        exchange: str,
        symbol: str,
        market_type: str,
        dex: str | None,
    ) -> SymbolAlias | None:
        keys = [
            (exchange, symbol, market_type, dex),
            (exchange, symbol, None, dex),
        ]
        if dex is not None:
            keys.extend(
                [
                    (exchange, symbol, market_type, None),
                    (exchange, symbol, None, None),
                ]
            )
        direct = next((self._by_key[key] for key in keys if key in self._by_key), None)
        if direct is not None or dex is not None:
            return direct

        # A raw HIP-3 ticker can safely imply its DEX only when the configured
        # alias is unique. Multiple DEX matches remain unresolved until the
        # caller supplies an explicit DEX.
        exact_market = [
            alias
            for (
                alias_exchange,
                alias_symbol,
                alias_market_type,
                alias_dex,
            ), alias in self._by_key.items()
            if alias_exchange == exchange
            and alias_symbol == symbol
            and alias_market_type == market_type
            and alias_dex is not None
        ]
        candidates = exact_market or [
            alias
            for (
                alias_exchange,
                alias_symbol,
                alias_market_type,
                alias_dex,
            ), alias in self._by_key.items()
            if alias_exchange == exchange
            and alias_symbol == symbol
            and alias_market_type is None
            and alias_dex is not None
        ]
        return candidates[0] if len(candidates) == 1 else None
