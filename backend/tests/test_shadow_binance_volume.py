import pytest

from app.exchanges.binance import BinanceAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "failed", "missing-symbol", "invalid-volume", "wrong-shape"])
async def test_binance_volume_absence_paths_are_metadata_only(mode):
    adapter = BinanceAdapter()
    async def fake_get(url):
        if url.endswith("bookTicker"):
            return [{"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "101"}]
        if url.endswith("premiumIndex"):
            return [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}]
        if url.endswith("fundingInfo"):
            return []
        if url.endswith("exchangeInfo"):
            return {"symbols": []}
        if mode == "failed":
            raise RuntimeError("synthetic metadata timeout")
        if mode == "wrong-shape":
            return {"code": -1}
        return [{"symbol": "OTHERUSDT" if mode == "missing-symbol" else "BTCUSDT",
                 "quoteVolume": "bad" if mode == "invalid-volume" else "1234567"}]
    adapter.get_json = fake_get
    try:
        rows = await adapter.fetch_future_tickers()
        assert len(rows) == 1 and rows[0].bid == 100
        assert rows[0].volume_24h_usdt == (1234567 if mode == "ok" else None)
        assert (await adapter.fetch_spot_tickers())[0].volume_24h_usdt is None
    finally:
        await adapter.client.aclose()
