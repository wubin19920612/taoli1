import asyncio

import httpx
import pytest

from app.models.opportunity import Opportunity
from app.services.gate_crossex_hyperliquid import SYMBOLS_URL, GateCrossExHyperliquidCatalog


def record(coin="NET", dex="xyz", **updates):
    return {
        "symbol": f"HYPERLIQUID_FUTURE_{coin}_USDC",
        "exchange_type": "HYPERLIQUID", "business_type": "FUTURE",
        "state": "live", "tag": dex, "support_cross": "false", **updates,
    }


@pytest.mark.asyncio
async def test_same_ticker_requires_exact_dex_and_isolated_markets_are_supported():
    calls = []

    async def fetch(symbol):
        calls.append(symbol)
        return [record()]

    catalog = GateCrossExHyperliquidCatalog(fetch)
    assert not await catalog.supports_market("para:NET")
    assert not await catalog.supports_market("io:NET")
    assert await catalog.supports_market("xyz:NET")
    assert not await catalog.supports_market("NET")
    assert calls == ["HYPERLIQUID_FUTURE_NET_USDC"]


@pytest.mark.asyncio
@pytest.mark.parametrize("records", [
    [], [record(state="suspend")], [record(coin="OTHER")], [record(exchange_type="GATE")],
    [record(business_type="SPOT")], [record(tag=None)],
    [record(), record(dex="para")], {"error": "unavailable"}, ["invalid"],
])
async def test_unlisted_suspended_malformed_or_ambiguous_symbols_are_unconfirmed(records):
    async def fetch(symbol):
        return records

    assert not await GateCrossExHyperliquidCatalog(fetch).supports_market("xyz:NET")


@pytest.mark.asyncio
async def test_expired_support_is_not_reused_after_fetch_failure_and_lookup_recovers():
    now = [0.0]
    calls = []

    async def fetch(symbol):
        calls.append(symbol)
        if len(calls) == 2:
            raise httpx.ReadTimeout("timeout")
        return [record()]

    catalog = GateCrossExHyperliquidCatalog(fetch, clock=lambda: now[0])
    assert await catalog.supports_market("xyz:NET")
    now[0] = 59
    assert await catalog.supports_market("xyz:NET")
    now[0] = 60
    assert not await catalog.supports_market("xyz:NET")
    now[0] = 74
    assert not await catalog.supports_market("xyz:NET")
    now[0] = 75
    assert await catalog.supports_market("xyz:NET")
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_cache_refresh_observes_delisting_and_dex_changes():
    now = [0.0]
    rows = [record()]

    async def fetch(symbol):
        return rows

    catalog = GateCrossExHyperliquidCatalog(fetch, clock=lambda: now[0])
    assert await catalog.supports_market("xyz:NET")
    rows[0] = record(state="suspend")
    now[0] = 60
    assert not await catalog.supports_market("xyz:NET")
    rows[0] = record(dex="para")
    now[0] = 120
    assert not await catalog.supports_market("xyz:NET")
    assert await catalog.supports_market("para:NET")


@pytest.mark.asyncio
async def test_outage_cooldown_also_prevents_repeated_fetches_for_other_coins():
    now = [0.0]
    calls = []

    async def fetch(symbol):
        calls.append(symbol)
        raise httpx.ConnectError("unavailable")

    catalog = GateCrossExHyperliquidCatalog(fetch, clock=lambda: now[0])
    assert not await catalog.supports_market("xyz:NET")
    assert not await catalog.supports_market("para:CIFR")
    assert len(calls) == 1
    now[0] = 15
    assert not await catalog.supports_market("para:CIFR")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_concurrent_lookups_share_one_refresh():
    calls = []

    async def fetch(symbol):
        calls.append(symbol)
        await asyncio.sleep(0)
        return [record()]

    catalog = GateCrossExHyperliquidCatalog(fetch)
    assert all(await asyncio.gather(*(catalog.supports_market("xyz:NET") for _ in range(6))))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_uses_raw_market_instead_of_canonical_alias_and_checks_both_legs():
    calls = []

    async def fetch(symbol):
        calls.append(symbol)
        return [record("ANTH", "io"), record("BTC", "")]

    catalog = GateCrossExHyperliquidCatalog(fetch)
    opportunity = Opportunity.model_construct(
        symbol="ANTHROPICUSDT", buy_exchange="hyperliquid", buy_raw_symbol="BTC",
        sell_exchange="hyperliquid", sell_raw_symbol="io:ANTH",
    )
    assert await catalog.supports_opportunity(opportunity)
    assert calls == ["HYPERLIQUID_FUTURE_BTC_USDC", "HYPERLIQUID_FUTURE_ANTH_USDC"]
    assert not await catalog.supports_opportunity(
        opportunity.model_copy(update={"sell_raw_symbol": "para:ANTH"})
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_symbol", [None, "", "para:", ":NET", "para:xyz:NET"])
async def test_missing_or_invalid_market_does_not_guess_or_fetch(raw_symbol):
    calls = []

    async def fetch(symbol):
        calls.append(symbol)
        return []

    assert not await GateCrossExHyperliquidCatalog(fetch).supports_market(raw_symbol)
    assert calls == []


@pytest.mark.asyncio
async def test_queries_the_public_gate_symbol_endpoint_without_credentials(monkeypatch):
    real_client = httpx.AsyncClient

    def handle(request):
        assert request.method == "GET"
        assert str(request.url).split("?", 1)[0] == SYMBOLS_URL
        assert request.url.params["symbols"] == "HYPERLIQUID_FUTURE_NET_USDC"
        assert "authorization" not in request.headers
        assert "key" not in request.headers
        return httpx.Response(200, json=[record()])

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    assert await GateCrossExHyperliquidCatalog().supports_market("xyz:NET")
