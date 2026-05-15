from datetime import UTC, datetime

from app.exchanges.base import normalize_usdt_symbol, parse_float
from app.models.market import MarketSnapshot, MarketType


def test_normalize_usdt_symbol_handles_common_formats() -> None:
    assert normalize_usdt_symbol("BTCUSDT") == ("BTCUSDT", "BTC", "USDT")
    assert normalize_usdt_symbol("BTC-USDT") == ("BTCUSDT", "BTC", "USDT")
    assert normalize_usdt_symbol("BTC-USDT-SWAP") == ("BTCUSDT", "BTC", "USDT")
    assert normalize_usdt_symbol("btcusdt") == ("BTCUSDT", "BTC", "USDT")


def test_parse_float_handles_missing_values() -> None:
    assert parse_float("1.23") == 1.23
    assert parse_float("") is None
    assert parse_float(None) is None


def test_market_snapshot_accepts_normalized_values() -> None:
    symbol, base, quote = normalize_usdt_symbol("ETH-USDT-SWAP")
    snapshot = MarketSnapshot(
        symbol=symbol,
        base=base,
        quote=quote,
        exchange="okx",
        market_type=MarketType.FUTURE,
        bid=100,
        ask=101,
        timestamp=datetime.now(UTC),
        raw_symbol="ETH-USDT-SWAP",
    )

    assert snapshot.symbol == "ETHUSDT"
