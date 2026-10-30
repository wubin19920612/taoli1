import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes_astro import router as astro_router
from app.api.routes_instruments import router as instruments_router
from app.services.astro_client import AstroClientError, AstroSdkClient, AstroSdkConfig
from app.services.snapshot_store import SnapshotStore


@pytest.fixture
def sdk():
    client = AstroSdkClient(AstroSdkConfig("https://astro.example", "admin", "test-key"))
    client.list_pairs = AsyncMock(return_value=[{"name": "BTC", "nested": {"values": [1]}}])
    client._post = AsyncMock(return_value={"code": 0})
    return client


@pytest.mark.asyncio
async def test_cache_ttl_copies_and_realtime_bypass(sdk, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.services.astro_client.monotonic", lambda: clock[0])
    first = await sdk.list_pairs_cached()
    first[0]["nested"]["values"].append(2)
    first.append({"name": "ETH"})
    clock[0] += 9.99
    second = await sdk.list_pairs_cached()
    assert second == [{"name": "BTC", "nested": {"values": [1]}}]
    second[0]["name"] = "changed"
    assert (await sdk.list_pairs_cached())[0]["name"] == "BTC"
    sdk.list_pairs.assert_awaited_once()
    clock[0] = 110.0
    await sdk.list_pairs_cached()
    assert sdk.list_pairs.await_count == 2
    await sdk.list_pairs_cached(max_age_seconds=0)
    assert sdk.list_pairs.await_count == 3
    await sdk.list_pairs()
    assert sdk.list_pairs.await_count == 4


@pytest.mark.asyncio
async def test_empty_list_is_cached_and_age_starts_at_completion(sdk, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.services.astro_client.monotonic", lambda: clock[0])

    async def slow_list():
        clock[0] += 20
        return []

    sdk.list_pairs.side_effect = slow_list
    assert await sdk.list_pairs_cached() == []
    clock[0] += 9
    assert await sdk.list_pairs_cached() == []
    sdk.list_pairs.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_twenty_cold_readers_share_result_or_original_error(sdk, failure):
    entered, release = asyncio.Event(), asyncio.Event()
    error = AstroClientError("bad signature", 401)

    async def upstream():
        entered.set()
        await release.wait()
        if failure:
            raise error
        return [{"name": "BTC", "nested": {"values": [1]}}]

    sdk.list_pairs.side_effect = upstream
    readers = [asyncio.create_task(sdk.list_pairs_cached()) for _ in range(20)]
    await asyncio.wait_for(entered.wait(), 1)
    release.set()
    results = await asyncio.gather(*readers, return_exceptions=True)
    sdk.list_pairs.assert_awaited_once()
    if failure:
        assert all(result is error for result in results)
        sdk.list_pairs.side_effect = None
        await sdk.list_pairs_cached()
        assert sdk.list_pairs.await_count == 2
    else:
        results[0][0]["nested"]["values"].append(2)
        assert all(result[0]["nested"]["values"] == [1] for result in results[1:])
        assert (await sdk.list_pairs_cached())[0]["nested"]["values"] == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["add_pair", "update_pair"])
@pytest.mark.parametrize("outcome", ["success", "sdk_error", "timeout", "cancelled"])
async def test_every_write_invalidates_on_success_and_failure(sdk, method, outcome):
    await sdk.list_pairs_cached()
    if outcome == "sdk_error":
        sdk._post.return_value = {"code": 1, "message": "failed"}
    elif outcome == "timeout":
        sdk._post.side_effect = AstroClientError("timeout")
    elif outcome == "cancelled":
        sdk._post.side_effect = asyncio.CancelledError()
    if outcome == "success":
        await getattr(sdk, method)({"name": "BTC"})
    else:
        error_type = asyncio.CancelledError if outcome == "cancelled" else AstroClientError
        with pytest.raises(error_type):
            await getattr(sdk, method)({"name": "BTC"})
    await sdk.list_pairs_cached()
    assert sdk.list_pairs.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_write_detaches_old_read_and_prevents_stale_refill(sdk, failure):
    entered, release = asyncio.Event(), asyncio.Event()

    async def upstream():
        if sdk.list_pairs.await_count == 1:
            entered.set()
            await release.wait()
            return [{"name": "old"}]
        return [{"name": "new"}]

    sdk.list_pairs.side_effect = upstream
    old_reader = asyncio.create_task(sdk.list_pairs_cached())
    await asyncio.wait_for(entered.wait(), 1)
    if failure:
        sdk._post.side_effect = AstroClientError("timeout")
        with pytest.raises(AstroClientError):
            await sdk.add_pair({"name": "new"})
    else:
        await sdk.add_pair({"name": "new"})
    assert await asyncio.wait_for(sdk.list_pairs_cached(), 1) == [{"name": "new"}]
    release.set()
    assert await old_reader == [{"name": "old"}]
    assert await sdk.list_pairs_cached() == [{"name": "new"}]
    assert sdk.list_pairs.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_display_waiter_does_not_cancel_shared_read(sdk):
    entered, release = asyncio.Event(), asyncio.Event()

    async def upstream():
        entered.set()
        await release.wait()
        return []

    sdk.list_pairs.side_effect = upstream
    first = asyncio.create_task(sdk.list_pairs_cached())
    second = asyncio.create_task(sdk.list_pairs_cached())
    await asyncio.wait_for(entered.wait(), 1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == []
    assert await sdk.list_pairs_cached() == []
    sdk.list_pairs.assert_awaited_once()


def display_app(sdk):
    app = FastAPI()
    app.include_router(astro_router, prefix="/api")
    app.include_router(instruments_router, prefix="/api")
    app.state.astro_client = sdk
    app.state.settings = SimpleNamespace(dashboard_password="test-password")
    app.state.snapshot_store = SnapshotStore()
    return app


@pytest.mark.asyncio
async def test_display_routes_share_cache_and_observe_write_invalidation(sdk):
    app = display_app(sdk)
    sdk.list_pairs.return_value = [{"name": "BTC", "type": "FF", "buyEx": "binance", "sellEx": "okx"}]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        headers = {"X-Dashboard-Password": "test-password"}
        pairs, instrument = await asyncio.gather(
            client.get("/api/astro/pairs", headers=headers),
            client.get("/api/instruments/BTC"),
        )
        assert pairs.status_code == instrument.status_code == 200
        assert pairs.json() == sdk.list_pairs.return_value
        assert len(instrument.json()["astro_routes"]) == 2
        sdk.list_pairs.assert_awaited_once()
        await sdk.update_pair({"name": "BTC", "status": False})
        sdk.list_pairs.return_value = []
        refreshed = await client.get("/api/instruments/BTC")
        assert refreshed.json()["astro_routes"] == []
        assert (await client.get("/api/astro/pairs", headers=headers)).json() == []
        assert sdk.list_pairs.await_count == 2


@pytest.mark.asyncio
async def test_display_auth_and_upstream_401_are_preserved_without_caching():
    upstream_calls = []

    async def handler(request):
        upstream_calls.append(request)
        return httpx.Response(401, text="bad signature")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        sdk = AstroSdkClient(AstroSdkConfig("https://astro.example", "admin", "test-key"), upstream)
        app = display_app(sdk)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/api/astro/pairs")).status_code == 401
            assert not upstream_calls
            headers = {"X-Dashboard-Password": "test-password"}
            for _ in range(2):
                response = await client.get("/api/astro/pairs", headers=headers)
                assert response.status_code == 401
                assert response.json()["detail"] == "Astro HTTP 401: bad signature"
            instrument = await client.get("/api/instruments/BTC")
            assert instrument.status_code == 200
            assert "Astro HTTP 401: bad signature" in instrument.json()["route_errors"]["astro"]
            assert len(upstream_calls) == 3
