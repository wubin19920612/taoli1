import pytest

from app.exchanges.base import ExchangeAdapter
from app.exchanges.okx import OKXAdapter


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self):
        self.urls: list[str] = []

    async def get(self, url: str):
        self.urls.append(url)
        if "market/tickers?instType=SWAP" in url:
            return FakeResponse(
                {
                    "data": [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "bidPx": "100",
                            "askPx": "101",
                            "bidSz": "1",
                            "askSz": "1",
                            "volCcy24h": "1000000",
                        }
                    ]
                }
            )
        if "funding-rate?instId=BTC-USDT-SWAP" in url:
            return FakeResponse({"data": [{"fundingRate": "0.0001"}]})
        raise AssertionError(f"unexpected url: {url}")


@pytest.mark.asyncio
async def test_okx_fetches_funding_per_swap_symbol() -> None:
    client = FakeClient()
    adapter = OKXAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    assert rows[0].symbol == "BTCUSDT"
    assert rows[0].funding_rate_pct == 0.01
    assert any("funding-rate?instId=BTC-USDT-SWAP" in url for url in client.urls)
    assert not any("funding-rate?instType=SWAP" in url for url in client.urls)


def test_exchange_adapter_uses_short_timeout_and_headers() -> None:
    adapter = OKXAdapter()

    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.client.timeout.connect <= 3
    assert adapter.client.headers["User-Agent"].startswith("taoli1-radar")
