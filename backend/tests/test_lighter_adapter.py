from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.exchanges.lighter import LighterAdapter, lighter_best_prices
from app.models.market import MarketType
from app.models.pair_spread import PairSpreadLegQuery
from app.services.pair_spread_query import PairSpreadQueryService


def detail(symbol: str, market_id: int, market_type: str = "perp", status: str = "active") -> dict:
    return {
        "symbol": symbol, "market_id": market_id, "market_type": market_type,
        "status": status, "mark_price": "100", "index_price": "99",
        "daily_quote_token_volume": "500000", "open_interest": "10",
    }


def book(bid: str = "99", ask: str = "101") -> dict:
    return {
        "code": 200,
        "bids": [
            {"price": bid, "remaining_base_amount": "2"},
            {"price": bid, "remaining_base_amount": "3"},
        ],
        "asks": [{"price": ask, "remaining_base_amount": "4"}],
    }


@pytest.mark.asyncio
async def test_lighter_collects_active_perps_and_spot_with_real_book_and_funding(monkeypatch) -> None:
    urls: list[str] = []

    async def fake_get(self, url: str):
        urls.append(url)
        if url.endswith("orderBookDetails"):
            return {
                "code": 200,
                "order_book_details": [detail("ETH", 0), detail("1000PEPE", 4), detail("OLD", 5, status="inactive")],
                "spot_order_book_details": [detail("ETH/USDC", 2048, "spot")],
            }
        if url.endswith("funding-rates"):
            return {"code": 200, "funding_rates": [
                {"exchange": "lighter", "market_id": 0, "rate": 0.000032},
                {"exchange": "binance", "market_id": 4, "rate": 0.5},
            ]}
        if "market_id=4" in url:
            return {"code": 200, "bids": [], "asks": []}
        return book()

    monkeypatch.setattr(LighterAdapter, "get_json", fake_get)
    adapter = LighterAdapter()
    try:
        perps = await adapter.fetch_future_tickers()
        spot = await adapter.fetch_spot_tickers()
        assert [row.symbol for row in perps] == ["ETHUSDT"]
        assert perps[0].bid == 99 and perps[0].ask == 101
        assert perps[0].bid_size == 5
        assert perps[0].funding_rate_pct == pytest.approx(0.0032)
        assert perps[0].funding_interval_hours == 1
        assert perps[0].volume_24h_usdt == 500000
        assert [row.symbol for row in spot] == ["ETHUSDT"]
        assert spot[0].raw_symbol == "ETH/USDC"
        assert spot[0].funding_rate_pct is None
        assert "market_id=5" not in " ".join(urls)
    finally:
        await adapter.client.aclose()


@pytest.mark.asyncio
async def test_lighter_order_book_uses_market_id_and_remaining_size(monkeypatch) -> None:
    async def fake_get(self, url: str):
        return {"code": 200, "order_book_details": [detail("ETH", 0)]} if url.endswith("orderBookDetails") else book()

    monkeypatch.setattr(LighterAdapter, "get_json", fake_get)
    adapter = LighterAdapter()
    try:
        result = await adapter.fetch_order_book("ETHUSDT", MarketType.FUTURE, "ETH")
        assert result is not None
        assert result.bids[0].size == 2
        assert result.raw_symbol == "ETH"
    finally:
        await adapter.client.aclose()


def test_lighter_rejects_crossed_book_and_accepts_pair_query() -> None:
    assert lighter_best_prices(book("101", "99")) is None
    assert PairSpreadLegQuery(exchange="lighter", symbol="ETH", market_type=MarketType.SPOT).symbol == "ETHUSDT"


@pytest.mark.asyncio
async def test_lighter_pair_query_current_candles_and_signed_historical_funding(monkeypatch) -> None:
    service = PairSpreadQueryService()
    start = datetime(2026, 9, 14, 10, tzinfo=UTC)
    end = start + timedelta(minutes=501)
    requested: list[str] = []

    async def fake_get(url: str):
        requested.append(url)
        if url.endswith("orderBookDetails"):
            return {"code": 200, "order_book_details": [detail("ETH", 0)], "spot_order_book_details": []}
        if "orderBookOrders" in url:
            return book()
        if "funding-rates" in url:
            return {"code": 200, "funding_rates": [{"exchange": "lighter", "market_id": 0, "rate": -0.0001}]}
        if "/candles?" in url:
            start_seconds = int(parse_qs(urlparse(url).query)["start_timestamp"][0])
            return {"code": 200, "c": [{"t": start_seconds * 1000, "c": "100", "V": "1200"}]}
        if "/fundings?" in url:
            return {"code": 200, "fundings": [
                {"timestamp": int(start.timestamp()), "rate": "0.0009", "direction": "long"},
                {"timestamp": int(start.timestamp()) + 3600, "rate": "0.0005", "direction": "short"},
            ]}
        raise AssertionError(url)

    monkeypatch.setattr(service, "_get_json", fake_get)
    monkeypatch.setattr(service, "_get_json_optional", fake_get)
    try:
        current = await service._fetch_lighter_current("ETHUSDT")
        assert current.price == 100
        assert current.funding_rate_pct == pytest.approx(-0.01)
        assert current.open_interest_usdt == 1000
        candles = await service._fetch_lighter_klines("ETHUSDT", start, end, 1)
        assert len([url for url in requested if "/candles?" in url]) == 2
        assert candles[0].volume_usdt == 1200
        funding = await service._fetch_lighter_funding("ETHUSDT", start, end)
        assert [point.funding_rate_pct for point in funding] == [0.0009, -0.0005]
    finally:
        await service.aclose()
