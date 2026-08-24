from datetime import UTC, datetime

import pytest

from app.models.market import MarketSnapshot, MarketType
from app.models.settings import RiskSettings, SymbolAlias
from app.services.spread_engine import build_opportunities
from app.services.symbol_aliases import apply_symbol_aliases, resolve_symbol_alias


def snapshot(
    exchange: str,
    symbol: str,
    market_type: MarketType = MarketType.SPOT,
    raw_symbol: str | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        base=symbol.removesuffix("USDT"),
        quote="USDT",
        exchange=exchange,
        market_type=market_type,
        bid=99,
        ask=100,
        volume_24h_usdt=10_000_000,
        timestamp=datetime(2026, 6, 3, tzinfo=UTC),
        raw_symbol=raw_symbol or symbol,
    )


def test_default_symbol_alias_maps_gate_edgex_to_edge() -> None:
    settings = RiskSettings(ticker_collision_symbols=[])

    gate = snapshot("gate", "EDGEXUSDT", raw_symbol="EDGEX_USDT")
    binance = snapshot("binance", "EDGEXUSDT")
    aliased = apply_symbol_aliases([gate, binance], settings.symbol_aliases)

    assert aliased[0].symbol == "EDGEUSDT"
    assert aliased[0].base == "EDGE"
    assert aliased[0].raw_symbol == "EDGEX_USDT"
    assert aliased[0].symbol_alias_original_symbol == "EDGEXUSDT"
    assert aliased[0].symbol_alias_price_multiplier == 1
    assert aliased[1].symbol == "EDGEXUSDT"


def test_symbol_alias_normalizes_case_and_optional_market_type() -> None:
    alias = SymbolAlias(
        exchange="Gate",
        symbol="edgex",
        canonical_symbol="edge",
        market_type=MarketType.FUTURE,
    )
    spot = snapshot("gate", "EDGEXUSDT", MarketType.SPOT, "EDGEX_USDT")
    future = snapshot("gate", "EDGEXUSDT", MarketType.FUTURE, "EDGEX_USDT")

    aliased = apply_symbol_aliases([spot, future], [alias])

    assert aliased[0].symbol == "EDGEXUSDT"
    assert aliased[1].symbol == "EDGEUSDT"


def test_symbol_alias_price_multiplier_scales_prices_but_not_usdt_volume() -> None:
    nex = snapshot("bitget", "NEXUSDT", MarketType.SPOT, "NEXUSDT").model_copy(
        update={
            "bid": 0.0101,
            "ask": 0.0102,
            "bid_size": 20_000,
            "ask_size": 30_000,
            "mark_price": None,
            "index_price": 0.01015,
            "volume_24h_usdt": 123_456,
        }
    )
    aliased = apply_symbol_aliases(
        [nex],
        [
            SymbolAlias(
                exchange="bitget",
                symbol="NEX",
                canonical_symbol="10000NEX",
                market_type=MarketType.SPOT,
                price_multiplier=10_000,
            )
        ],
    )

    assert aliased[0].symbol == "10000NEXUSDT"
    assert aliased[0].base == "10000NEX"
    assert aliased[0].bid == pytest.approx(101)
    assert aliased[0].ask == pytest.approx(102)
    assert aliased[0].bid_size == pytest.approx(2)
    assert aliased[0].ask_size == pytest.approx(3)
    assert aliased[0].index_price == pytest.approx(101.5)
    assert aliased[0].volume_24h_usdt == pytest.approx(123_456)
    assert aliased[0].symbol_alias_original_symbol == "NEXUSDT"
    assert aliased[0].symbol_alias_price_multiplier == pytest.approx(10_000)


def test_symbol_alias_resolver_accepts_canonical_or_raw_symbol() -> None:
    aliases = [
        SymbolAlias(
            exchange="gate",
            symbol="NEX",
            canonical_symbol="10000NEX",
            market_type=MarketType.FUTURE,
            price_multiplier=10_000,
        )
    ]

    from_canonical = resolve_symbol_alias(
        aliases,
        exchange="gate",
        symbol="10000NEX",
        market_type=MarketType.FUTURE,
    )
    from_raw = resolve_symbol_alias(
        aliases,
        exchange="gate",
        symbol="NEX",
        market_type=MarketType.FUTURE,
    )

    assert from_canonical.raw_symbol == "NEXUSDT"
    assert from_canonical.canonical_symbol == "10000NEXUSDT"
    assert from_canonical.price_multiplier == pytest.approx(10_000)
    assert from_raw.requested_symbol == "NEXUSDT"
    assert from_raw.raw_symbol == from_canonical.raw_symbol
    assert from_raw.canonical_symbol == from_canonical.canonical_symbol
    assert from_raw.price_multiplier == from_canonical.price_multiplier


def test_alias_enables_cross_exchange_opportunity_with_raw_leg_symbols() -> None:
    gate = snapshot("gate", "EDGEXUSDT", MarketType.SPOT, "EDGEX_USDT")
    binance = snapshot("binance", "EDGEUSDT", MarketType.FUTURE, "EDGEUSDT").model_copy(
        update={"bid": 103, "ask": 104}
    )
    markets = apply_symbol_aliases(
        [gate, binance],
        [SymbolAlias(exchange="gate", symbol="EDGEXUSDT", canonical_symbol="EDGEUSDT")],
    )

    opportunities = build_opportunities(markets, mode="SF")

    assert len(opportunities) == 1
    assert opportunities[0].symbol == "EDGEUSDT"
    assert opportunities[0].buy_exchange == "gate"
    assert opportunities[0].buy_raw_symbol == "EDGEX_USDT"
    assert opportunities[0].sell_exchange == "binance"
    assert opportunities[0].sell_raw_symbol == "EDGEUSDT"
