from datetime import UTC, datetime

from app.models.market import MarketSnapshot, MarketType
from app.services.spread_engine import build_opportunities, midpoint_spread_pct


def snapshot(exchange: str, market_type: MarketType, bid: float, ask: float) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        quote="USDT",
        exchange=exchange,
        market_type=market_type,
        bid=bid,
        ask=ask,
        volume_24h_usdt=10_000_000,
        timestamp=datetime(2026, 5, 15, tzinfo=UTC),
        raw_symbol="BTCUSDT",
    )


def test_midpoint_spread_pct_uses_bid_ask_formula() -> None:
    buy = snapshot("binance", MarketType.SPOT, bid=99, ask=100)
    sell = snapshot("okx", MarketType.FUTURE, bid=102, ask=103)

    open_spread, close_spread = midpoint_spread_pct(buy, sell)

    assert round(open_spread, 6) == round(2 * (102 - 100) / (100 + 102) * 100, 6)
    assert round(close_spread, 6) == round(2 * (103 - 99) / (99 + 103) * 100, 6)


def test_builds_sf_opportunity_from_spot_and_future() -> None:
    markets = [
        snapshot("binance", MarketType.SPOT, bid=99, ask=100),
        snapshot("okx", MarketType.FUTURE, bid=102, ask=103),
    ]

    opportunities = build_opportunities(markets, mode="SF")

    assert len(opportunities) == 1
    assert opportunities[0].symbol == "BTCUSDT"
    assert opportunities[0].buy_exchange == "binance"
    assert opportunities[0].sell_exchange == "okx"
    assert opportunities[0].type == "SF"


def test_builds_ff_opportunity_and_orients_positive_spread() -> None:
    markets = [
        snapshot("binance", MarketType.FUTURE, bid=99, ask=100),
        snapshot("okx", MarketType.FUTURE, bid=102, ask=103),
    ]

    opportunities = build_opportunities(markets, mode="FF")

    assert len(opportunities) == 1
    assert opportunities[0].buy_exchange == "binance"
    assert opportunities[0].sell_exchange == "okx"
    assert opportunities[0].open_spread_pct > 0


def test_skips_symbols_without_two_matching_markets() -> None:
    opportunities = build_opportunities(
        [snapshot("binance", MarketType.SPOT, bid=99, ask=100)],
        mode="SF",
    )

    assert opportunities == []
