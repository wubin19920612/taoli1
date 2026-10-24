from datetime import UTC, datetime, timedelta
from random import Random

import pytest
from spread_engine_reference import build_opportunities as original_build

from app.models.market import MarketSnapshot, MarketType
from app.models.settings import FeeSettings, RiskSettings
from app.services.collector import MarketCollector
from app.services.risk_labels import apply_risk_labels, risk_labels_for
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_all_opportunities

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def mixed_markets(seed):
    rng = Random(seed)
    markets = []
    for i in range(48):
        symbol = rng.choice(["BTCUSDT", "AAPLUSDT", "AIUSDT"])
        kind = rng.choice(list(MarketType))
        exchange = rng.choice(["binance", "hyperliquid", "bitget", "lighter", "rh-lighter"])
        dex = rng.choice([None, "xyz", "io"]) if exchange == "hyperliquid" else None
        raw = f"{dex}:{symbol}" if dex else symbol
        if exchange == "bitget" and kind == MarketType.SPOT and symbol == "AAPLUSDT":
            raw = "RAAPLUSDT"
        market = MarketSnapshot(
            symbol=symbol, base=symbol.removesuffix("USDT"), raw_symbol=raw,
            exchange=exchange, market_type=kind, dex=dex,
            bid=rng.choice([98, 100, 102]), ask=rng.choice([99, 101, 103]),
            timestamp=NOW - timedelta(seconds=rng.choice([0, 29, 30, 31])),
            upstream_timestamp=rng.choice([None, NOW - timedelta(seconds=35)]),
            estimated_fields=rng.choice([[], [], ["bid"], ["volume_24h_usdt"]]),
            contract_size_multiplier=rng.choice([None, 1, 1000]),
            symbol_alias_price_multiplier=rng.choice([1, 0.001]),
            funding_rate_pct=rng.choice([None, -0.02, 0.03]),
            funding_next_rate_pct=rng.choice([None, 0.01]),
            funding_interval_hours=rng.choice([None, 1, 4, 8]),
            volume_24h_usdt=rng.choice([None, 0, 100, 1_000_000]),
            mark_price=102, index_price=100, bid_size=10, ask_size=20,
        )
        markets.append(market)
        if i % 12 == 0:
            markets.extend([market.model_copy(deep=True), market.model_copy(update={"bid": 106.0})])
    rng.shuffle(markets)
    return markets


def original_pipeline(markets, risk, fees, now):
    raw = []
    for mode in ("SF", "FF", "SS"):
        raw.extend(original_build(
            markets, mode,
            buy_fee_pct=fees.spot_fee_pct if mode in {"SF", "SS"} else fees.future_fee_pct,
            sell_fee_pct=fees.future_fee_pct if mode in {"SF", "FF"} else fees.spot_fee_pct,
            safety_slippage_pct=fees.safety_slippage_pct,
            now=now, stale_after_seconds=risk.stale_after_seconds,
        ))
    labeled = [apply_risk_labels(item, risk, now) for item in raw]
    return sorted(labeled, key=lambda item: item.open_spread_pct, reverse=True)


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("offset", [0, 1, 24 * 60 * 60])
def test_complete_pipeline_matches_original_fields_order_and_input_ownership(seed, offset):
    markets = mixed_markets(seed)
    before = [item.model_dump() for item in markets]
    risk = RiskSettings(ticker_collision_symbols=["aiusdt", "AAPLUSDT"])
    fees = FeeSettings(spot_fee_pct=0.13, future_fee_pct=0.04, safety_slippage_pct=0.06)
    current = NOW + timedelta(seconds=offset)
    collector = MarketCollector([], SnapshotStore(), risk_settings=risk, fee_settings=fees)
    actual = collector._build_labeled_opportunities(markets, now=current)
    expected = original_pipeline(markets, risk, fees, current)
    assert [item.model_dump() for item in actual] == [item.model_dump() for item in expected]
    assert [item.model_dump() for item in markets] == before
    assert len({id(item.risk_labels) for item in actual}) == len(actual)


def test_public_label_functions_leave_inputs_untouched_and_returns_are_independent():
    item = build_all_opportunities(mixed_markets(3), now=NOW)[0]
    before = item.model_dump()
    labels = risk_labels_for(item, RiskSettings(), NOW)
    labeled = apply_risk_labels(item, RiskSettings(), NOW)
    assert item.model_dump() == before
    assert labeled is not item
    assert labeled.risk_labels == labels
    assert labeled.risk_labels is not labels
    labels.append("caller-only")
    assert "caller-only" not in labeled.risk_labels
    assert "caller-only" not in item.risk_labels


def test_next_collection_reloads_mutated_settings_and_preserves_published_models():
    markets = mixed_markets(5)
    risk = RiskSettings(ticker_collision_symbols=[])
    fees = FeeSettings()
    store = SnapshotStore()
    collector = MarketCollector([], store, risk_settings=risk, fee_settings=fees)
    first = collector._build_labeled_opportunities(markets, now=NOW)
    assert first
    store.set_opportunities(first)
    before = [item.model_dump() for item in first]
    risk.ticker_collision_symbols.extend(["btcusdt", "aaplusdt", "aiusdt"])
    fees.spot_fee_pct = 0.23
    fees.future_fee_pct = 0.17
    second = collector._build_labeled_opportunities(markets, now=NOW)
    expected = original_pipeline(markets, risk, fees, NOW)
    assert [item.model_dump() for item in second] == [item.model_dump() for item in expected]
    assert all("SAME_TICKER_RISK" in item.risk_labels for item in second)
    assert [item.model_dump() for item in store.get_opportunities()] == before
    assert not ({id(item) for item in first} & {id(item) for item in second})


def test_batch_empty_and_unpaired_markets_return_no_opportunities():
    assert build_all_opportunities([], now=NOW) == []
    assert build_all_opportunities(mixed_markets(0)[:1], now=NOW) == []
