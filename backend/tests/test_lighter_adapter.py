import asyncio
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.exchanges.base import ExchangeRequestError
from app.exchanges.lighter import LighterAdapter, lighter_best_prices, lighter_order_books
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

    async def fake_books(market_ids: list[int]):
        return {market_id: book() for market_id in market_ids if market_id != 4}

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
        raise AssertionError(url)

    monkeypatch.setattr(LighterAdapter, "get_json", fake_get)
    monkeypatch.setattr("app.exchanges.lighter.lighter_order_books", fake_books)
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
        assert len([url for url in urls if url.endswith("orderBookDetails")]) == 1
    finally:
        await adapter.client.aclose()


@pytest.mark.asyncio
async def test_lighter_always_scans_priority_hood_market_within_perp_limit(monkeypatch) -> None:
    requested_market_ids: list[int] = []

    async def fake_books(market_ids: list[int]):
        requested_market_ids.extend(market_ids)
        return {market_id: book() for market_id in market_ids}

    async def fake_get(self, url: str):
        if url.endswith("orderBookDetails"):
            btc = detail("BTC", 1)
            btc["daily_quote_token_volume"] = "3000000"
            eth = detail("ETH", 2)
            eth["daily_quote_token_volume"] = "2000000"
            hood = detail("HOOD", 108)
            hood["daily_quote_token_volume"] = "1000"
            return {
                "code": 200,
                "order_book_details": [btc, eth, hood],
                "spot_order_book_details": [],
            }
        if url.endswith("funding-rates"):
            return {"code": 200, "funding_rates": []}
        raise AssertionError(url)

    monkeypatch.setattr(LighterAdapter, "get_json", fake_get)
    monkeypatch.setattr("app.exchanges.lighter.lighter_order_books", fake_books)
    adapter = LighterAdapter()
    adapter.max_scanner_perp_markets = 2
    try:
        perps = await adapter.fetch_future_tickers()
        assert [row.symbol for row in perps] == ["BTCUSDT", "HOODUSDT"]
        assert requested_market_ids == [108, 1]
    finally:
        await adapter.client.aclose()


@pytest.mark.asyncio
async def test_lighter_fails_closed_when_priority_hood_book_is_missing(monkeypatch) -> None:
    async def fake_get(self, url: str):
        if url.endswith("orderBookDetails"):
            return {
                "code": 200,
                "order_book_details": [detail("BTC", 1), detail("HOOD", 108)],
                "spot_order_book_details": [],
            }
        if url.endswith("funding-rates"):
            return {"code": 200, "funding_rates": []}
        raise AssertionError(url)

    async def fake_books(market_ids: list[int]):
        return {1: book()}

    monkeypatch.setattr(LighterAdapter, "get_json", fake_get)
    monkeypatch.setattr("app.exchanges.lighter.lighter_order_books", fake_books)
    adapter = LighterAdapter()
    try:
        with pytest.raises(RuntimeError, match="missing Lighter priority order books: HOOD"):
            await adapter.fetch_future_tickers()
    finally:
        await adapter.client.aclose()


@pytest.mark.asyncio
async def test_lighter_order_book_uses_market_id_and_remaining_size(monkeypatch) -> None:
    async def fake_books(market_ids: list[int]):
        return {market_id: book() for market_id in market_ids}

    async def fake_get(self, url: str):
        return {"code": 200, "order_book_details": [detail("ETH", 0)],
                "spot_order_book_details": []}

    monkeypatch.setattr(LighterAdapter, "get_json", fake_get)
    monkeypatch.setattr("app.exchanges.lighter.lighter_order_books", fake_books)
    adapter = LighterAdapter()
    try:
        result = await adapter.fetch_order_book("ETHUSDT", MarketType.FUTURE, "ETH")
        assert result is not None
        assert result.bids[0].size == 2
        assert result.raw_symbol == "ETH"
    finally:
        await adapter.client.aclose()


@pytest.mark.asyncio
async def test_lighter_websocket_subscribes_and_parses_book_snapshots(monkeypatch) -> None:
    sent: list[dict] = []
    messages = iter(
        [
            {"type": "connected"},
            {
                "type": "subscribed/order_book",
                "channel": "order_book:108",
                "order_book": {
                    "code": 0,
                    "bids": [{"price": "117.4", "size": "2"}],
                    "asks": [{"price": "117.5", "size": "3"}],
                },
            },
            {"type": "ping"},
            {
                "type": "subscribed/order_book",
                "channel": "order_book:1",
                "order_book": {
                    "code": 0,
                    "bids": [{"price": "100", "size": "4"}],
                    "asks": [{"price": "101", "size": "5"}],
                },
            },
        ]
    )

    class FakeWebSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def recv(self):
            return json.dumps(next(messages))

        async def send(self, message: str):
            sent.append(json.loads(message))

    def fake_connect(*args, **kwargs):
        return FakeWebSocket()

    monkeypatch.setattr("app.exchanges.lighter.websocket_connect", fake_connect)

    snapshots = await lighter_order_books([108, 1])

    assert [message["channel"] for message in sent if message["type"] == "subscribe"] == [
        "order_book/108",
        "order_book/1",
    ]
    assert {market_id: lighter_best_prices(payload) for market_id, payload in snapshots.items()} == {
        108: (117.4, 117.5, 2.0, 3.0),
        1: (100.0, 101.0, 4.0, 5.0),
    }
    assert {"type": "pong"} in sent


def details_405() -> ExchangeRequestError:
    request = httpx.Request("GET", "https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails")
    response = httpx.Response(405, request=request)
    return ExchangeRequestError("GET", str(request.url), httpx.HTTPStatusError("405", request=request, response=response))


@pytest.mark.asyncio
async def test_lighter_concurrent_market_types_share_details_fetch(monkeypatch) -> None:
    calls = 0

    async def fake_books(market_ids: list[int]):
        return {market_id: book() for market_id in market_ids}

    async def fake_get(self, url: str):
        nonlocal calls
        if url.endswith("orderBookDetails"):
            calls += 1
            await asyncio.sleep(0)
            return {"code": 200, "order_book_details": [detail("ETH", 0)],
                    "spot_order_book_details": [detail("ETH/USDC", 2048, "spot")]}
        if url.endswith("funding-rates"):
            return {"code": 200, "funding_rates": []}
        return book()

    monkeypatch.setattr(LighterAdapter, "get_json", fake_get)
    monkeypatch.setattr("app.exchanges.lighter.lighter_order_books", fake_books)
    adapter = LighterAdapter()
    try:
        future, spot = await asyncio.gather(adapter.fetch_future_tickers(), adapter.fetch_spot_tickers())
        assert future and spot
        assert calls == 1
    finally:
        await adapter.client.aclose()


@pytest.mark.asyncio
async def test_lighter_405_uses_short_lived_details_but_refreshes_prices_and_recovers(monkeypatch) -> None:
    calls = 0
    price = "99"
    rejecting = False
    resets = 0

    async def fake_books(market_ids: list[int]):
        return {market_id: book(bid=price) for market_id in market_ids}

    async def fake_get(self, url: str):
        nonlocal calls
        if url.endswith("orderBookDetails"):
            calls += 1
            if rejecting:
                raise details_405()
            return {"code": 200, "order_book_details": [detail("ETH", 0)], "spot_order_book_details": []}
        if url.endswith("funding-rates"):
            return {"code": 200, "funding_rates": []}
        return book(bid=price)

    async def fake_reset(self):
        nonlocal resets
        resets += 1

    monkeypatch.setattr(LighterAdapter, "get_json", fake_get)
    monkeypatch.setattr(LighterAdapter, "reset_client", fake_reset)
    monkeypatch.setattr("app.exchanges.lighter.lighter_order_books", fake_books)
    adapter = LighterAdapter()
    try:
        first = await adapter.fetch_future_tickers()
        assert first[0].bid == 99
        adapter._details = (datetime.now(UTC) - timedelta(seconds=61), adapter._details[1])
        adapter._cached.clear()
        price, rejecting = "98", True
        fallback = await adapter.fetch_future_tickers()
        assert fallback[0].bid == 98
        assert fallback[0].timestamp >= first[0].timestamp
        assert calls == 3 and resets == 1
        adapter._cached.clear()
        await adapter.fetch_future_tickers()
        assert calls == 3  # 30-second cooldown, books still refresh.
        adapter._details_retry_after = datetime.now(UTC) - timedelta(seconds=1)
        adapter._cached.clear()
        rejecting = False
        await adapter.fetch_future_tickers()
        assert calls == 4
        assert adapter._details_retry_after is None
    finally:
        await adapter.client.aclose()


@pytest.mark.asyncio
async def test_lighter_405_without_recent_details_fails_closed(monkeypatch) -> None:
    async def fake_get(self, url: str):
        if url.endswith("orderBookDetails"):
            raise details_405()
        return book()

    async def fake_reset(self):
        return None

    monkeypatch.setattr(LighterAdapter, "get_json", fake_get)
    monkeypatch.setattr(LighterAdapter, "reset_client", fake_reset)
    adapter = LighterAdapter()
    try:
        with pytest.raises(ExchangeRequestError):
            await adapter.fetch_future_tickers()
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

    async def fake_books(market_ids: list[int]):
        return {market_id: book() for market_id in market_ids}

    async def fake_get(url: str):
        requested.append(url)
        if url.endswith("orderBookDetails"):
            return {"code": 200, "order_book_details": [detail("ETH", 0)], "spot_order_book_details": []}
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
    monkeypatch.setattr("app.services.pair_spread_query.lighter_order_books", fake_books)
    try:
        current = await service._fetch_lighter_current("ETHUSDT")
        assert current.price == 100
        assert current.funding_rate_pct == pytest.approx(-0.01)
        assert current.open_interest_usdt == 1000
        assert not any("orderBookOrders" in url for url in requested)
        candles = await service._fetch_lighter_klines("ETHUSDT", start, end, 1)
        assert len([url for url in requested if "/candles?" in url]) == 2
        assert candles[0].volume_usdt == 1200
        funding = await service._fetch_lighter_funding("ETHUSDT", start, end)
        assert [point.funding_rate_pct for point in funding] == [0.0009, -0.0005]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_lighter_pair_legs_share_one_market_details_request(monkeypatch) -> None:
    service = PairSpreadQueryService()
    details_calls = 0

    async def fake_books(market_ids: list[int]):
        return {market_id: book() for market_id in market_ids}

    async def fake_get(url: str):
        nonlocal details_calls
        if url.endswith("orderBookDetails"):
            details_calls += 1
            if details_calls > 1:
                raise RuntimeError("simultaneous market details request rejected")
            await asyncio.sleep(0)
            return {"code": 200, "order_book_details": [detail("ETH", 0)],
                    "spot_order_book_details": [detail("ETH/USDC", 2048, "spot")]}
        return {"code": 200, "funding_rates": []}

    monkeypatch.setattr(service, "_get_json", fake_get)
    monkeypatch.setattr(service, "_get_json_optional", fake_get)
    monkeypatch.setattr("app.services.pair_spread_query.lighter_order_books", fake_books)
    try:
        future, spot = await asyncio.gather(
            service._fetch_lighter_current("ETHUSDT"),
            service._fetch_lighter_current("ETHUSDT", market_type=MarketType.SPOT),
        )
        assert future.market_type == MarketType.FUTURE
        assert spot.raw_symbol == "ETH/USDC"
        assert details_calls == 1
    finally:
        await service.aclose()
