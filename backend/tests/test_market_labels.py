from app.models.market import MarketType
from app.services.market_labels import (
    astro_exchange_id,
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
