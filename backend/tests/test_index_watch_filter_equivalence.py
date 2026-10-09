from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_symbol_aliases import snapshot

from app.models.index_component import index_watch_symbol
from app.models.market import MarketType
from app.services.collector import MarketCollector
from app.services.snapshot_store import SnapshotStore


async def reference_filter(collector, markets):
    """Original filter and matching order, frozen before normalization reuse."""
    watched = await collector._index_component_watched_symbols()
    if watched is None:
        return markets

    def matches(symbol):
        normalized = index_watch_symbol(symbol)
        return any(index_watch_symbol(item) == normalized for item in watched)

    return [m for m in markets if matches(m.symbol) or (
        m.symbol_alias_original_symbol is not None and matches(m.symbol_alias_original_symbol)
    )]


@pytest.mark.asyncio
@pytest.mark.parametrize("watch", [set(), {"BTC"}, {" btc-usdt "}, {"xyz:BTC", "BTCUSDT"},
                                  {"1000PEPE", "ETH/USDT"}, {":"}, {"BTC", ":"},
                                  {"", "  "}, {"NOMATCH", "cash:ETH"}])
@pytest.mark.parametrize("malformed_market", [None, "symbol", "symbol_alias_original_symbol"])
async def test_watch_filter_preserves_objects_order_aliases_and_error_behavior(watch, malformed_market):
    markets = [snapshot("binance", "BTCUSDT", MarketType.SPOT),
               snapshot("hyperliquid", "BTCUSDT", MarketType.FUTURE, "xyz:BTC"),
               snapshot("hyperliquid", "BTCUSDT", MarketType.FUTURE, "cash:BTC"),
               snapshot("okx", "ETHUSDT", MarketType.FUTURE),
               snapshot("binance", "PEPEUSDT").model_copy(update={
                   "symbol_alias_original_symbol": "1000PEPEUSDT",
                   "symbol_alias_price_multiplier": 0.001,
               })]
    if malformed_market:
        markets.append(snapshot("binance", "MISSINGUSDT").model_copy(update={malformed_market: ":"}))
    original = [m.model_dump() for m in markets]
    monitor = SimpleNamespace(watched_symbols=AsyncMock(return_value=watch))
    collector = MarketCollector([], SnapshotStore(), index_component_monitor=monitor)
    try:
        expected = await reference_filter(collector, markets)
    except ValueError as error:
        with pytest.raises(ValueError, match=str(error)):
            await collector._index_component_markets(markets)
    else:
        actual = await collector._index_component_markets(markets)
        assert len(actual) == len(expected)
        assert all(a is b for a, b in zip(actual, expected, strict=True))
    assert [m.model_dump() for m in markets] == original


@pytest.mark.asyncio
async def test_watch_list_refreshes_each_call_and_absent_filter_keeps_input():
    markets = [snapshot("binance", "BTCUSDT"), snapshot("binance", "ETHUSDT")]
    monitor = SimpleNamespace(watched_symbols=AsyncMock(side_effect=[{"BTC"}, {"ETH"}, set()]))
    collector = MarketCollector([], SnapshotStore(), index_component_monitor=monitor)
    assert await collector._index_component_markets(markets) == markets[:1]
    assert await collector._index_component_markets(markets) == markets[1:]
    assert await collector._index_component_markets(markets) == []
    collector.index_component_monitor = SimpleNamespace()
    assert await collector._index_component_markets(markets) is markets
