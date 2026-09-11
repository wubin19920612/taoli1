from app.models.market import MarketType
from app.services.market_labels import (
    astro_exchange_id,
    astro_exchange_route_variants,
    is_bitget_rtoken_spot,
    market_leg_label,
    market_type_label,
)


def test_market_labels_use_chinese_market_names() -> None:
    assert market_type_label("binance", MarketType.FUTURE) == "合约"
    assert market_type_label("okx", MarketType.SPOT) == "现货"
    assert market_leg_label("binance", MarketType.FUTURE) == "binance 合约"


def test_bitget_rtoken_is_labeled_as_stock_spot() -> None:
    assert is_bitget_rtoken_spot("Bitget", MarketType.SPOT, "RSOXLUSDT", "SOXLUSDT")
    assert market_type_label("bitget", MarketType.SPOT, "RSOXLUSDT", "SOXLUSDT") == "股票现货"
    assert market_leg_label("bitget", MarketType.SPOT, "RSOXLUSDT", "SOXLUSDT") == "bitget 股票现货"
    assert astro_exchange_id("bitget", MarketType.SPOT, "RSOXLUSDT", "SOXLUSDT") == "bitgetr"
    assert astro_exchange_id("bitget", MarketType.SPOT, "BTCUSDT", "BTCUSDT") == "bitget"
    assert not is_bitget_rtoken_spot("bitget", MarketType.SPOT, "RUSDT", "USDT")
    assert not is_bitget_rtoken_spot("bitget", MarketType.FUTURE, "RSOXLUSDT", "SOXLUSDT")


def test_hyperliquid_uses_astro_hl_exchange_id() -> None:
    assert astro_exchange_id("hyperliquid", MarketType.FUTURE) == "hl"
    assert astro_exchange_id("hyper", MarketType.FUTURE) == "hl"
    assert astro_exchange_id("hl", MarketType.FUTURE) == "hl"


def test_supported_route_gets_matching_gc_variant() -> None:
    assert astro_exchange_route_variants("hl", "binance") == [
        ("hl", "binance"),
        ("gc-hl", "gc-binance"),
    ]
    assert astro_exchange_route_variants("gate", "bybit") == [
        ("gate", "bybit"),
        ("gc-gate", "gc-bybit"),
    ]


def test_bitget_keeps_its_id_while_other_leg_gets_gc_variant() -> None:
    assert astro_exchange_route_variants("bitget", "binance") == [
        ("bitget", "binance"),
        ("bitget", "gc-binance"),
    ]
    assert astro_exchange_route_variants("okx", "bitget") == [
        ("okx", "bitget"),
        ("gc-okx", "bitget"),
    ]
    assert astro_exchange_route_variants("bitgetr", "okx") == [
        ("bitgetr", "okx"),
        ("bitgetr", "gc-okx"),
    ]


def test_unsupported_exchange_never_creates_single_sided_gc_route() -> None:
    assert astro_exchange_route_variants("gate", "aster") == [("gate", "aster")]
    assert astro_exchange_route_variants("aster", "bybit") == [("aster", "bybit")]
    assert astro_exchange_route_variants("aster", "htx") == [("aster", "htx")]
