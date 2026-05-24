import httpx
import pytest

from app.exchanges.aster import AsterAdapter
from app.exchanges.base import ExchangeAdapter
from app.exchanges.binance import BinanceAdapter
from app.exchanges.bitget import BitgetAdapter
from app.exchanges.bybit import BybitAdapter
from app.exchanges.gate import GateAdapter
from app.exchanges.hyperliquid import HyperliquidAdapter
from app.exchanges.htx import HTXAdapter
from app.exchanges.okx import OKXAdapter
from app.models.market import MarketType


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

    async def aclose(self):
        return None


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
        if "funding-rate?instId=ANY" in url:
            return FakeResponse(
                {
                    "data": [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "fundingRate": "0.0001",
                            "nextFundingRate": "0.0002",
                            "fundingTime": "1779192000000",
                            "nextFundingTime": "1779206400000",
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected url: {url}")


class FundingIntervalClient:
    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.urls: list[str] = []

    async def get(self, url: str):
        self.urls.append(url)
        for fragment, payload in self.responses.items():
            if fragment in url:
                return FakeResponse(payload)
        raise AssertionError(f"unexpected url: {url}")


class FailingFragmentClient(FundingIntervalClient):
    def __init__(self, responses: dict[str, object], failing_fragment: str):
        super().__init__(responses)
        self.failing_fragment = failing_fragment

    async def get(self, url: str):
        if self.failing_fragment in url:
            self.urls.append(url)
            raise httpx.TimeoutException("funding interval timeout")
        return await super().get(url)


class FailingFundingClient(FakeClient):
    async def get(self, url: str):
        self.urls.append(url)
        if "funding-rate?instId=ANY" in url:
            raise httpx.TimeoutException("funding timeout")
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
        raise AssertionError(f"unexpected url: {url}")


class ClosingResponse:
    def __init__(self, payload=None, exc: Exception | None = None):
        self.payload = payload
        self.exc = exc
        self.closed = False

    def raise_for_status(self):
        if self.exc is not None:
            raise self.exc

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    async def aclose(self):
        self.closed = True


class ClosingClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def get(self, url: str):
        self.calls += 1
        return self.responses.pop(0)

    async def post(self, url: str, json: dict):
        self.calls += 1
        return self.responses.pop(0)


class FakePostClient:
    def __init__(self, payloads: dict[object, object]):
        self.payloads = payloads
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url: str, json: dict):
        self.posts.append((url, json))
        key = (json["type"], json.get("dex"))
        if key in self.payloads:
            return FakeResponse(self.payloads[key])
        response_type = json["type"]
        if response_type in self.payloads:
            return FakeResponse(self.payloads[response_type])
        raise AssertionError(f"unexpected body: {json}")


class ConcurrencyPostClient:
    def __init__(self, payloads: dict[object, object]):
        self.payloads = payloads
        self.current = 0
        self.max_seen = 0

    async def post(self, url: str, json: dict):
        import asyncio

        self.current += 1
        self.max_seen = max(self.max_seen, self.current)
        try:
            await asyncio.sleep(0.01)
            key = (json["type"], json.get("dex"))
            if key in self.payloads:
                return FakeResponse(self.payloads[key])
            response_type = json["type"]
            if response_type in self.payloads:
                return FakeResponse(self.payloads[response_type])
            raise AssertionError(f"unexpected body: {json}")
        finally:
            self.current -= 1


@pytest.mark.asyncio
async def test_okx_fetches_all_funding_rates_in_one_request() -> None:
    client = FakeClient()
    adapter = OKXAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    assert rows[0].symbol == "BTCUSDT"
    assert rows[0].funding_rate_pct == 0.01
    assert rows[0].funding_next_rate_pct == 0.02
    assert rows[0].funding_interval_hours == 4
    assert any("funding-rate?instId=ANY" in url for url in client.urls)
    assert not any("funding-rate?instType=SWAP" in url for url in client.urls)
    assert not any("funding-rate?instId=BTC-USDT-SWAP" in url for url in client.urls)


@pytest.mark.asyncio
async def test_binance_uses_funding_info_interval_over_default() -> None:
    client = FundingIntervalClient(
        {
            "ticker/bookTicker": [
                {
                    "symbol": "LPTUSDT",
                    "bidPrice": "10",
                    "askPrice": "10.1",
                    "bidQty": "1",
                    "askQty": "1",
                }
            ],
            "premiumIndex": [
                {
                    "symbol": "LPTUSDT",
                    "lastFundingRate": "0.0001",
                    "nextFundingTime": "1779206400000",
                    "markPrice": "10",
                    "indexPrice": "10",
                }
            ],
            "fundingInfo": [
                {
                    "symbol": "LPTUSDT",
                    "fundingIntervalHours": 4,
                }
            ],
        }
    )
    adapter = BinanceAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    assert rows[0].funding_interval_hours == 4
    assert any("fundingInfo" in url for url in client.urls)


@pytest.mark.asyncio
async def test_aster_uses_funding_info_interval_over_default() -> None:
    client = FundingIntervalClient(
        {
            "ticker/bookTicker": [
                {
                    "symbol": "SBETUSDT",
                    "bidPrice": "1",
                    "askPrice": "1.01",
                    "bidQty": "1",
                    "askQty": "1",
                }
            ],
            "fundingInfo": [
                {
                    "symbol": "SBETUSDT",
                    "fundingIntervalHours": 4,
                }
            ],
        }
    )
    adapter = AsterAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    assert rows[0].funding_interval_hours == 4
    assert any("fundingInfo" in url for url in client.urls)


@pytest.mark.asyncio
async def test_aster_leaves_interval_unknown_when_funding_info_fails() -> None:
    client = FailingFragmentClient(
        {
            "ticker/bookTicker": [
                {
                    "symbol": "SBETUSDT",
                    "bidPrice": "1",
                    "askPrice": "1.01",
                    "bidQty": "1",
                    "askQty": "1",
                }
            ],
        },
        failing_fragment="fundingInfo",
    )
    adapter = AsterAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    assert rows[0].funding_interval_hours is None


@pytest.mark.asyncio
async def test_gate_uses_contract_funding_interval_when_ticker_omits_it() -> None:
    client = FundingIntervalClient(
        {
            "futures/usdt/tickers": [
                {
                    "contract": "2Z_USDT",
                    "highest_bid": "0.1",
                    "lowest_ask": "0.101",
                    "funding_rate": "-0.000154",
                    "funding_rate_indicative": "-0.0001",
                    "funding_next_apply": "1779364800",
                    "volume_24h_quote": "1000000",
                }
            ],
            "futures/usdt/contracts": [
                {
                    "name": "2Z_USDT",
                    "funding_interval": 14400,
                }
            ],
        }
    )
    adapter = GateAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    assert rows[0].funding_interval_hours == 4
    assert any("futures/usdt/contracts" in url for url in client.urls)


@pytest.mark.asyncio
async def test_htx_uses_contract_info_settlement_period_over_default() -> None:
    client = FundingIntervalClient(
        {
            "batch_merged": {
                "ticks": [
                    {
                        "contract_code": "MASK-USDT",
                        "tick": {
                            "bid": [1.0],
                            "ask": [1.01],
                            "amount": "1000000",
                        },
                    }
                ]
            },
            "swap_contract_info": {
                "status": "ok",
                "data": [
                    {
                        "contract_code": "MASK-USDT",
                        "settlement_period": "4",
                        "settlement_date": "1779364800000",
                    }
                ],
            },
        }
    )
    adapter = HTXAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    assert rows[0].funding_interval_hours == 4
    assert rows[0].funding_next_time is not None
    assert any("swap_contract_info" in url for url in client.urls)


@pytest.mark.asyncio
async def test_okx_keeps_future_tickers_when_funding_request_times_out() -> None:
    client = FailingFundingClient()
    adapter = OKXAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    assert rows[0].symbol == "BTCUSDT"
    assert rows[0].funding_rate_pct is None
    assert any("funding-rate?instId=ANY" in url for url in client.urls)


def test_exchange_adapter_uses_short_timeout_and_headers() -> None:
    adapter = OKXAdapter()

    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.client.timeout.connect <= 3
    assert adapter.client.timeout.pool >= 5
    assert adapter.client.headers["User-Agent"].startswith("taoli1-radar")


@pytest.mark.asyncio
async def test_exchange_adapter_closes_failed_responses_before_retrying() -> None:
    first = ClosingResponse(exc=httpx.TimeoutException("timeout"))
    second = ClosingResponse(payload={"ok": True})
    client = ClosingClient([first, second])
    adapter = OKXAdapter(client=client)

    payload = await adapter.get_json("https://example.com/test")

    assert payload == {"ok": True}
    assert first.closed is True
    assert second.closed is True


@pytest.mark.asyncio
async def test_hyperliquid_parses_perp_contexts_from_info_endpoint() -> None:
    client = FakePostClient(
        {
            ("perpDexs", None): [],
            ("metaAndAssetCtxs", None): [
                {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
                [
                    {
                        "midPx": "100.5",
                        "markPx": "100.7",
                        "oraclePx": "100.2",
                        "funding": "0.0000125",
                        "dayNtlVlm": "1234567.89",
                    },
                    {
                        "midPx": "200.1",
                        "markPx": "200.3",
                        "oraclePx": "199.8",
                        "funding": "-0.00001",
                        "dayNtlVlm": "987654.32",
                    },
                ],
            ]
        }
    )
    adapter = HyperliquidAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    assert [(url, body) for url, body in client.posts] == [
        ("https://api.hyperliquid.xyz/info", {"type": "perpDexs"}),
        ("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"}),
        ("https://api.hyperliquid.xyz/info", {"type": "predictedFundings"}),
    ]
    assert rows[0].symbol == "BTCUSDT"
    assert rows[0].exchange == "hyperliquid"
    assert rows[0].market_type == MarketType.FUTURE
    assert rows[0].bid == 100.5
    assert rows[0].ask == 100.5
    assert rows[0].mark_price == 100.7
    assert rows[0].index_price == 100.2
    assert rows[0].funding_rate_pct == 0.00125
    assert rows[0].funding_interval_hours == 1
    assert rows[0].volume_24h_usdt == 1234567.89


@pytest.mark.asyncio
async def test_hyperliquid_fetches_stock_perp_dexes_and_keeps_best_symbol() -> None:
    client = FakePostClient(
        {
            ("perpDexs", None): [
                None,
                {"name": "xyz", "fullName": "XYZ"},
                {"name": "cash", "fullName": "dreamcash"},
            ],
            ("metaAndAssetCtxs", None): [
                {"universe": [{"name": "BTC"}]},
                [
                    {
                        "midPx": "100.5",
                        "markPx": "100.7",
                        "oraclePx": "100.2",
                        "funding": "0.0000125",
                        "dayNtlVlm": "1234567.89",
                    }
                ],
            ],
            ("metaAndAssetCtxs", "xyz"): [
                {"universe": [{"name": "xyz:TSLA"}, {"name": "xyz:AAPL"}]},
                [
                    {
                        "midPx": "400.0",
                        "markPx": "401.0",
                        "oraclePx": "399.5",
                        "funding": "0.00000625",
                        "dayNtlVlm": "1000000",
                    },
                    {
                        "midPx": "200.0",
                        "markPx": "201.0",
                        "oraclePx": "199.5",
                        "funding": "0.00000625",
                        "dayNtlVlm": "2500000",
                    },
                ],
            ],
            ("metaAndAssetCtxs", "cash"): [
                {"universe": [{"name": "cash:TSLA"}]},
                [
                    {
                        "midPx": "405.0",
                        "markPx": "406.0",
                        "oraclePx": "404.5",
                        "funding": "0.0000057078",
                        "dayNtlVlm": "5000000",
                    }
                ],
            ],
        }
    )
    adapter = HyperliquidAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    assert [
        (url, body["type"], body.get("dex"))
        for url, body in client.posts
    ] == [
        ("https://api.hyperliquid.xyz/info", "perpDexs", None),
        ("https://api.hyperliquid.xyz/info", "metaAndAssetCtxs", None),
        ("https://api.hyperliquid.xyz/info", "predictedFundings", None),
        ("https://api.hyperliquid.xyz/info", "metaAndAssetCtxs", "xyz"),
        ("https://api.hyperliquid.xyz/info", "metaAndAssetCtxs", "cash"),
    ]
    assert {row.symbol for row in rows} == {"BTCUSDT", "TSLAUSDT", "AAPLUSDT"}
    tsla = next(row for row in rows if row.symbol == "TSLAUSDT")
    assert tsla.raw_symbol == "cash:TSLA"
    assert tsla.bid == 405.0
    assert tsla.ask == 405.0
    assert tsla.mark_price == 406.0
    assert tsla.volume_24h_usdt == 5_000_000
    assert sum(1 for row in rows if row.symbol == "TSLAUSDT") == 1


@pytest.mark.asyncio
async def test_hyperliquid_limits_perp_dex_request_concurrency() -> None:
    dex_names = [f"dex{i}" for i in range(10)]
    payloads: dict[object, object] = {
        ("perpDexs", None): [{"name": name} for name in dex_names],
        ("metaAndAssetCtxs", None): [{"universe": []}, []],
        ("predictedFundings", None): [],
    }
    for name in dex_names:
        payloads[("metaAndAssetCtxs", name)] = [{"universe": []}, []]
    client = ConcurrencyPostClient(payloads)
    adapter = HyperliquidAdapter(client=client)

    await adapter.fetch_future_tickers()

    assert client.max_seen <= adapter.perp_dex_concurrency


@pytest.mark.asyncio
async def test_hyperliquid_parses_canonical_spot_usdc_pairs_as_usdt_symbols() -> None:
    client = FakePostClient(
        {
            "spotMetaAndAssetCtxs": [
                {
                    "tokens": [
                        {"index": 0, "name": "USDC"},
                        {"index": 1, "name": "PURR"},
                        {"index": 150, "name": "HYPE"},
                        {"index": 151, "name": "BTC"},
                    ],
                    "universe": [
                        {"name": "PURR/USDC", "tokens": [1, 0], "isCanonical": True},
                        {"name": "@107", "tokens": [150, 0], "isCanonical": False},
                        {"name": "@108", "tokens": [150, 151], "isCanonical": False},
                    ]
                },
                [
                    {
                        "coin": "PURR/USDC",
                        "midPx": "0.0724",
                        "markPx": "0.0725",
                        "dayNtlVlm": "1196478.61",
                    },
                    {"coin": "@107", "midPx": "42.5", "markPx": "42.6", "dayNtlVlm": "16071"},
                    {"coin": "@108", "midPx": "0.027", "dayNtlVlm": "500"},
                ],
            ]
        }
    )
    adapter = HyperliquidAdapter(client=client)

    rows = await adapter.fetch_spot_tickers()

    assert [(url, body) for url, body in client.posts] == [
        ("https://api.hyperliquid.xyz/info", {"type": "spotMetaAndAssetCtxs"})
    ]
    assert len(rows) == 2
    assert rows[0].symbol == "PURRUSDT"
    assert rows[0].base == "PURR"
    assert rows[0].quote == "USDT"
    assert rows[0].exchange == "hyperliquid"
    assert rows[0].market_type == MarketType.SPOT
    assert rows[0].bid == 0.0724
    assert rows[0].ask == 0.0724
    assert rows[0].mark_price == 0.0725
    assert rows[0].volume_24h_usdt == 1196478.61
    assert rows[1].symbol == "HYPEUSDT"
    assert rows[1].base == "HYPE"
    assert rows[1].bid == 42.5
    assert rows[1].ask == 42.5
    assert rows[1].mark_price == 42.6


@pytest.mark.asyncio
async def test_hyperliquid_uses_predicted_fundings_for_next_rate_and_time() -> None:
    client = FakePostClient(
        {
            ("perpDexs", None): [],
            ("metaAndAssetCtxs", None): [
                {"universe": [{"name": "BTC"}]},
                [
                    {
                        "midPx": "100.5",
                        "markPx": "100.7",
                        "oraclePx": "100.2",
                        "funding": "0.0000125",
                        "dayNtlVlm": "1234567.89",
                    }
                ],
            ],
            ("predictedFundings", None): [
                ["BTC", [["HlPerp", {"fundingRate": "0.000025", "nextFundingTime": 1779170400000, "fundingIntervalHours": 1}]]]
            ],
        }
    )
    adapter = HyperliquidAdapter(client=client)

    rows = await adapter.fetch_future_tickers()

    btc = next(row for row in rows if row.symbol == "BTCUSDT")
    assert btc.funding_rate_pct == 0.00125
    assert btc.funding_next_rate_pct == 0.0025
    assert btc.funding_interval_hours == 1
    assert btc.funding_next_time is not None


@pytest.mark.parametrize(
    "adapter_cls",
    [AsterAdapter, BitgetAdapter, BybitAdapter, GateAdapter, HTXAdapter],
)
def test_exchange_adapters_use_shared_get_json(adapter_cls, monkeypatch) -> None:
    called_urls: list[str] = []

    async def fake_get_json(self, url: str):
        called_urls.append(url)
        if adapter_cls is AsterAdapter:
            return []
        if adapter_cls is BybitAdapter:
            return {"result": {"list": []}}
        if adapter_cls is BitgetAdapter:
            return {"data": []}
        if adapter_cls is GateAdapter:
            return []
        if adapter_cls is HTXAdapter:
            return {"data": [], "ticks": []}
        return {}

    monkeypatch.setattr(adapter_cls, "get_json", fake_get_json)

    adapter = adapter_cls()
    if adapter_cls is HTXAdapter:
        # HTX spot uses "data", future uses "ticks"; exercise both response shapes.
        assert called_urls == []

    import asyncio

    asyncio.run(adapter.fetch_spot_tickers())
    asyncio.run(adapter.fetch_future_tickers())

    expected_calls_by_adapter = {
        AsterAdapter: 3,
        BitgetAdapter: 3,
        BybitAdapter: 2,
        GateAdapter: 3,
        HTXAdapter: 3,
    }
    expected_calls = expected_calls_by_adapter[adapter_cls]
    assert len(called_urls) == expected_calls
