"""Compare every output field and stable ordering against the original traversal."""
from datetime import UTC, datetime, timedelta
from itertools import permutations
from random import Random

import pytest
from spread_engine_reference import build_opportunities as reference_build

from app.models.market import MarketSnapshot, MarketType
from app.services.spread_engine import build_opportunities

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def market(index: int, **overrides) -> MarketSnapshot:
    values = {
        "symbol": "BTCUSDT", "base": "BTC", "exchange": f"exchange-{index}",
        "market_type": MarketType.FUTURE, "bid": 100 + index, "ask": 101 + index,
        "timestamp": NOW, "raw_symbol": "BTCUSDT", "volume_24h_usdt": 1_000_000,
    }
    values.update(overrides)
    return MarketSnapshot(**values)


def assert_equivalent(markets, mode, **kwargs):
    before = [item.model_dump() for item in markets]
    options = {"now": NOW, "buy_fee_pct": 0.03, "sell_fee_pct": 0.07, "safety_slippage_pct": 0.02}
    options.update(kwargs)
    expected = reference_build(markets, mode, **options)
    actual = build_opportunities(markets, mode, **options)
    assert [item.model_dump() for item in actual] == [item.model_dump() for item in expected]
    assert [item.model_dump() for item in markets] == before


@pytest.mark.parametrize("mode", ["SF", "FF", "SS"])
def test_duplicate_identity_and_equal_objects_keep_first_pair_semantics(mode):
    kind = MarketType.SPOT if mode == "SS" else MarketType.FUTURE
    first = market(0, market_type=kind)
    # Same identity with a different book must not be silently coalesced.
    changed = first.model_copy(update={"bid": 105.0, "ask": 106.0})
    other = market(1, market_type=kind, bid=110, ask=111)
    spot = market(2, market_type=MarketType.SPOT, bid=90, ask=91)
    for ordered in permutations([first, first.model_copy(deep=True), changed, other, spot]):
        assert_equivalent(list(ordered), mode)


@pytest.mark.parametrize("mode", ["SF", "FF", "SS"])
@pytest.mark.parametrize("seed", range(12))
def test_seeded_mixed_markets_match_all_fields_and_order(mode, seed):
    rng = Random(seed)
    markets = []
    for index in range(100):
        symbol = rng.choice(["BTCUSDT", "AAPLUSDT", "HOODUSDT"])
        raw = symbol
        exchange = rng.choice(["binance", "bitget", "hyperliquid", "lighter", "rh-lighter"])
        dex = None
        kind = rng.choice(list(MarketType))
        if exchange == "hyperliquid":
            dex = rng.choice([None, "xyz", "io", " main "])
            raw = f"{dex.strip()}:{symbol}" if dex else symbol
        elif exchange == "bitget" and kind == MarketType.SPOT and symbol == "AAPLUSDT":
            raw = "RAAPLUSDT"
        item = market(
            index, symbol=symbol, exchange=exchange, market_type=kind, raw_symbol=raw,
            dex=dex, bid=rng.choice([98, 100, 102]), ask=rng.choice([99, 101, 103]),
            timestamp=NOW - timedelta(seconds=rng.choice([0, 5, 30, 31])),
            upstream_timestamp=rng.choice([None, NOW - timedelta(seconds=60)]),
            funding_rate_pct=rng.choice([None, -0.03, 0.01]),
            funding_next_rate_pct=rng.choice([None, 0.02]),
            funding_interval_hours=rng.choice([None, 0, 1, 4, 8]),
            funding_next_time=NOW + timedelta(hours=1),
            contract_size_multiplier=rng.choice([None, 1, 1000]),
            symbol_alias_price_multiplier=rng.choice([1, 0.001]),
            volume_24h_usdt=rng.choice([None, 0, 10_000_000]),
            bid_size=rng.choice([None, 0, 10]), ask_size=rng.choice([None, 0, 20]),
            estimated_fields=rng.choice([[], [" BID "], ["ask"], ["volume_24h_usdt"]]),
            data_source="fixture",
        )
        markets.append(item)
        if index % 13 == 0:
            markets.extend([item, item.model_copy(deep=True)])
    rng.shuffle(markets)
    for stale_after in [None, 30]:
        assert_equivalent(markets, mode, stale_after_seconds=stale_after)
        assert_equivalent(
            markets, mode, stale_after_seconds=stale_after,
            now=datetime(2026, 10, 10, 15, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize("mode", ["SF", "FF", "SS"])
def test_tied_spreads_keep_input_order_and_dex_routes(mode):
    kind = MarketType.SPOT if mode == "SS" else MarketType.FUTURE
    markets = [
        market(0, market_type=MarketType.SPOT if mode == "SF" else kind, bid=99, ask=100),
        market(1, market_type=kind, exchange="hyperliquid", raw_symbol="xyz:BTC", dex="xyz",
               bid=102, ask=103),
        market(2, market_type=kind, exchange="hyperliquid", raw_symbol="io:BTC", dex="io",
               bid=102, ask=103),
        market(3, market_type=kind, exchange="hyperliquid", raw_symbol="BTC", bid=102, ask=103),
    ]
    for ordered in permutations(markets):
        assert_equivalent(list(ordered), mode)


@pytest.mark.parametrize("mode", ["SF", "FF", "SS"])
def test_empty_singleton_and_mode_without_candidates(mode):
    for markets in [[], [market(0)], [market(0), market(0).model_copy(deep=True)]]:
        assert_equivalent(markets, mode)
