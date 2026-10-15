import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.instrument_market_cap import InstrumentMarketCapService, InvalidMarketCapSelection


@pytest.mark.asyncio
async def test_unique_symbol_uses_exact_coin_and_caches_search_and_market_cap() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"coins": [
                {"id": "bitcoin", "name": "Bitcoin", "symbol": "btc", "market_cap_rank": 1},
                {"id": "btc2", "name": "Wrong symbol", "symbol": "btc2"},
            ]})
        assert request.url.params["ids"] == "bitcoin"
        return httpx.Response(200, json=[{
            "id": "bitcoin", "market_cap": 2_000_000,
            "last_updated": "2026-09-25T01:00:00Z",
        }])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = InstrumentMarketCapService(client)
        first = await service.lookup("btc")
        second = await service.lookup("BTC")

    assert first.status == second.status == "available"
    assert first.market_cap_usd == 2_000_000
    assert first.selected_id == "bitcoin"
    assert first.updated_at.isoformat() == "2026-09-25T01:00:00+00:00"
    assert requests == ["/api/v3/search", "/api/v3/coins/markets"]


@pytest.mark.asyncio
async def test_ambiguous_symbol_requires_selected_coin_from_exact_candidates() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"coins": [
                {"id": "coin-a", "name": "Coin A", "symbol": "ABC", "market_cap_rank": 5},
                {"id": "coin-b", "name": "Coin B", "symbol": "abc", "market_cap_rank": 10},
            ]})
        return httpx.Response(200, json=[{"id": "coin-b", "market_cap": 500}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = InstrumentMarketCapService(client)
        ambiguous = await service.lookup("ABC")
        with pytest.raises(InvalidMarketCapSelection):
            await service.lookup("ABC", "another-coin")
        selected = await service.lookup("ABC", "coin-b")

    assert ambiguous.status == "ambiguous"
    assert ambiguous.market_cap_usd is None
    assert [coin.id for coin in ambiguous.candidates] == ["coin-a", "coin-b"]
    assert selected.status == "available"
    assert selected.selected_id == "coin-b"
    assert selected.market_cap_usd == 500
    assert requests == ["/api/v3/search", "/api/v3/coins/markets"]


@pytest.mark.asyncio
async def test_missing_market_cap_and_source_failure_do_not_create_a_ratio_denominator() -> None:
    def missing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"coins": [{"id": "abc", "name": "ABC", "symbol": "ABC"}]})
        return httpx.Response(200, json=[{"id": "abc", "market_cap": 0}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(missing)) as client:
        result = await InstrumentMarketCapService(client).lookup("ABC")
    assert result.status == "unavailable"
    assert result.market_cap_usd is None

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
        failed = await InstrumentMarketCapService(client).lookup("ABC")
    assert failed.status == "source_error"
    assert failed.market_cap_usd is None


def test_market_cap_route_validates_selection_without_affecting_instrument_lookup() -> None:
    class Service:
        async def lookup(self, base: str, coin_id: str | None = None):
            if coin_id:
                raise InvalidMarketCapSelection("Selected coin does not match the queried symbol")
            return {"base": base, "status": "not_found", "candidates": []}

        async def aclose(self):
            pass

    app = create_app(settings=Settings(database_url="sqlite:///:memory:"))
    app.state.instrument_market_cap_service = Service()
    with TestClient(app) as client:
        response = client.get("/api/instrument-market-cap/BTC")
        invalid = client.get("/api/instrument-market-cap/BTC?coin_id=wrong")
        instrument = client.get("/api/instruments/BTC")

    assert response.status_code == 200
    assert response.json()["status"] == "not_found"
    assert invalid.status_code == 422
    assert instrument.status_code == 200
