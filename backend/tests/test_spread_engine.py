from datetime import UTC, datetime, timedelta

import pytest

from app.models.market import MarketSnapshot, MarketType
from app.services.spread_engine import _market_identity, build_opportunities, midpoint_spread_pct


def snapshot(exchange: str, market_type: MarketType, bid: float, ask: float) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        quote="USDT",
        exchange=exchange,
        market_type=market_type,
        bid=bid,
        ask=ask,
        funding_rate_pct=None,
        funding_next_rate_pct=None,
        funding_interval_hours=None,
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


def test_opportunity_identity_keeps_raw_market_dex_and_multiplier() -> None:
    io_market = snapshot("hyperliquid", MarketType.FUTURE, bid=102, ask=103).model_copy(
        update={"raw_symbol": "io:ANTH", "dex": "io"}
    )
    xyz_market = snapshot("hyperliquid", MarketType.FUTURE, bid=104, ask=105).model_copy(
        update={
            "raw_symbol": "xyz:ANTH",
            "dex": "xyz",
            "symbol_alias_price_multiplier": 10,
        }
    )
    lighter = snapshot("lighter", MarketType.FUTURE, bid=99, ask=100).model_copy(
        update={"raw_symbol": "ANTHROPIC", "contract_size_multiplier": 1}
    )

    opportunities = build_opportunities([lighter, io_market, xyz_market], mode="FF")

    lighter_routes = [item for item in opportunities if item.buy_exchange == "lighter"]
    assert len(lighter_routes) == 2
    assert {item.sell_dex for item in lighter_routes} == {"io", "xyz"}
    assert {item.sell_raw_symbol for item in lighter_routes} == {"io:ANTH", "xyz:ANTH"}
    assert len({item.id for item in lighter_routes}) == 2
    assert {item.sell_price_multiplier for item in lighter_routes} == {1, 10}


def test_opportunity_identity_keeps_contract_size_multiplier() -> None:
    buy_one = snapshot("lighter", MarketType.FUTURE, bid=99, ask=100).model_copy(
        update={"raw_symbol": "ANTHROPIC", "contract_size_multiplier": 1}
    )
    buy_cent = buy_one.model_copy(update={"contract_size_multiplier": 0.01})
    sell = snapshot("binance", MarketType.FUTURE, bid=102, ask=103).model_copy(
        update={"raw_symbol": "ANTHROPICUSDT"}
    )

    [one] = build_opportunities([buy_one, sell], mode="FF")
    [cent] = build_opportunities([buy_cent, sell], mode="FF")

    assert one.id != cent.id
    assert one.buy_contract_size_multiplier == 1
    assert cent.buy_contract_size_multiplier == 0.01


def test_market_identity_does_not_append_empty_contract_multiplier() -> None:
    without_multiplier = snapshot("binance", MarketType.FUTURE, bid=99, ask=100)
    with_multiplier = without_multiplier.model_copy(update={"contract_size_multiplier": 1})

    assert _market_identity(without_multiplier) == (
        "binance",
        "future",
        "BTCUSDT",
        "",
        "1",
    )
    assert _market_identity(with_multiplier) == (
        "binance",
        "future",
        "BTCUSDT",
        "",
        "1",
        "1",
    )


def test_stale_market_does_not_generate_realtime_opportunity() -> None:
    now = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
    fresh = snapshot("lighter", MarketType.FUTURE, bid=99, ask=100).model_copy(
        update={"timestamp": now - timedelta(seconds=5), "raw_symbol": "ANTHROPIC"}
    )
    stale = snapshot("binance", MarketType.FUTURE, bid=102, ask=103).model_copy(
        update={"timestamp": now - timedelta(seconds=31), "raw_symbol": "ANTHROPICUSDT"}
    )

    opportunities = build_opportunities(
        [fresh, stale],
        mode="FF",
        now=now,
        stale_after_seconds=30,
    )

    assert opportunities == []


def test_upstream_timestamp_controls_realtime_freshness() -> None:
    now = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
    delayed = snapshot("lighter", MarketType.FUTURE, bid=99, ask=100).model_copy(
        update={
            "timestamp": now,
            "upstream_timestamp": now - timedelta(seconds=31),
            "raw_symbol": "ANTHROPIC",
        }
    )
    fresh = snapshot("binance", MarketType.FUTURE, bid=102, ask=103).model_copy(
        update={"timestamp": now, "raw_symbol": "ANTHROPICUSDT"}
    )

    assert build_opportunities(
        [delayed, fresh],
        mode="FF",
        now=now,
        stale_after_seconds=30,
    ) == []


def test_estimated_bid_or_ask_does_not_generate_realtime_opportunity() -> None:
    estimated = snapshot("hyperliquid", MarketType.FUTURE, bid=99, ask=100).model_copy(
        update={
            "raw_symbol": "io:ANTH",
            "dex": "io",
            "is_estimated": True,
            "estimated_fields": ["bid", "ask"],
        }
    )
    real = snapshot("lighter", MarketType.FUTURE, bid=102, ask=103).model_copy(
        update={"raw_symbol": "ANTHROPIC"}
    )

    assert build_opportunities([estimated, real], mode="FF") == []


def test_skips_symbols_without_two_matching_markets() -> None:
    opportunities = build_opportunities(
        [snapshot("binance", MarketType.SPOT, bid=99, ask=100)],
        mode="SF",
    )

    assert opportunities == []


def test_builds_ff_opportunity_with_predicted_and_normalized_funding() -> None:
    buy = snapshot("binance", MarketType.FUTURE, bid=99, ask=100).model_copy(
        update={
            "funding_rate_pct": 0.08,
            "funding_next_rate_pct": 0.09,
            "funding_interval_hours": 8,
        }
    )
    sell = snapshot("okx", MarketType.FUTURE, bid=102, ask=103).model_copy(
        update={
            "funding_rate_pct": 0.02,
            "funding_next_rate_pct": 0.05,
            "funding_interval_hours": 1,
        }
    )

    opportunities = build_opportunities([buy, sell], mode="FF")

    assert len(opportunities) == 1
    item = opportunities[0]
    assert item.funding_rate_buy_pct == 0.08
    assert item.funding_rate_sell_pct == 0.02
    assert item.funding_next_rate_buy_pct == 0.09
    assert item.funding_next_rate_sell_pct == 0.05
    assert item.buy_funding_interval_hours == 8
    assert item.sell_funding_interval_hours == 1
    assert item.net_funding_pct == pytest.approx(-0.06)
    assert item.net_funding_next_pct == pytest.approx(-0.04)
    assert item.net_funding_hourly_pct == pytest.approx(0.01)
    assert item.net_funding_daily_pct == pytest.approx(0.24)


def test_builds_open_depth_from_top_of_book_sizes() -> None:
    buy = snapshot("binance", MarketType.FUTURE, bid=99, ask=100).model_copy(
        update={"bid_size": 3.0, "ask_size": 2.0}
    )
    sell = snapshot("okx", MarketType.FUTURE, bid=102, ask=103).model_copy(
        update={"bid_size": 5.0, "ask_size": 4.0}
    )

    opportunities = build_opportunities([buy, sell], mode="FF")

    assert len(opportunities) == 1
    item = opportunities[0]
    assert item.buy_ask_depth_usdt == pytest.approx(200.0)
    assert item.sell_bid_depth_usdt == pytest.approx(510.0)
    assert item.min_open_depth_usdt == pytest.approx(200.0)


def test_builds_sf_opportunity_treats_spot_funding_as_zero() -> None:
    buy = snapshot("binance", MarketType.SPOT, bid=99, ask=100)
    sell = snapshot("okx", MarketType.FUTURE, bid=102, ask=103).model_copy(
        update={
            "funding_rate_pct": 0.03,
            "funding_next_rate_pct": 0.05,
            "funding_interval_hours": 8,
        }
    )

    opportunities = build_opportunities([buy, sell], mode="SF")

    assert len(opportunities) == 1
    item = opportunities[0]
    assert item.net_funding_pct == pytest.approx(0.03)
    assert item.net_funding_next_pct == pytest.approx(0.05)
    assert item.net_funding_hourly_pct == pytest.approx(0.00375)
    assert item.net_funding_daily_pct == pytest.approx(0.09)


def test_builds_rtoken_spot_to_perpetual_opportunity_with_positive_funding() -> None:
    rtoken_spot = snapshot("bitget", MarketType.SPOT, bid=311.18, ask=311.29).model_copy(
        update={
            "symbol": "AAPLUSDT",
            "base": "AAPL",
            "raw_symbol": "RAAPLUSDT",
            "symbol_alias_original_symbol": "RAAPLUSDT",
        }
    )
    perpetual = snapshot("binance", MarketType.FUTURE, bid=312, ask=312.1).model_copy(
        update={
            "symbol": "AAPLUSDT",
            "base": "AAPL",
            "funding_rate_pct": 0.04,
            "funding_next_rate_pct": 0.05,
            "funding_interval_hours": 8,
        }
    )

    opportunities = build_opportunities(
        [rtoken_spot, perpetual],
        mode="SF",
        now=datetime(2026, 5, 15, 15, 0, tzinfo=UTC),
    )

    assert len(opportunities) == 1
    item = opportunities[0]
    assert item.buy_exchange == "bitget"
    assert item.buy_raw_symbol == "RAAPLUSDT"
    assert item.sell_exchange == "binance"
    assert item.net_funding_pct == pytest.approx(0.04)
    assert item.net_funding_next_pct == pytest.approx(0.05)


def test_skips_rtoken_spot_to_perpetual_opportunity_when_us_stock_market_is_closed() -> None:
    rtoken_spot = snapshot("bitget", MarketType.SPOT, bid=311.18, ask=311.29).model_copy(
        update={
            "symbol": "AAPLUSDT",
            "base": "AAPL",
            "raw_symbol": "RAAPLUSDT",
            "symbol_alias_original_symbol": "RAAPLUSDT",
        }
    )
    perpetual = snapshot("binance", MarketType.FUTURE, bid=312, ask=312.1).model_copy(
        update={
            "symbol": "AAPLUSDT",
            "base": "AAPL",
            "funding_rate_pct": 0.04,
            "funding_next_rate_pct": 0.05,
            "funding_interval_hours": 8,
        }
    )

    opportunities = build_opportunities(
        [rtoken_spot, perpetual],
        mode="SF",
        now=datetime(2026, 9, 9, 21, 0, tzinfo=UTC),
    )

    assert opportunities == []
