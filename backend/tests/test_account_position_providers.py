
import httpx
import pytest

from app.models.market import MarketType
from app.services.account_position_providers import (
    CcxtAccountPositionProvider,
    HyperliquidAccountPositionProvider,
)


class FakeCcxtClient:
    def __init__(self) -> None:
        self.markets = {
            "BTC/USDT:USDT": {
                "id": "BTC-USDT-SWAP",
                "symbol": "BTC/USDT:USDT",
                "base": "BTC",
                "quote": "USDT",
                "contractSize": 0.001,
            },
            "BTC/USDT": {
                "id": "BTCUSDT",
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "active": True,
            },
        }
        self.closed = False

    async def load_markets(self):
        return self.markets

    def market(self, symbol: str):
        return self.markets[symbol]

    async def fetch_positions(self):
        return [
            {
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "contracts": 20,
                "entryPrice": 60_000,
                "markPrice": 61_000,
                "notional": 1_220,
                "unrealizedPnl": 20,
                "percentage": 8.2,
                "leverage": 5,
                "timestamp": 1_796_000_000_000,
                "info": {},
            },
            {
                "symbol": "BTC/USDT:USDT",
                "side": "short",
                "contracts": 8,
                "entryPrice": 62_000,
                "markPrice": 61_000,
                "notional": 488,
                "unrealizedPnl": 8,
                "percentage": 4.1,
                "leverage": 5,
                "info": {},
            },
        ]

    async def fetch_balance(self):
        return {"total": {"BTC": 0.01, "USDT": 0.25, "ETH": 0}}

    async def fetch_ticker(self, symbol: str):
        assert symbol == "BTC/USDT"
        return {"last": 60_000}

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_ccxt_futures_preserve_raw_market_dual_sides_and_metrics() -> None:
    client = FakeCcxtClient()
    provider = CcxtAccountPositionProvider(
        exchange="okx",
        account_id="okx:account_1",
        account_label="OKX 主账户",
        market_type=MarketType.FUTURE,
        credentials={"api_key": "test", "api_secret": "test"},
        client=client,
    )

    positions = await provider.fetch_positions()

    assert [item.side for item in positions] == ["long", "short"]
    assert len({item.id for item in positions}) == 2
    assert positions[0].raw_symbol == "BTC-USDT-SWAP"
    assert positions[0].symbol == "BTCUSDT"
    assert positions[0].quantity == pytest.approx(0.02)
    assert positions[0].contract_quantity == 20
    assert positions[0].contract_multiplier == pytest.approx(0.001)
    assert positions[0].notional_usdt == 1_220
    assert positions[0].unrealized_pnl_usdt == 20
    assert positions[0].roi_pct == 8.2
    assert positions[0].leverage == 5


@pytest.mark.asyncio
async def test_ccxt_spot_keeps_dust_and_unknown_valuation_for_ui_filtering() -> None:
    client = FakeCcxtClient()
    provider = CcxtAccountPositionProvider(
        exchange="binance",
        account_id="binance:account_1",
        account_label="Binance",
        market_type=MarketType.SPOT,
        credentials={"api_key": "test", "api_secret": "test"},
        client=client,
    )

    positions = await provider.fetch_positions()

    assert [item.quantity_unit for item in positions] == ["BTC", "USDT"]
    btc, usdt = positions
    assert btc.raw_symbol == "BTCUSDT"
    assert btc.notional_usdt == 600
    assert {"mark_price", "notional_usdt"}.issubset(btc.estimated_fields)
    assert usdt.raw_symbol == "USDT"
    assert usdt.notional_usdt == 0.25


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dex", "coin", "expected_raw"),
    [("main", "BTC", "BTC"), ("xyz", "xyz:BTC", "xyz:BTC")],
)
async def test_hyperliquid_public_address_preserves_dex_identity(
    dex: str,
    coin: str,
    expected_raw: str,
) -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        requests.append(payload)
        if payload["type"] == "clearinghouseState":
            return httpx.Response(
                200,
                json={
                    "assetPositions": [
                        {
                            "position": {
                                "coin": coin,
                                "szi": "-2",
                                "entryPx": "100",
                                "positionValue": "220",
                                "unrealizedPnl": "20",
                                "returnOnEquity": "0.1",
                                "leverage": {"type": "cross", "value": "4"},
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json=[{"universe": [{"name": coin}]}, [{"markPx": "110"}]],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HyperliquidAccountPositionProvider(
        account_id="hyperliquid:account_1",
        account_label="HL",
        public_address="0x1111111111111111111111111111111111111111",
        dex=dex,
        client=client,
    )

    positions = await provider.fetch_positions()

    assert len(positions) == 1
    assert positions[0].dex == dex
    assert positions[0].raw_symbol == expected_raw
    assert positions[0].side == "short"
    assert positions[0].mark_price == 110
    assert positions[0].roi_pct == 10
    assert "notional_usdt" in positions[0].estimated_fields
    assert "unrealized_pnl_usdt" in positions[0].estimated_fields
    assert all(payload.get("dex") == dex for payload in requests) if dex != "main" else all(
        "dex" not in payload for payload in requests
    )
    await client.aclose()
