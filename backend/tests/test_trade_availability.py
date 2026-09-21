from datetime import UTC, datetime
from unittest.mock import AsyncMock

import aiosqlite
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.schema import initialize_schema
from app.exchanges.base import ExchangeAdapter
from app.main import create_app
from app.models.hyperliquid_trade_status import (
    HyperliquidActionState,
    HyperliquidMarketTradeStatus,
    HyperliquidTradeActionStatus,
    HyperliquidTradeStatusResult,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.models.trade_availability import (
    MarketTradeAvailability,
    TradeActionStatus,
    TradeAvailabilityResult,
    TradeAvailabilityState,
    TradeAvailabilityWatch,
    TransferAvailabilityState,
)
from app.services.snapshot_store import SnapshotStore
from app.services.trade_availability import (
    TradeAvailabilityMonitor,
    TradeAvailabilityService,
    TradeAvailabilityWatchRepository,
)


class FakeAdapter(ExchangeAdapter):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))))

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        return []

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        return []

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=raw_symbol,
            bids=[OrderBookLevel(price=100, size=10), OrderBookLevel(price=99.5, size=5)],
            asks=[OrderBookLevel(price=101, size=8), OrderBookLevel(price=101.5, size=4)],
            timestamp=datetime(2026, 9, 21, 6, tzinfo=UTC),
        )


def _snapshot(exchange: str, market_type: MarketType, raw_symbol: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        exchange=exchange,
        market_type=market_type,
        bid=100,
        ask=101,
        volume_24h_usdt=2_000_000,
        funding_rate_pct=0.01 if market_type == MarketType.FUTURE else None,
        funding_interval_hours=8 if market_type == MarketType.FUTURE else None,
        timestamp=datetime(2026, 9, 21, 5, 59, tzinfo=UTC),
        raw_symbol=raw_symbol,
    )


def _metadata_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "www.binance.com/bapi/capital" in url:
        return httpx.Response(200, json={
            "code": "000000",
            "data": [{
                "coin": "BTC",
                "networkList": [
                    {"network": "BTC", "depositEnable": True, "withdrawEnable": True},
                    {"network": "BSC", "depositEnable": False, "withdrawEnable": True},
                ],
            }],
        })
    if "binance.vision" in url:
        return httpx.Response(200, json={"symbols": [{
            "symbol": "BTCUSDT", "status": "TRADING", "isSpotTradingAllowed": True,
        }]})
    if "fapi.binance.com" in url:
        return httpx.Response(200, json={"symbols": [{"symbol": "BTCUSDT", "status": "TRADING"}]})
    if "okx.com" in url:
        return httpx.Response(200, json={"data": [{
            "instId": "BTC-USDT-SWAP", "state": "live", "ctVal": "0.01", "ctMult": "1",
        }]})
    if "bybit.com" in url:
        return httpx.Response(200, json={
            "result": {"list": [{"symbol": "BTCUSDT", "status": "Trading"}]},
        })
    if "gateio.ws/api/v4/spot/currencies/" in url:
        return httpx.Response(200, json={
            "currency": "BTC",
            "chains": [{
                "name": "BTC",
                "deposit_disabled": False,
                "withdraw_disabled": False,
            }],
        })
    if "gateio.ws/api/v4/spot" in url:
        return httpx.Response(200, json={"id": "BTC_USDT", "trade_status": "buyable", "fee": "0.2"})
    if "bitget.com/api/v2/spot/public/coins" in url:
        return httpx.Response(200, json={
            "code": "00000",
            "data": [{
                "coin": "BTC",
                "chains": [{
                    "chain": "BTC",
                    "rechargeable": "false",
                    "withdrawable": "true",
                }],
            }],
        })
    if "bitget.com/api/v2/spot/public/symbols" in url:
        return httpx.Response(200, json={"data": [{
            "symbol": "BTCUSDT",
            "status": "online",
            "makerFeeRate": "0.001",
            "takerFeeRate": "0.001",
        }]})
    if "bitget.com/api/v2/mix" in url:
        return httpx.Response(200, json={"data": [{
            "symbol": "BTCUSDT",
            "symbolStatus": "normal",
            "makerFeeRate": "0.0002",
            "takerFeeRate": "0.0006",
        }]})
    if "sapi.asterdex.com" in url:
        return httpx.Response(200, json={"symbols": [{"symbol": "BTCUSDT", "status": "TRADING"}]})
    if "zklighter" in url:
        return httpx.Response(200, json={
            "code": 200,
            "spot_order_book_details": [],
            "order_book_details": [{
                "symbol": "BTC",
                "status": "active",
                "is_frozen": False,
                "maker_fee": "0.0000",
                "taker_fee": "0.0000",
                "multiplier": "1",
                "market_config": {"force_reduce_only": True},
            }],
        })
    raise AssertionError(f"unexpected URL {url}")


@pytest.mark.asyncio
async def test_trade_status_covers_core_exchanges_and_evaluated_venues() -> None:
    snapshots = [
        _snapshot("binance", MarketType.SPOT, "BTCUSDT"),
        _snapshot("okx", MarketType.FUTURE, "BTC-USDT-SWAP"),
        _snapshot("bybit", MarketType.FUTURE, "BTCUSDT"),
        _snapshot("gate", MarketType.SPOT, "BTC_USDT"),
        _snapshot("bitget", MarketType.FUTURE, "BTCUSDT"),
        _snapshot("aster", MarketType.SPOT, "BTCUSDT"),
        _snapshot("lighter", MarketType.FUTURE, "BTC"),
    ]
    store = SnapshotStore()
    store.set_all_markets(snapshots)
    adapters = [FakeAdapter(snapshot.exchange) for snapshot in snapshots]
    hyperliquid = AsyncMock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(_metadata_handler))
    service = TradeAvailabilityService(store, adapters, hyperliquid, client)

    result = await service.fetch_status("BTCUSDT")

    assert [market.exchange for market in result.markets] == [
        "binance", "okx", "bybit", "gate", "bitget", "aster", "lighter",
    ]
    by_key = {(market.exchange, market.market_type): market for market in result.markets}
    assert by_key[("binance", MarketType.SPOT)].buy_open.state == TradeAvailabilityState.AVAILABLE
    assert by_key[("binance", MarketType.SPOT)].buy_reduce_only.state == TradeAvailabilityState.NOT_APPLICABLE
    assert by_key[("binance", MarketType.SPOT)].spot_transfer is not None
    assert (
        by_key[("binance", MarketType.SPOT)].spot_transfer.deposit_state
        == TransferAvailabilityState.PARTIAL
    )
    assert by_key[("binance", MarketType.SPOT)].spot_transfer.withdraw_state == TransferAvailabilityState.ENABLED
    assert by_key[("okx", MarketType.FUTURE)].contract_size_multiplier == pytest.approx(0.01)
    assert by_key[("okx", MarketType.FUTURE)].bid_depth_1pct_usdt == pytest.approx(
        (100 * 10 + 99.5 * 5) * 0.01
    )
    assert by_key[("gate", MarketType.SPOT)].buy_open.state == TradeAvailabilityState.AVAILABLE
    assert by_key[("gate", MarketType.SPOT)].sell_open.state == TradeAvailabilityState.BLOCKED
    assert by_key[("gate", MarketType.SPOT)].spot_transfer is not None
    assert by_key[("gate", MarketType.SPOT)].spot_transfer.all_enabled is True
    assert by_key[("bitget", MarketType.FUTURE)].taker_fee_pct == pytest.approx(0.06)
    assert by_key[("aster", MarketType.SPOT)].coverage_tier == "evaluated"
    lighter = by_key[("lighter", MarketType.FUTURE)]
    assert lighter.coverage_tier == "evaluated"
    assert lighter.buy_open.reason_code == "FORCE_REDUCE_ONLY"
    assert lighter.buy_reduce_only.state == TradeAvailabilityState.CONDITIONAL
    assert all(market.fees_included is False for market in result.markets)
    assert {item.scope.value for item in result.markets[0].diagnostics} == {
        "public_market", "account", "order_error",
    }
    await service.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_spot_transfer_status_distinguishes_public_data_and_auth_only_sources() -> None:
    snapshots = [
        _snapshot("okx", MarketType.SPOT, "BTC-USDT"),
        _snapshot("bybit", MarketType.SPOT, "BTCUSDT"),
        _snapshot("bitget", MarketType.SPOT, "BTCUSDT"),
        _snapshot("aster", MarketType.SPOT, "BTCUSDT"),
    ]
    store = SnapshotStore()
    store.set_all_markets(snapshots)
    adapters = [FakeAdapter(snapshot.exchange) for snapshot in snapshots]
    client = httpx.AsyncClient(transport=httpx.MockTransport(_metadata_handler))
    service = TradeAvailabilityService(store, adapters, AsyncMock(), client)

    result = await service.fetch_status("BTCUSDT")

    by_exchange = {market.exchange: market for market in result.markets}
    bitget = by_exchange["bitget"].spot_transfer
    assert bitget is not None
    assert bitget.asset == "BTC"
    assert bitget.deposit_state == TransferAvailabilityState.DISABLED
    assert bitget.withdraw_state == TransferAvailabilityState.ENABLED
    assert bitget.all_enabled is False
    assert bitget.networks[0].network == "BTC"
    for exchange in ("okx", "bybit", "aster"):
        transfer = by_exchange[exchange].spot_transfer
        assert transfer is not None
        assert transfer.publicly_queryable is False
        assert transfer.deposit_state == TransferAvailabilityState.UNKNOWN
        assert transfer.withdraw_state == TransferAvailabilityState.UNKNOWN
        assert transfer.all_enabled is None
        assert transfer.observed_at is None
    await service.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_transfer_status_uses_future_market_when_no_spot_pair_exists() -> None:
    snapshot = _snapshot("binance", MarketType.FUTURE, "BTCUSDT")
    store = SnapshotStore()
    store.set_all_markets([snapshot])
    client = httpx.AsyncClient(transport=httpx.MockTransport(_metadata_handler))
    service = TradeAvailabilityService(
        store,
        [FakeAdapter("binance")],
        AsyncMock(),
        client,
    )

    transfer = await service.fetch_transfer_status(
        "BTCUSDT",
        exchange="binance",
        raw_symbol="BTCUSDT",
    )

    assert transfer.asset == "BTC"
    assert transfer.deposit_state == TransferAvailabilityState.PARTIAL
    assert transfer.withdraw_state == TransferAvailabilityState.ENABLED
    assert len(transfer.networks) == 2
    await service.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_spot_transfer_failure_is_unknown_without_dropping_trade_diagnostics() -> None:
    def failing_transfer_handler(request: httpx.Request) -> httpx.Response:
        if "/spot/currencies/BTC" in str(request.url):
            return httpx.Response(503, json={"message": "maintenance"})
        return _metadata_handler(request)

    snapshot = _snapshot("gate", MarketType.SPOT, "BTC_USDT")
    store = SnapshotStore()
    store.set_all_markets([snapshot])
    client = httpx.AsyncClient(transport=httpx.MockTransport(failing_transfer_handler))
    service = TradeAvailabilityService(store, [FakeAdapter("gate")], AsyncMock(), client)

    result = await service.fetch_status("BTCUSDT")

    assert len(result.markets) == 1
    market = result.markets[0]
    assert market.buy_open.state == TradeAvailabilityState.AVAILABLE
    assert market.spot_transfer is not None
    assert market.spot_transfer.publicly_queryable is True
    assert market.spot_transfer.deposit_state == TransferAvailabilityState.UNKNOWN
    assert market.spot_transfer.all_enabled is None
    assert "现货充提状态请求失败" in (market.spot_transfer.error or "")
    assert "gate:spot:BTC_USDT" in result.errors
    await service.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_hyperliquid_keeps_dex_and_raw_symbol_in_unified_status() -> None:
    snapshot = _snapshot("hyperliquid", MarketType.FUTURE, "xyz:BTC")
    store = SnapshotStore()
    store.set_all_markets([snapshot])
    action = HyperliquidTradeActionStatus(
        state=HyperliquidActionState.BLOCKED,
        reason_code="OPEN_INTEREST_CAP",
        reason="cap",
    )
    reduce_action = HyperliquidTradeActionStatus(
        state=HyperliquidActionState.CONDITIONAL,
        reason_code="REDUCE_ONLY_REQUIRES_POSITION",
        reason="position",
        executable_price=101,
        depth_1pct_usdt=1000,
    )
    hl_market = HyperliquidMarketTradeStatus(
        symbol="BTCUSDT",
        dex="xyz",
        raw_symbol="xyz:BTC",
        observed_at=datetime.now(UTC),
        at_open_interest_cap=True,
        best_bid=100,
        best_ask=101,
        bid_depth_1pct_usdt=900,
        ask_depth_1pct_usdt=1000,
        volume_24h_usdt=1_000_000,
        funding_rate_pct=0.01,
        buy_open=action,
        sell_open=action,
        buy_reduce_only=reduce_action,
        sell_reduce_only=reduce_action,
    )
    hyperliquid = AsyncMock()
    hyperliquid.fetch_status.return_value = HyperliquidTradeStatusResult(
        query="BTCUSDT",
        observed_at=datetime.now(UTC),
        markets=[hl_market],
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(_metadata_handler))
    service = TradeAvailabilityService(store, [], hyperliquid, client)

    result = await service.fetch_status("BTCUSDT")

    assert result.markets[0].exchange == "hyperliquid"
    assert result.markets[0].dex == "xyz"
    assert result.markets[0].raw_symbol == "xyz:BTC"
    assert result.markets[0].buy_open.state == TradeAvailabilityState.BLOCKED
    hyperliquid.fetch_status.assert_awaited_once_with(
        "BTCUSDT", dex="xyz", raw_symbol="xyz:BTC"
    )
    await service.aclose()
    await client.aclose()


def _available_market(state: TradeAvailabilityState) -> MarketTradeAvailability:
    open_action = TradeActionStatus(
        state=state,
        reason_code="PUBLIC_MARKET_AVAILABLE",
        reason="test",
        executable_price=101 if state == TradeAvailabilityState.AVAILABLE else None,
        depth_1pct_usdt=1_000 if state == TradeAvailabilityState.AVAILABLE else None,
    )
    reduce_action = TradeActionStatus(
        state=TradeAvailabilityState.CONDITIONAL,
        reason_code="REDUCE_ONLY_REQUIRES_POSITION",
        reason="test",
        executable_price=101,
        depth_1pct_usdt=1_000,
    )
    now = datetime.now(UTC)
    return MarketTradeAvailability(
        exchange="okx",
        market_type=MarketType.FUTURE,
        symbol="BTCUSDT",
        raw_symbol="BTC-USDT-SWAP",
        coverage_tier="core",
        observed_at=now,
        market_data_updated_at=now,
        orderbook_updated_at=now,
        orderbook_source="test",
        public_status_code="live",
        public_status_source="test",
        best_bid=100,
        best_ask=101,
        volume_24h_usdt=1_000_000,
        funding_rate_pct=0.01,
        funding_interval_hours=8,
        buy_open=open_action,
        sell_open=open_action,
        buy_reduce_only=reduce_action,
        sell_reduce_only=reduce_action,
    )


@pytest.mark.asyncio
async def test_monitor_notifies_once_on_unavailable_to_available_transition() -> None:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await initialize_schema(db)
    repository = TradeAvailabilityWatchRepository(db)
    now = datetime.now(UTC)
    await repository.upsert(TradeAvailabilityWatch(
        symbol="BTCUSDT",
        exchange="okx",
        market_type=MarketType.FUTURE,
        raw_symbol="BTC-USDT-SWAP",
        last_buy_state=TradeAvailabilityState.BLOCKED,
        last_sell_state=TradeAvailabilityState.UNKNOWN,
        created_at=now,
        updated_at=now,
    ))
    service = AsyncMock()
    service.fetch_status.return_value = TradeAvailabilityResult(
        query="BTCUSDT",
        observed_at=now,
        markets=[_available_market(TradeAvailabilityState.AVAILABLE)],
    )
    sender = AsyncMock()
    monitor = TradeAvailabilityMonitor(repository, service, sender)

    first = await monitor.check_once()
    second = await monitor.check_once()

    assert first[0].recovered_sides == ["buy", "sell"]
    assert second == []
    sender.assert_awaited_once()
    message = sender.await_args.args[0]
    assert "okx / future / BTC-USDT-SWAP" in message
    assert "手续费：未计入" in message
    await db.close()


@pytest.mark.asyncio
async def test_monitor_retries_recovery_when_feishu_send_fails() -> None:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await initialize_schema(db)
    repository = TradeAvailabilityWatchRepository(db)
    now = datetime.now(UTC)
    await repository.upsert(TradeAvailabilityWatch(
        symbol="BTCUSDT",
        exchange="okx",
        market_type=MarketType.FUTURE,
        raw_symbol="BTC-USDT-SWAP",
        last_buy_state=TradeAvailabilityState.BLOCKED,
        last_sell_state=TradeAvailabilityState.BLOCKED,
        created_at=now,
        updated_at=now,
    ))
    service = AsyncMock()
    service.fetch_status.return_value = TradeAvailabilityResult(
        query="BTCUSDT",
        observed_at=now,
        markets=[_available_market(TradeAvailabilityState.AVAILABLE)],
    )
    sender = AsyncMock(side_effect=RuntimeError("webhook unavailable"))
    monitor = TradeAvailabilityMonitor(repository, service, sender)

    assert await monitor.check_once() == []
    failed = (await repository.list())[0]
    assert failed.last_buy_state == TradeAvailabilityState.BLOCKED
    assert "webhook unavailable" in (failed.last_error or "")

    sender.side_effect = None
    retried = await monitor.check_once()
    assert retried[0].recovered_sides == ["buy", "sell"]
    assert (await repository.list())[0].last_buy_state == TradeAvailabilityState.AVAILABLE
    await db.close()


def test_trade_status_routes_return_exact_market_and_persist_watch() -> None:
    app = create_app(
        settings=Settings(database_url="sqlite:///:memory:"),
        start_background_workers=False,
    )
    now = datetime.now(UTC)
    result = TradeAvailabilityResult(
        query="BTCUSDT",
        observed_at=now,
        markets=[_available_market(TradeAvailabilityState.AVAILABLE)],
    )
    service = AsyncMock()
    service.fetch_status.return_value = result

    with TestClient(app) as client:
        original = app.state.trade_availability_service
        app.state.trade_availability_service = service
        response = client.get("/api/trade-status/BTCUSDT")
        created = client.post("/api/trade-status/watches", json={
            "symbol": "BTCUSDT",
            "exchange": "okx",
            "market_type": "future",
            "raw_symbol": "BTC-USDT-SWAP",
        })
        watches = client.get("/api/trade-status/watches/list")
        deleted = client.delete(f"/api/trade-status/watches/{created.json()['id']}")
        app.state.trade_availability_service = original

    assert response.status_code == 200
    assert response.json()["markets"][0]["raw_symbol"] == "BTC-USDT-SWAP"
    assert created.status_code == 200
    assert created.json()["last_buy_state"] == "available"
    assert len(watches.json()) == 1
    assert deleted.status_code == 200
