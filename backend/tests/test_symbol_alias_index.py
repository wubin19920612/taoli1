import random
from itertools import product
from unittest.mock import patch

import pytest
from symbol_aliases_reference import ReferenceAliasResolver
from test_symbol_aliases import snapshot

from app.models.market import MarketType
from app.models.settings import SymbolAlias
from app.services.symbol_aliases import SymbolAliasResolver, apply_symbol_aliases


def aliases_for(seed, count):
    rng = random.Random(seed)
    aliases = []
    for _ in range(count):
        exchange = rng.choice(["hyperliquid", "gate", "okx"])
        aliases.append(SymbolAlias(
            exchange=exchange, symbol=rng.choice(["ANTH", "RAW0", "RAW1", "RAW2"]),
            canonical_symbol=rng.choice(["ANTHROPIC", "CANON0", "RAW1"]),
            market_type=rng.choice([None, MarketType.FUTURE]),
            dex=rng.choice([None, "xyz", "io", "cash"]) if exchange == "hyperliquid" else None,
            price_multiplier=rng.choice([0.001, 1, 10, 1000]),
        ))
    return aliases


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("count", [0, 1, 8, 80])
def test_index_matches_frozen_lookup_for_direct_reverse_and_exact_markets(seed, count):
    aliases = aliases_for(seed, count)
    before, after = ReferenceAliasResolver(aliases), SymbolAliasResolver(aliases)
    for exchange, kind, dex, symbol in product(
        ["hyperliquid", "gate", "okx", "binance"], list(MarketType),
        [None, "main", "io", "xyz", "cash"],
        ["ANTH", "ANTHROPIC", "RAW0", "RAW1", "RAW2", "CANON0", "UNMATCHED"],
    ):
        kwargs = {"exchange": exchange, "symbol": symbol, "market_type": kind, "dex": dex}
        assert after.resolve(**kwargs) == before.resolve(**kwargs)
        raw = f"{dex}:{symbol}" if exchange == "hyperliquid" and dex else symbol
        market = snapshot(exchange, symbol + "USDT", kind, raw_symbol=raw)
        assert after.alias_for(market) is before.alias_for(market)


def test_ambiguous_exact_market_never_falls_back_to_unique_generic_alias():
    aliases = [
        SymbolAlias(exchange="hyperliquid", symbol="RAW", canonical_symbol="GENERIC", dex="cash"),
        SymbolAlias(exchange="hyperliquid", symbol="RAW", canonical_symbol="FIRST",
                    dex="xyz", market_type=MarketType.FUTURE),
        SymbolAlias(exchange="hyperliquid", symbol="RAW", canonical_symbol="SECOND",
                    dex="io", market_type=MarketType.FUTURE),
    ]
    resolver = SymbolAliasResolver(aliases)
    target = snapshot("hyperliquid", "RAWUSDT", MarketType.FUTURE)
    assert resolver.alias_for(target) is None
    assert resolver.resolve(exchange="hyperliquid", symbol="RAW", market_type=MarketType.FUTURE,
                            dex="xyz").canonical_symbol == "FIRSTUSDT"


def test_overrides_are_deduplicated_before_ambiguity_and_settings_are_not_cached_globally():
    first = SymbolAlias(exchange="hyperliquid", symbol="ANTH", canonical_symbol="OVERRIDE",
                        market_type=MarketType.FUTURE, dex="io", price_multiplier=10)
    final = first.model_copy(update={"canonical_symbol": "FINALUSDT"})
    target = snapshot("hyperliquid", "ANTHUSDT", MarketType.FUTURE)
    assert SymbolAliasResolver([first, final]).alias_for(target) is final
    assert SymbolAliasResolver([]).alias_for(target).canonical_symbol == "ANTHROPICUSDT"
    assert SymbolAliasResolver([first]).alias_for(target) is first


@pytest.mark.parametrize("seed", range(6))
def test_complete_market_transformation_fields_order_and_ownership_match(seed):
    aliases = aliases_for(seed, 80)
    markets = [snapshot(exchange, symbol, kind, raw_symbol=raw)
               for exchange, kind, symbol, raw in product(
                   ["hyperliquid", "gate", "okx"], list(MarketType),
                   ["ANTHUSDT", "RAW0USDT", "RAW1USDT", "RAW2USDT", "NOMATCHUSDT"],
                   ["RAW0", "xyz:RAW0", "io:RAW1"],
               )]
    originals = [m.model_dump() for m in markets]
    with patch("app.services.symbol_aliases.SymbolAliasResolver", ReferenceAliasResolver):
        expected = apply_symbol_aliases(markets, aliases)
    actual = apply_symbol_aliases(markets, aliases)
    assert [m.model_dump() for m in actual] == [m.model_dump() for m in expected]
    assert [a is m for a, m in zip(actual, markets, strict=True)] == [
        a is m for a, m in zip(expected, markets, strict=True)
    ]
    assert [m.model_dump() for m in markets] == originals
