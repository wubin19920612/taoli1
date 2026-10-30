"""Offline key-encoding compatibility, cache bounds, retained memory and timing checks."""

import gc
import json
import tracemalloc
from time import perf_counter
from types import SimpleNamespace

import pytest

from app.models import market_identity
from app.models.market import MarketType
from app.models.market_identity import MarketKey


# Preserve the key implementation from 8779d40 for byte-for-byte comparisons.
def legacy_encode_key(market):
    return json.dumps(
        [market.exchange, market.market_type, market.dex, market.raw_symbol],
        separators=(",", ":"),
    )


def snapshot(exchange="hyperliquid", market_type="future", dex=None, raw_symbol="BTC"):
    return SimpleNamespace(
        exchange=exchange, market_type=market_type, dex=dex, raw_symbol=raw_symbol
    )


@pytest.fixture(autouse=True)
def isolate_encoding_cache():
    market_identity._encode_key.cache_clear()
    yield
    market_identity._encode_key.cache_clear()


VALID_IDENTITIES = [
    ("hyperliquid", "future", None, "BTC"),
    ("hyperliquid", "future", "main", "BTC"),
    ("hyperliquid", "future", None, "xyz:ABC"),
    ("hyperliquid", "future", "xyz", "xyz:ABC"),
    ("hyperliquid", "future", "io", "io:ABC"),
    ("hyperliquid", "future", None, "XYZ:AbC:USD"),
    (" HYPERLIQUID ", "future", " XYZ ", "  xyz:AbC  "),
    ("hyperliquid", "spot", None, "@123"),
    ("bybit", "spot", None, "ABC:USDT"),
    ("bybit", "future", None, "ABC:USDT"),
    (" BYBIT ", "future", None, "  AbC/USDT:USDT  "),
    ("交易所", "future", None, "资产/美元☃"),
    ("hyperliquid", "future", None, "xyz:资产🚀"),
    ("bybit", "spot", None, 'A"B\\C\nD\tE'),
]


@pytest.mark.parametrize("identity", VALID_IDENTITIES)
@pytest.mark.parametrize("use_enum", [False, True], ids=["string", "enum"])
def test_encoding_matches_legacy_byte_for_byte(identity, use_enum):
    row = snapshot(*identity)
    if use_enum:
        row.market_type = MarketType(row.market_type)
    # Exercise the encoder with verbatim inputs, including whitespace and dex=None.
    for _ in range(2):
        encoded = market_identity._encode_key(
            row.exchange, row.market_type, row.dex, row.raw_symbol
        )
        assert encoded.encode("utf-8") == legacy_encode_key(row).encode("utf-8")
    # The property must keep using the model's existing normalization rules.
    market = MarketKey(**vars(row))
    for _ in range(2):
        assert market.key.encode("utf-8") == legacy_encode_key(market).encode("utf-8")


def test_same_identity_reuses_encoded_string():
    first = MarketKey(**vars(snapshot()))
    second = MarketKey(**vars(snapshot()))
    first_encoding = first.key
    assert first_encoding is second.key
    assert first_encoding is first.key
    info = market_identity._encode_key.cache_info()
    assert info.misses == 1
    assert info.hits == 2
    assert info.currsize == 1


def test_each_identity_component_remains_part_of_the_encoding_cache_key():
    rows = [snapshot(*identity) for identity in VALID_IDENTITIES]
    rows.extend(
        [
            snapshot(raw_symbol="btc"),
            snapshot(exchange="bybit"),
            snapshot(market_type="spot"),
            snapshot(dex="main"),
        ]
    )
    encoded = [
        market_identity._encode_key(row.exchange, row.market_type, row.dex, row.raw_symbol)
        for row in rows
    ]
    for row, key in zip(rows, encoded, strict=True):
        assert key == legacy_encode_key(row)
        assert (
            market_identity._encode_key(row.exchange, row.market_type, row.dex, row.raw_symbol)
            is key
        )
    for left, left_key in zip(rows, encoded, strict=True):
        for right, right_key in zip(rows, encoded, strict=True):
            if vars(left) != vars(right):
                assert left_key != right_key


def test_encoding_lru_cache_is_bounded_and_evicts_oldest_identity():
    encode = market_identity._encode_key
    assert market_identity.MARKET_KEY_CACHE_SIZE == 32768
    assert encode.cache_parameters() == {"maxsize": 32768, "typed": False}
    first = MarketKey(**vars(snapshot(raw_symbol="xyz:0")))
    first_encoding = first.key
    for i in range(1, 32769):
        market = MarketKey(**vars(snapshot(raw_symbol=f"xyz:{i}")))
        assert market.key == legacy_encode_key(market)
    assert encode.cache_info().currsize == 32768
    assert first.key == first_encoding
    assert encode.cache_info().misses == 32770


def test_retained_memory_for_12400_encoded_identities(capsys):
    # Prebuild models before tracing: v2 measures only the encoding cache's additions.
    markets = [MarketKey(**vars(snapshot(raw_symbol=f"xyz:ASSET{i}"))) for i in range(12400)]
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for market in markets:
            _ = market.key
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] - before
        with capsys.disabled():
            print(
                f"MarketKey encoding cache retained memory (12400 identities): "
                f"{retained} bytes ({retained / 1_000_000:.3f} MB); limit=4000000 bytes"
            )
        assert market_identity._encode_key.cache_info().currsize == 12400
        assert retained <= 4_000_000
    finally:
        tracemalloc.stop()


def read_keys(rows, encode):
    total = 0
    for row in rows:
        market = MarketKey.from_snapshot(row)
        for _ in range(4):
            total += len(encode(market))
    return total


def test_microbenchmark_12400_rows_with_four_key_reads(capsys):
    rows = [snapshot(raw_symbol=f"xyz:ASSET{i}") for i in range(12400)]
    start = perf_counter()
    expected = read_keys(rows, legacy_encode_key)
    legacy_seconds = perf_counter() - start
    start = perf_counter()
    actual = read_keys(rows, lambda market: market.key)
    cold_seconds = perf_counter() - start
    start = perf_counter()
    warm = read_keys(rows, lambda market: market.key)
    warm_seconds = perf_counter() - start
    assert actual == warm == expected
    with capsys.disabled():
        print(
            f"MarketKey microbenchmark (12400 rows x 4 key reads): "
            f"legacy={legacy_seconds:.6f}s, cached_cold={cold_seconds:.6f}s, "
            f"cached_warm={warm_seconds:.6f}s"
        )
