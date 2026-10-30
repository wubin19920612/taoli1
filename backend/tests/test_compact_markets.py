"""Offline round-trip, ownership and retained-allocation gates for M2-4a."""

import gc
import tracemalloc
from datetime import UTC, datetime, timedelta
from time import perf_counter

import pytest

from app.models.market import MarketSnapshot, MarketType
from app.services import compact_markets
from app.services.compact_markets import CompactMarkets, pack

NOW = datetime(2026, 10, 28, tzinfo=UTC)


def full_market(index=0):
    return MarketSnapshot(
        symbol=f"ASSET{index}USDT", base=f"ASSET{index}", quote="USDT",
        exchange="hyperliquid", market_type=MarketType.FUTURE,
        bid=100.0 + index, ask=101.0 + index, bid_size=20.0, ask_size=30.0,
        volume_24h_usdt=2_000_000.0 + index, funding_rate_pct=0.01,
        funding_next_rate_pct=-0.02, funding_interval_hours=8,
        funding_next_time=NOW + timedelta(hours=8), mark_price=100.5 + index,
        index_price=100.4 + index, timestamp=NOW, raw_symbol=f"io:ASSET{index}",
        dex="io", contract_size_multiplier=1000.0, data_source="offline fixture",
        upstream_timestamp=NOW, is_estimated=True, estimated_fields=["bid", "ask"],
        symbol_alias_original_symbol=f"OLD{index}USDT", symbol_alias_price_multiplier=10.0,
        asset_id=f"asset-{index}", asset_name=f"Asset {index}", asset_kind="crypto",
        identity_status="verified", unit_status="verified", identity_checked=True,
        identity_collision=False, identity_enforced=True, binding_revision=7,
        binding_valid_until=NOW + timedelta(days=1), binding_fingerprint=f"binding-{index}",
        quote_kind="reference", original_quote_currency="USDC", contract_kind="linear",
        tracking_definition="underlying", quote_conversion_rate=1.001,
        quote_conversion_bid=1.0, quote_conversion_ask=1.002,
        quote_conversion_source="offline FX", quote_conversion_valid_until=NOW,
        reference_price=100.5 + index, book_timestamp=NOW, quote_time_source="upstream",
    )


def minimal_market(**updates):
    return MarketSnapshot(**{
        "symbol": "BTCUSDT", "base": "BTC", "exchange": "binance",
        "market_type": MarketType.SPOT, "bid": 100.0, "ask": 101.0,
        "timestamp": NOW, "raw_symbol": "BTCUSDT", **updates,
    })


@pytest.mark.parametrize("force_fallback", [False, True])
def test_round_trip_preserves_all_fields_types_and_explicit_defaults(monkeypatch, force_fallback):
    if force_fallback:
        monkeypatch.setattr(compact_markets, "_FAST", False)
    full = full_market()
    assert full.model_fields_set == set(MarketSnapshot.model_fields)
    rows = [full, minimal_market(), minimal_market(dex=None, estimated_fields=[], quote="USDT")]
    compact = pack(rows)
    assert isinstance(compact, CompactMarkets)
    assert len(compact) == len(rows)
    for original, restored in zip(rows, compact.unpack(), strict=True):
        assert restored is not original
        assert restored.model_dump() == original.model_dump()
        assert restored.model_dump(exclude_unset=True) == original.model_dump(exclude_unset=True)
        assert restored.model_fields_set == original.model_fields_set
        for name in MarketSnapshot.model_fields:
            assert type(getattr(restored, name)) is type(getattr(original, name))


def test_fast_unpack_matches_model_construct(monkeypatch):
    rows = [full_market(), minimal_market(), minimal_market(dex=None, estimated_fields=[], quote="USDT")]
    compact = pack(rows)
    monkeypatch.setattr(compact_markets, "_FAST", False)
    standard = compact.unpack()
    monkeypatch.setattr(compact_markets, "_FAST", True)
    fast = compact.unpack()
    for original, actual, expected in zip(rows, fast, standard, strict=True):
        assert type(actual) is type(expected) is type(original) is MarketSnapshot
        assert actual.model_dump() == expected.model_dump() == original.model_dump()
        assert actual.model_fields_set == expected.model_fields_set == original.model_fields_set
        assert actual == expected == original
        assert actual.model_fields_set is not expected.model_fields_set
        for name in MarketSnapshot.model_fields:
            assert type(getattr(actual, name)) is type(getattr(expected, name))


def test_pack_and_each_unpack_own_their_mutable_values_and_fields_set():
    source = full_market()
    compact = pack([source, source])
    expected = source.model_dump()
    expected_fields = source.model_fields_set.copy()
    source.estimated_fields.append("source mutation")
    source.model_fields_set.clear()
    first = compact.unpack()
    first[0].estimated_fields.append("unpacked mutation")
    first[0].model_fields_set.clear()
    assert first[1].model_dump() == expected
    assert first[1].model_fields_set == expected_fields
    for restored in compact.unpack():
        assert restored.model_dump() == expected
        assert restored.model_fields_set == expected_fields


def test_nested_mutable_values_remain_isolated():
    # Trusted model copies may contain richer containers than validated list[str].
    source = full_market().model_copy(update={"estimated_fields": [{"nested": ["bid"]}]})
    compact = pack([source, source])
    source.estimated_fields[0]["nested"].append("source mutation")
    first = compact.unpack()
    first[0].estimated_fields[0]["nested"].append("unpacked mutation")
    assert first[1].estimated_fields == [{"nested": ["bid"]}]
    assert all(row.estimated_fields == [{"nested": ["bid"]}] for row in compact.unpack())


def test_empty_batch():
    compact = pack([])
    assert len(compact) == 0
    assert compact.unpack() == []
    assert compact.unpack() is not compact.unpack()


def test_12000_rows_retained_memory_and_unpack_time(capsys):
    # Measure the SAME graph before and after releasing the original models.
    # Leaf values retained by compact storage remain charged to both measurements.
    gc.collect()
    tracemalloc.start()
    try:
        baseline = tracemalloc.get_traced_memory()[0]
        rows = [full_market(i) if i % 4 == 0 else minimal_market(
            symbol=f"ASSET{i}USDT", base=f"ASSET{i}", raw_symbol=f"ASSET{i}USDT",
            bid=100.0 + i, ask=101.0 + i, volume_24h_usdt=2_000_000.0 + i,
            market_type=MarketType.FUTURE if i % 2 else MarketType.SPOT,
        ) for i in range(12_000)]
        gc.collect()
        original_bytes = tracemalloc.get_traced_memory()[0] - baseline
        compact = pack(rows)
        del rows
        gc.collect()
        compact_bytes = tracemalloc.get_traced_memory()[0] - baseline
    finally:
        tracemalloc.stop()
    reduction = 1 - compact_bytes / original_bytes
    started = perf_counter()
    restored = compact.unpack()
    elapsed_ms = (perf_counter() - started) * 1000
    with capsys.disabled():
        print(f"\nM2-4a retained: original={original_bytes} bytes, compact={compact_bytes} bytes, "
              f"reduction={reduction:.2%}")
        print(f"M2-4a unpack 12000 rows: {elapsed_ms:.3f} ms (budget 150 ms)")
    assert len(restored) == 12_000
    assert reduction >= 0.60
    # The specification requires reporting and a batching proposal if >150 ms,
    # rather than a flaky timing assertion or changing the storage design.
