import pytest

from app.exchanges.bitget import BitgetAdapter, rtoken_spot_symbols
from app.models.market import MarketType


def test_rtoken_spot_symbols_only_accept_verified_bitget_stock_products() -> None:
    products = [
        {"symbol": "RAAPLUSDT", "baseCoin": "rAAPL", "areaSymbol": "yes"},
        {"symbol": "RWAUSDT", "baseCoin": "RWA", "areaSymbol": "no"},
        {"symbol": "RANDOMUSDT", "baseCoin": "RANDOM", "areaSymbol": "yes"},
        {"symbol": "RAAPLUSDT", "baseCoin": "rAAPL", "areaSymbol": "no"},
        {"symbol": "RBTCUSDC", "baseCoin": "rBTC", "areaSymbol": "yes"},
    ]

    assert rtoken_spot_symbols(products) == {"RAAPLUSDT"}


@pytest.mark.asyncio
async def test_bitget_spot_parser_maps_verified_rtoken_to_its_perpetual_symbol() -> None:
    adapter = BitgetAdapter()
    try:
        rows = adapter._parse(
            [
                {
                    "symbol": "RAAPLUSDT",
                    "bidPr": "311.18",
                    "askPr": "311.29",
                    "usdtVolume": "1000000",
                },
                {
                    "symbol": "RWAUSDT",
                    "bidPr": "1.18",
                    "askPr": "1.19",
                    "usdtVolume": "1000000",
                },
            ],
            MarketType.SPOT,
            rtoken_symbols={"RAAPLUSDT"},
        )
    finally:
        # This unit test only exercises parsing, so close the adapter's real client.
        await adapter.client.aclose()

    stock, unrelated = rows
    assert stock.symbol == "AAPLUSDT"
    assert stock.base == "AAPL"
    assert stock.raw_symbol == "RAAPLUSDT"
    assert stock.symbol_alias_original_symbol == "RAAPLUSDT"
    assert unrelated.symbol == "RWAUSDT"


@pytest.mark.asyncio
async def test_bitget_future_parser_does_not_remap_r_prefixed_symbols() -> None:
    adapter = BitgetAdapter()
    try:
        rows = adapter._parse(
            [{"symbol": "RAAPLUSDT", "bidPr": "311.18", "askPr": "311.29"}],
            MarketType.FUTURE,
            rtoken_symbols={"RAAPLUSDT"},
        )
    finally:
        await adapter.client.aclose()

    assert rows[0].symbol == "RAAPLUSDT"
    assert rows[0].raw_symbol == "RAAPLUSDT"
