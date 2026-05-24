from datetime import UTC

import pytest

from app.exchanges.aster import AsterAdapter
from app.exchanges.binance import BinanceAdapter
from app.exchanges.bitget import BitgetAdapter
from app.exchanges.bybit import BybitAdapter
from app.exchanges.gate import GateAdapter
from app.exchanges.htx import HTXAdapter
from app.exchanges.hyperliquid import HyperliquidAdapter
from app.exchanges.okx import OKXAdapter
from app.models.market import MarketType


@pytest.mark.asyncio
async def test_binance_fetches_future_order_book(monkeypatch) -> None:
    urls: list[str] = []

    async def fake_get_json(self, url: str):
        urls.append(url)
        return {"bids": [["101", "2"]], "asks": [["100", "3"]]}

    monkeypatch.setattr(BinanceAdapter, "get_json", fake_get_json)

    book = await BinanceAdapter().fetch_order_book("BTCUSDT", MarketType.FUTURE, "BTCUSDT", limit=20)

    assert urls == ["https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=20"]
    assert book is not None
    assert book.bids[0].price == 101
    assert book.bids[0].size == 2
    assert book.asks[0].price == 100
    assert book.raw_symbol == "BTCUSDT"


@pytest.mark.asyncio
async def test_okx_fetches_swap_order_book(monkeypatch) -> None:
    urls: list[str] = []

    async def fake_get_json(self, url: str):
        urls.append(url)
        return {
            "data": [
                {
                    "bids": [["101", "2", "0", "1"]],
                    "asks": [["100", "3", "0", "1"]],
                    "ts": "1779192000000",
                }
            ]
        }

    monkeypatch.setattr(OKXAdapter, "get_json", fake_get_json)

    book = await OKXAdapter().fetch_order_book("BTCUSDT", MarketType.FUTURE, "BTCUSDT", limit=20)

    assert urls == ["https://www.okx.com/api/v5/market/books?instId=BTC-USDT-SWAP&sz=20"]
    assert book is not None
    assert book.raw_symbol == "BTC-USDT-SWAP"
    assert book.timestamp.tzinfo == UTC
    assert book.bids[0].price == 101
    assert book.asks[0].size == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_cls", "market_type", "expected_url", "payload", "expected_raw"),
    [
        (
            BybitAdapter,
            MarketType.FUTURE,
            "https://api.bybit.com/v5/market/orderbook?category=linear&symbol=BTCUSDT&limit=20",
            {"result": {"b": [["101", "2"]], "a": [["100", "3"]]}},
            "BTCUSDT",
        ),
        (
            GateAdapter,
            MarketType.FUTURE,
            "https://api.gateio.ws/api/v4/futures/usdt/order_book?contract=BTC_USDT&limit=20",
            {"bids": [{"p": "101", "s": "2"}], "asks": [{"p": "100", "s": "3"}]},
            "BTC_USDT",
        ),
        (
            BitgetAdapter,
            MarketType.FUTURE,
            "https://api.bitget.com/api/v2/mix/market/orderbook?symbol=BTCUSDT&productType=USDT-FUTURES&limit=20",
            {"data": {"bids": [["101", "2"]], "asks": [["100", "3"]]}},
            "BTCUSDT",
        ),
        (
            HTXAdapter,
            MarketType.FUTURE,
            "https://api.hbdm.com/linear-swap-ex/market/depth?contract_code=BTC-USDT&type=step0&depth=20",
            {"tick": {"bids": [[101, 2]], "asks": [[100, 3]], "ts": 1779192000000}},
            "BTC-USDT",
        ),
        (
            AsterAdapter,
            MarketType.FUTURE,
            "https://fapi.asterdex.com/fapi/v1/depth?symbol=BTCUSDT&limit=20",
            {"bids": [["101", "2"]], "asks": [["100", "3"]]},
            "BTCUSDT",
        ),
    ],
)
async def test_common_adapters_fetch_order_books(
    adapter_cls,
    market_type: MarketType,
    expected_url: str,
    payload: dict,
    expected_raw: str,
    monkeypatch,
) -> None:
    urls: list[str] = []

    async def fake_get_json(self, url: str):
        urls.append(url)
        return payload

    monkeypatch.setattr(adapter_cls, "get_json", fake_get_json)

    book = await adapter_cls().fetch_order_book("BTCUSDT", market_type, "BTCUSDT", limit=20)

    assert urls == [expected_url]
    assert book is not None
    assert book.raw_symbol == expected_raw
    assert book.bids[0].price == 101
    assert book.bids[0].size == 2
    assert book.asks[0].price == 100
    assert book.asks[0].size == 3


@pytest.mark.asyncio
async def test_hyperliquid_fetches_future_l2_book() -> None:
    class FakePostClient:
        def __init__(self):
            self.posts: list[tuple[str, dict]] = []

        async def post(self, url: str, json: dict):
            self.posts.append((url, json))

            class Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "time": 1779192000000,
                        "levels": [
                            [{"px": "101", "sz": "2"}],
                            [{"px": "100", "sz": "3"}],
                        ],
                    }

                async def aclose(self):
                    return None

            return Response()

    client = FakePostClient()
    adapter = HyperliquidAdapter(client=client)

    book = await adapter.fetch_order_book("BTCUSDT", MarketType.FUTURE, "BTCUSDT", limit=20)

    assert client.posts == [("https://api.hyperliquid.xyz/info", {"type": "l2Book", "coin": "BTC"})]
    assert book is not None
    assert book.raw_symbol == "BTC"
    assert book.bids[0].price == 101
    assert book.asks[0].size == 3
