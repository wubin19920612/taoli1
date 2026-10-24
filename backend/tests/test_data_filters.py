from datetime import UTC, datetime

from app.models.market import MarketSnapshot, MarketType
from app.models.settings import RiskSettings
from app.services.data_filters import filter_markets, filter_opportunities
from app.services.spread_engine import build_directional_opportunity

NOW = datetime(2026, 9, 24, tzinfo=UTC)


def market(symbol: str, exchange: str, market_type: MarketType) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        base=symbol.removesuffix("USDT"),
        exchange=exchange,
        market_type=market_type,
        bid=99,
        ask=100,
        timestamp=NOW,
        raw_symbol=symbol,
    )


def test_batch_filters_keep_only_nonexcluded_markets_and_opportunities() -> None:
    btc_spot = market("BTCUSDT", "binance", MarketType.SPOT)
    btc_future = market("BTCUSDT", "okx", MarketType.FUTURE)
    eth_spot = market("ETHUSDT", "binance", MarketType.SPOT)
    eth_future = market("ETHUSDT", "okx", MarketType.FUTURE)
    btc_opportunity = build_directional_opportunity(btc_spot, btc_future, mode="SF")
    eth_opportunity = build_directional_opportunity(eth_spot, eth_future, mode="SF")

    settings = RiskSettings(excluded_symbols=["btc-usdt"], ignored_exchanges=["OKX"])
    assert filter_markets([btc_spot, btc_future, eth_spot, eth_future], settings) == [eth_spot]
    assert filter_opportunities([btc_opportunity, eth_opportunity], settings, now=NOW) == []

    settings = RiskSettings(excluded_symbols=["btc-usdt"])
    assert filter_opportunities([btc_opportunity, eth_opportunity], settings, now=NOW) == [
        eth_opportunity
    ]
