import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from test_instrument_statistics import market

from app.api.routes_instruments import router
from app.models.instrument import InstrumentStatisticsResult
from app.models.market import MarketSnapshot
from app.models.settings import RiskSettings
from app.services.snapshot_store import SnapshotStore


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_state", ["blocked", "error", "ready"])
async def test_statistics_independent_of_astro_but_basic_lookup_preserves_routes(sdk_state):
    now = datetime.now(UTC)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.snapshot_store = SnapshotStore()
    app.state.snapshot_store.set_all_markets([
        MarketSnapshot.model_validate(market("binance", timestamp=now).model_dump())
    ])
    entered = asyncio.Event()
    released = asyncio.Event()

    async def list_pairs():
        entered.set()
        if sdk_state == "blocked":
            await released.wait()
        elif sdk_state == "error":
            raise RuntimeError("SDK unavailable")
        return [{"id": "test", "name": "BTC", "type": "FF",
                 "buyEx": "binance", "sellEx": "okx"}]

    app.state.astro_client = SimpleNamespace(list_pairs=AsyncMock(side_effect=list_pairs))
    service = AsyncMock()
    service.lookup.return_value = InstrumentStatisticsResult(symbol="BTCUSDT", observed_at=now)
    app.state.instrument_statistics_service = service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        stats = await asyncio.wait_for(client.get("/api/instruments/BTC/statistics"), timeout=1)
        assert stats.status_code == 200
        app.state.astro_client.list_pairs.assert_not_awaited()
        target = service.lookup.await_args.args[0]
        assert target.symbol == "BTCUSDT"
        assert target.markets[0].raw_symbol == "BTCUSDT"
        basic_task = asyncio.create_task(client.get("/api/instruments/BTC"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            if sdk_state == "blocked":
                assert not basic_task.done()
            released.set()
            basic = await basic_task
        finally:
            released.set()
            if not basic_task.done():
                basic_task.cancel()
                await asyncio.gather(basic_task, return_exceptions=True)
        assert basic.status_code == 200
        app.state.astro_client.list_pairs.assert_awaited_once()
        if sdk_state == "error":
            assert "SDK unavailable" in basic.json()["route_errors"]["astro"]
        else:
            assert any(route["status"] == "live_market" for route in basic.json()["astro_routes"])


@pytest.mark.asyncio
async def test_history_pair_universe_survives_stale_books_but_respects_identity_and_exchange_filters():
    now = datetime.now(UTC)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.snapshot_store = SnapshotStore()
    app.state.settings_repo = SimpleNamespace(get_risk_settings=AsyncMock(
        return_value=RiskSettings(ignored_exchanges=["gate"])))
    app.state.snapshot_store.set_all_markets([
        MarketSnapshot.model_validate(market(exchange, timestamp=now - timedelta(minutes=2),
            identity_status="conflict" if exchange == "aster" else "verified").model_dump())
        for exchange in ("binance", "okx", "bybit", "aster", "gate")
    ])
    service = AsyncMock()
    service.lookup.return_value = InstrumentStatisticsResult(symbol="BTCUSDT", observed_at=now)
    app.state.instrument_statistics_service = service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        basic = await client.get("/api/instruments/BTC")
        assert basic.status_code == 200
        assert basic.json()["spreads"] == []  # Live routes still require fresh books.
        response = await client.get("/api/instruments/BTC/statistics")
    assert response.status_code == 200
    target = service.lookup.await_args.args[0]
    assert len(target.spreads) == 3
    assert all({row.buy_exchange, row.sell_exchange} <= {"binance", "okx", "bybit"}
               for row in target.spreads)


@pytest.mark.asyncio
async def test_historical_statistics_still_require_selection_for_different_assets():
    now = datetime.now(UTC)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.snapshot_store = SnapshotStore()
    app.state.snapshot_store.set_all_markets([
        MarketSnapshot.model_validate(market(exchange, timestamp=now - timedelta(minutes=2),
            asset_id=asset_id).model_dump())
        for exchange, asset_id in (("binance", "asset.a"), ("okx", "asset.b"))
    ])
    app.state.instrument_statistics_service = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/instruments/BTC/statistics")
    assert response.status_code == 409
    app.state.instrument_statistics_service.lookup.assert_not_awaited()
