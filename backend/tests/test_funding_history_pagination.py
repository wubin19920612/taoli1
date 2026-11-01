import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes_pair_spread import router
from app.models.pair_spread import PairSpreadFundingPoint, PairSpreadKlinePoint
from app.services import funding_history_pagination as pagination
from app.services.pair_spread_query import PairSpreadQueryService

START = datetime(2026, 10, 1, tzinfo=UTC)


def parse(row):
    return PairSpreadFundingPoint(
        exchange="okx", symbol="BTCUSDT", funding_time=row["at"], funding_rate_pct=0.01,
    )


async def test_total_budget_shrinks_page_timeout_and_preserves_completed_pages(monkeypatch):
    elapsed = 0
    timeouts = []

    async def fetch(cursor, page):
        return [{"at": START + timedelta(hours=page)}]

    class PageTimeout:
        def __init__(self, timeout):
            self.timeout = timeout
            timeouts.append(timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            nonlocal elapsed
            elapsed += self.timeout
            if self.expired():
                raise TimeoutError

        def expired(self):
            return self.timeout < 20

    monkeypatch.setattr(pagination, "monotonic", lambda: elapsed)
    monkeypatch.setattr(pagination.asyncio, "timeout", PageTimeout)
    with pytest.raises(pagination.PartialFundingHistoryError, match="total timeout budget") as error:
        await pagination.collect_funding_pages(
            fetch, lambda row: row["at"], parse,
            start=START, end=START + timedelta(days=1), limit=1, direction="forward",
            total_timeout_seconds=45,
        )
    assert timeouts == [20, 20, 5]
    assert [p.funding_time for p in error.value.points] == [
        START + timedelta(hours=1), START + timedelta(hours=2),
    ]


async def test_budget_exhausted_between_pages_does_not_start_another_fetch(monkeypatch):
    elapsed = 0

    async def fetch(cursor, page):
        nonlocal elapsed
        elapsed = 45
        return [{"at": START}]

    fetch = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(pagination, "monotonic", lambda: elapsed)
    with pytest.raises(pagination.PartialFundingHistoryError, match="total timeout budget") as error:
        await pagination.collect_funding_pages(
            fetch, lambda row: row["at"], parse,
            start=START, end=START + timedelta(days=1), limit=1, direction="forward",
        )
    assert len(error.value.points) == 1
    fetch.assert_awaited_once()


@pytest.mark.parametrize("budget", [5, 45])
async def test_upstream_timeout_is_not_misreported_as_total_budget(monkeypatch, budget):
    monkeypatch.setattr(pagination, "monotonic", lambda: 0)
    fetch = AsyncMock(side_effect=TimeoutError("upstream page timeout"))
    with pytest.raises(pagination.PartialFundingHistoryError, match="upstream page timeout"):
        await pagination.collect_funding_pages(
            fetch, lambda row: row["at"], parse,
            start=START, end=START + timedelta(days=1), limit=1,
            total_timeout_seconds=budget,
        )
    fetch.assert_awaited_once()


async def test_external_cancellation_is_not_swallowed():
    fetch = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await pagination.collect_funding_pages(
            fetch, lambda row: row["at"], parse,
            start=START, end=START + timedelta(days=1), limit=1,
        )


@pytest.mark.parametrize("endpoint", ["query", "funding-history", "range-backtest"])
async def test_all_routes_keep_partial_records_and_warning_on_shared_budget(monkeypatch, endpoint):
    monkeypatch.setattr(pagination, "FUNDING_HISTORY_TOTAL_TIMEOUT_SECONDS", 0.02)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.settings = SimpleNamespace(dashboard_password="test-password")
    service = PairSpreadQueryService()
    app.state.pair_spread_query_service_factory = lambda: service
    times = [START + timedelta(minutes=i) for i in range(1, 101)]
    cancelled = asyncio.Event()
    calls = 0

    async def get_json(url):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"data": [{"fundingTime": int(at.timestamp() * 1000),
                              "realizedRate": "0.0001"} for at in times]}
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    service._get_json = get_json
    service._fetch_klines_with_warning = AsyncMock(return_value=[
        PairSpreadKlinePoint(bucket_at=START, close=100),
        PairSpreadKlinePoint(bucket_at=START + timedelta(days=1), close=101),
    ])
    service._fetch_current_with_warning = AsyncMock(return_value=None)
    service._query_open_interest = AsyncMock(
        return_value=([], ("unavailable", "unavailable", "unavailable")),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        async with asyncio.timeout(2):
            if endpoint == "range-backtest":
                response = await client.post("/api/pair-spread/range-backtest", json={
                    "leg1": {"exchange": "okx", "symbol": "BTCUSDT"},
                    "leg2": {"exchange": "binance", "symbol": "BTCUSDT", "market_type": "spot"},
                    "start_at": START.isoformat(),
                    "end_at": (START + timedelta(days=1)).isoformat(),
                    "direction": "long1_short2", "notional_usd": 1000,
                }, headers={"X-Dashboard-Password": "test-password"})
            else:
                response = await client.get(f"/api/pair-spread/{endpoint}", params={
                    "leg1_exchange": "okx", "leg1_symbol": "BTCUSDT",
                    "leg2_exchange": "binance", "leg2_symbol": "BTCUSDT",
                    "leg2_market_type": "spot", "hours": 24,
                    "start_at": START.isoformat(),
                    "end_at": (START + timedelta(days=1)).isoformat(),
                })
    assert response.status_code == 200, response.text
    body = response.json()
    records = body["funding_pnl"]["settlements"] if endpoint == "range-backtest" else body["funding_history"]
    assert len(records) == 100
    assert any("total timeout budget" in w for w in body["warnings"])
    assert calls == 2
    assert cancelled.is_set()
    assert service.client.is_closed
