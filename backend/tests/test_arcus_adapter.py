import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.exchanges.arcus import (
    ARCUS_URL,
    ARCUS_WS_URL,
    ArcusAdapter,
    arcus_bbo_prices,
    arcus_bbos,
    arcus_market_rows,
    arcus_timestamp,
)
from app.main import create_app
from app.models.market import MarketType
from app.models.negative_basis import NEGATIVE_BASIS_FUTURE_EXCHANGES, NEGATIVE_BASIS_SPOT_EXCHANGES
from app.models.pair_spread import PairSpreadLegQuery
from app.services.collector import default_exchange_adapters
from app.services.pair_spread_query import PairSpreadQueryService
from app.services.snapshot_store import SnapshotStore
from app.services.trade_availability import TradeAvailabilityService

OBSERVED_AT = datetime(2026, 10, 8, 7, tzinfo=UTC)


def market(symbol="BTC", **overrides):
    return {
        "marketDisplayName": f"{symbol}-USD", "marketId": 1,
        "baseAsset": symbol, "quoteAsset": "USD", "type": "PERPETUAL", "status": "ONLINE",
        "markPrice": "100.5", "oraclePrice": "100", "lastTradePrice": "100.4",
        "volume24h": "9999", "volume24hNotional": "2000000", "openInterest": "12",
        "fundingRate": "-0.0000125", "nextFundingRate": "0.000025",
        "nextFundingAt": int((OBSERVED_AT + timedelta(hours=1)).timestamp()),
        **overrides,
    }


def bbo(**overrides):
    return {
        "bestBid": {"price": "100", "size": "10"},
        "bestAsk": {"price": "101", "size": "8"},
        "timestamp": int(OBSERVED_AT.timestamp() * 1_000_000),
        **overrides,
    }


def test_arcus_filters_market_status_type_quote_and_raw_identity():
    payload = {"markets": [
        market(), market("ETH", status="OFFLINE"), market("SOL", type="SPOT"),
        market("NVDA", quoteAsset="USDC"), market("OPENAI", marketDisplayName="OAI-USD"),
        None, {"type": "PERPETUAL"}, market("../BTC"),
    ]}
    assert arcus_market_rows(payload) == [market()]
    with pytest.raises(RuntimeError, match="invalid Arcus markets"):
        arcus_market_rows({"error": "maintenance"})


@pytest.mark.parametrize("overrides", [
    {"bestBid": None}, {"bestAsk": {"price": "99", "size": "1"}},
    {"bestAsk": {"price": "100", "size": "1"}},
    {"bestBid": {"price": "NaN", "size": "1"}},
    {"bestAsk": {"price": "Infinity", "size": "1"}},
    {"bestBid": {"price": "100", "size": "0"}},
])
def test_arcus_rejects_non_executable_bbo(overrides):
    assert arcus_bbo_prices(bbo(**overrides)) is None


def test_arcus_timestamps_use_documented_units():
    assert arcus_timestamp(int(OBSERVED_AT.timestamp() * 1_000_000)) == OBSERVED_AT
    assert arcus_timestamp(int(OBSERVED_AT.timestamp()), microseconds=False) == OBSERVED_AT
    assert arcus_timestamp("NaN") is None
    assert arcus_timestamp(-1) is None
    assert arcus_timestamp(1e300) is None


@pytest.mark.asyncio
async def test_arcus_adapter_keeps_native_market_funding_turnover_and_cache(monkeypatch):
    requests = []

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={"markets": [market(), market("ETH", status="OFFLINE")]})

    fake_books = AsyncMock(return_value={"BTC-USD": bbo()})
    monkeypatch.setattr("app.exchanges.arcus.arcus_bbos", fake_books)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = ArcusAdapter(client)
        assert await adapter.fetch_spot_tickers() == []
        first = await adapter.fetch_future_tickers()
        assert await adapter.fetch_future_tickers() is first
    assert requests == [f"{ARCUS_URL}/markets"]
    fake_books.assert_awaited_once_with(["BTC-USD"])
    snapshot = first[0]
    assert snapshot.exchange == "arcus"
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.raw_symbol == "BTC-USD"
    assert snapshot.bid == 100 and snapshot.ask == 101
    assert snapshot.bid_size == 10 and snapshot.ask_size == 8
    assert snapshot.volume_24h_usdt == 2_000_000
    assert snapshot.funding_rate_pct == pytest.approx(-0.00125)
    assert snapshot.funding_next_rate_pct == pytest.approx(0.0025)
    assert snapshot.funding_interval_hours == 1
    assert snapshot.funding_next_time == OBSERVED_AT + timedelta(hours=1)
    assert snapshot.contract_size_multiplier == 1
    assert snapshot.upstream_timestamp == OBSERVED_AT
    assert snapshot.is_estimated is False
    assert snapshot.data_source.endswith("(USD)")


@pytest.mark.asyncio
async def test_arcus_never_substitutes_mark_price_for_empty_bbo(monkeypatch):
    monkeypatch.setattr("app.exchanges.arcus.arcus_bbos", AsyncMock(return_value={
        "BTC-USD": bbo(bestBid=None, bestAsk=None),
    }))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"markets": [market()]}),
    )) as client:
        assert await ArcusAdapter(client).fetch_future_tickers() == []


@pytest.mark.asyncio
async def test_arcus_orderbook_uses_native_market_and_caps_depth():
    requests = []

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={
            "bids": [["100", "10"]], "asks": [["101", "8"]],
            "timestamp": int(OBSERVED_AT.timestamp() * 1_000_000),
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = ArcusAdapter(client)
        assert await adapter.fetch_order_book("BTCUSDT", MarketType.SPOT, "BTC-USD") is None
        book = await adapter.fetch_order_book("BTCUSDT", MarketType.FUTURE, "BTC-USD", 1000)
    assert requests == [f"{ARCUS_URL}/l2OrderBook/BTC-USD?nLevels=100"]
    assert book.raw_symbol == "BTC-USD"
    assert book.timestamp == OBSERVED_AT
    assert book.bids[0].size == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_arcus_websocket_reads_initial_contents_and_requires_all_markets(monkeypatch, missing):
    sent = []
    messages = [
        {"type": "connected"},
        {"type": "channel_data", "channel": "trades", "id": "BTC-USD", "contents": {}},
        {"type": "subscribed", "channel": "bbo", "id": "BTC-USD", "contents": bbo()},
    ]
    if not missing:
        messages.append({"type": "subscribed", "channel": "bbo", "id": "ETH-USD", "contents": bbo()})

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send(self, message):
            sent.append(json.loads(message))

        async def recv(self):
            if not messages:
                raise TimeoutError
            return json.dumps(messages.pop(0))

    def connect(url, **kwargs):
        assert url == ARCUS_WS_URL
        return Socket()

    monkeypatch.setattr("app.exchanges.arcus.websocket_connect", connect)
    if missing:
        with pytest.raises(RuntimeError, match="missing Arcus BBO snapshots: ETH-USD"):
            await arcus_bbos(["BTC-USD", "ETH-USD", "BTC-USD"])
    else:
        assert set(await arcus_bbos(["BTC-USD", "ETH-USD", "BTC-USD"])) == {"BTC-USD", "ETH-USD"}
    assert sent == [
        {"type": "subscribe", "channel": "bbo", "id": "BTC-USD"},
        {"type": "subscribe", "channel": "bbo", "id": "ETH-USD"},
    ]


@pytest.mark.asyncio
async def test_arcus_pair_current_is_executable_and_native():
    def handle(request):
        if request.url.path == "/v1/markets":
            return httpx.Response(200, json={"markets": [market()]})
        assert request.url.path == "/v1/bbo/BTC-USD"
        return httpx.Response(200, json=bbo())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service = PairSpreadQueryService(client)
        current = await service._fetch_current_leg("arcus", "BTCUSDT")
        with pytest.raises(RuntimeError, match="not found"):
            await service._fetch_arcus_current("OAIUSDT")
    assert current.exchange == "arcus"
    assert current.raw_symbol == "BTC-USD"
    assert current.bid_price == 100 and current.ask_price == 101
    assert current.funding_interval_hours == 1
    assert current.funding_rate_pct == pytest.approx(-0.00125)
    assert current.funding_next_rate_pct == pytest.approx(0.0025)
    assert current.volume_24h_usdt == 2_000_000
    assert current.open_interest_contracts == 12
    assert current.open_interest_usdt == pytest.approx(1206)
    assert current.fee_is_estimated is True
    assert current.estimated_taker_fee_pct == 0.05
    assert current.upstream_timestamp == OBSERVED_AT


@pytest.mark.asyncio
async def test_arcus_candles_paginate_microseconds_sort_and_use_notional_volume():
    start = OBSERVED_AT - timedelta(minutes=1502)
    windows = []

    def handle(request):
        if request.url.path == "/v1/markets":
            return httpx.Response(200, json={"markets": [market()]})
        assert request.url.path == "/v1/candles"
        params = parse_qs(urlparse(str(request.url)).query)
        assert params["market"] == ["BTC-USD"] and params["timeframe"] == ["1m"]
        lower, upper = int(params["from"][0]), int(params["to"][0])
        windows.append((lower, upper))
        rows = [{
            "marketDisplayName": "BTC-USD", "openTime": timestamp,
            "close": "100.5", "volume": "1234", "notionalVolume": "54321",
        } for timestamp in range(lower, upper, 60_000_000)]
        rows.append({"marketDisplayName": "ETH-USD", "openTime": lower, "close": "1"})
        return httpx.Response(200, json={"candles": list(reversed(rows))})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        points = await PairSpreadQueryService(client)._fetch_klines("arcus", "BTCUSDT", start, OBSERVED_AT, 1)
    assert len(windows) == 2 and windows[0][1] == windows[1][0]
    assert windows[0][0] == int(start.timestamp() * 1_000_000)
    assert len(points) == 1503
    assert points[0].bucket_at == start and points[-1].bucket_at == OBSERVED_AT
    assert all(point.volume_usdt == 54321 for point in points)


@pytest.mark.asyncio
async def test_arcus_funding_paginates_without_duplicate_boundary():
    timestamps = [OBSERVED_AT - timedelta(hours=index) for index in range(1002)]
    windows = []

    def handle(request):
        if request.url.path == "/v1/markets":
            return httpx.Response(200, json={"markets": [market()]})
        assert request.url.path == "/v1/fundingRates"
        params = parse_qs(urlparse(str(request.url)).query)
        upper = int(params["to"][0])
        windows.append(upper)
        rows = [{
            "marketDisplayName": "BTC-USD", "time": int(timestamp.timestamp() * 1_000_000),
            "fundingRate": "-0.0000125",
        } for timestamp in timestamps if int(timestamp.timestamp() * 1_000_000) <= upper][:1000]
        return httpx.Response(200, json={"fundingRates": rows})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        points = await PairSpreadQueryService(client)._fetch_funding_history(
            "arcus", "BTCUSDT", timestamps[-1], OBSERVED_AT,
        )
    assert len(windows) == 2
    assert windows[1] == int(timestamps[999].timestamp() * 1_000_000) - 1
    assert len(points) == 1002
    assert points[0].funding_time == timestamps[-1] and points[-1].funding_time == OBSERVED_AT
    assert all(point.exchange == "arcus" and point.funding_rate_pct == pytest.approx(-0.00125) for point in points)


@pytest.mark.asyncio
@pytest.mark.parametrize("fee_tiers", [[{"level": 0, "maker_fee_ppm": 0, "taker_fee_ppm": 225}], None])
async def test_arcus_status_reports_native_online_market_and_public_base_fees(monkeypatch, fee_tiers):
    def handle(request):
        if request.url.path == "/v1/markets":
            return httpx.Response(200, json={"markets": [market()]})
        if request.url.path == "/v1/feetiers":
            return httpx.Response(200, json={"tiers": fee_tiers})
        assert request.url.path == "/v1/l2OrderBook/BTC-USD"
        return httpx.Response(200, json={
            "bids": [["100", "10"]], "asks": [["101", "8"]],
            "timestamp": int(OBSERVED_AT.timestamp() * 1_000_000),
        })

    monkeypatch.setattr("app.exchanges.arcus.arcus_bbos", AsyncMock(return_value={"BTC-USD": bbo()}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = ArcusAdapter(client)
        store = SnapshotStore()
        store.set_all_markets(await adapter.fetch_future_tickers())
        service = TradeAvailabilityService(store, [adapter], AsyncMock(), client)
        result = await service.fetch_status("BTCUSDT")
        await service.aclose()
    assert result.errors == {}
    status = result.markets[0]
    assert status.exchange == "arcus" and status.raw_symbol == "BTC-USD"
    assert status.public_status_code == "ONLINE"
    assert status.taker_fee_pct == (pytest.approx(0.0225) if fee_tiers else None)
    assert status.maker_fee_pct == (0 if fee_tiers else None)
    assert status.funding_interval_hours == 1
    assert result.index_compositions[0].index_price == 100
    assert any(item.exchange == "arcus" for item in result.coverage)


def test_arcus_model_accepts_native_symbol_and_rejects_spot_without_extending_other_modules():
    assert PairSpreadLegQuery(exchange="arcus", symbol="btc-usd").symbol == "BTCUSDT"
    with pytest.raises(ValidationError, match="spot RFQ is not integrated"):
        PairSpreadLegQuery(exchange="arcus", symbol="BTC", market_type=MarketType.SPOT)
    with pytest.raises(ValidationError, match="dex is only supported"):
        PairSpreadLegQuery(exchange="arcus", symbol="BTC", dex="io")
    assert "arcus" not in NEGATIVE_BASIS_SPOT_EXCHANGES
    assert "arcus" not in NEGATIVE_BASIS_FUTURE_EXCHANGES


@pytest.mark.asyncio
async def test_arcus_registered_collector_and_instrument_lookup_keep_raw_market(monkeypatch):
    adapters = default_exchange_adapters()
    try:
        assert [adapter.name for adapter in adapters].count("arcus") == 1
    finally:
        for adapter in adapters:
            await adapter.client.aclose()
    monkeypatch.setattr("app.exchanges.arcus.arcus_bbos", AsyncMock(return_value={"BTC-USD": bbo()}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"markets": [market()]}),
    )) as client:
        snapshots = await ArcusAdapter(client).fetch_future_tickers()
    store = SnapshotStore()
    store.set_all_markets(snapshots)
    app = create_app(snapshot_store=store, settings=Settings(database_url="sqlite:///:memory:"))
    with TestClient(app) as client:
        payload = client.get("/api/instruments/BTCUSDT").json()
    result = next(row for row in payload["exchanges"] if row["exchange"] == "arcus")
    assert result["spot"] is None
    assert result["future"]["raw_symbol"] == "BTC-USD"
    assert result["future"]["funding_interval_hours"] == 1
