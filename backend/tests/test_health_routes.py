import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import Settings
from app.main import create_app
from app.models.settings import RiskSettings
from app.runtime.supervisor import TaskSupervisor
from app.services import pair_spread_query
from app.services.collector import MarketCollector


@pytest.fixture
def app():
    # ASGITransport deliberately skips lifespan: no external workers or real database.
    return create_app(settings=Settings(dashboard_password="test-password"), start_collector=True)


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await app.state.task_supervisor.stop()


async def running_critical(app):
    entered = []

    async def worker():
        entered.append(True)
        await asyncio.Event().wait()

    for name in ("market-collector", "alert-loop"):
        app.state.task_supervisor.start(name, worker, restart=True, critical=True)
    while len(entered) < 2:
        await asyncio.sleep(0)


async def test_legacy_health_exact_fields_and_filters_are_preserved(app, client):
    now = datetime.now(UTC)
    exchange_states = {"binance": {"status": "healthy", "last_success_at": now}}
    app.state.market_collector = SimpleNamespace(exchange_states=lambda: exchange_states)
    app.state.snapshot_store.set_exchange_errors({"binance:spot": "offline", "okx": "ignored"})
    app.state.settings_repo = SimpleNamespace(
        get_risk_settings=AsyncMock(return_value=RiskSettings(ignored_exchanges=["okx"])),
    )
    response = await client.get("/api/health")
    assert response.status_code == 200
    # Capture of the legacy contract; no supervision fields added to it.
    assert response.json() == {
        "status": "ok", "markets": 0, "opportunities": 0,
        "exchange_errors": {"binance:spot": "offline"},
        "exchange_states": {"binance": {"status": "healthy", "last_success_at": now.isoformat().replace("+00:00", "Z")}},
    }


async def test_missing_and_stalled_generation_make_ready_fail(app, client, monkeypatch):
    await running_critical(app)
    assert (await client.get("/api/health/ready")).status_code == 503
    now = datetime.now(UTC)
    app.state.snapshot_store.mark_published(now)
    generation = app.state.snapshot_store.generation
    assert (await client.get("/api/health/ready")).status_code == 200

    class Clock:
        @staticmethod
        def now(_):
            return now + timedelta(seconds=3 * app.state.settings.poll_interval_seconds + 30)

    monkeypatch.setattr("app.api.routes_health.datetime", Clock)
    assert app.state.snapshot_store.generation == generation
    assert (await client.get("/api/health/ready")).status_code == 503
    assert (await client.get("/api/health/live")).status_code == 200
    app.state.snapshot_store.mark_published(Clock.now(UTC))
    assert (await client.get("/api/health/ready")).status_code == 200


@pytest.mark.parametrize("task_state", ["stopped", "failed", "backoff"])
async def test_non_running_critical_task_makes_ready_fail(app, client, task_state):
    await running_critical(app)
    app.state.snapshot_store.mark_published()
    app.state.task_supervisor.statuses["alert-loop"].state = task_state
    assert (await client.get("/api/health/ready")).status_code == 503


async def test_single_exchange_failure_does_not_affect_ready(app, client):
    await running_critical(app)
    failed = SimpleNamespace(
        name="broken", fetch_spot_tickers=AsyncMock(side_effect=RuntimeError("offline")),
        fetch_future_tickers=AsyncMock(side_effect=RuntimeError("offline")),
    )
    healthy = SimpleNamespace(
        name="healthy", fetch_spot_tickers=AsyncMock(return_value=[]),
        fetch_future_tickers=AsyncMock(return_value=[]),
    )
    collector = MarketCollector([failed, healthy], app.state.snapshot_store)
    app.state.market_collector = collector
    await collector.collect_once()
    assert app.state.snapshot_store.get_exchange_errors()
    assert (await client.get("/api/health/ready")).status_code == 200
    detail = (await client.get("/api/health/detail", headers={"X-Dashboard-Password": "test-password"})).json()
    assert detail["exchanges"]["broken"]["last_success_at"] is None
    assert detail["exchanges"]["broken"]["data_age_seconds"] is None
    assert detail["exchanges"]["healthy"]["data_age_seconds"] >= 0


async def test_api_only_ready_does_not_require_collector(app, client):
    app.state.start_collector = False
    assert not app.state.task_supervisor.tasks
    assert (await client.get("/api/health/ready")).status_code == 200


async def test_detail_password_metrics_and_nested_cache_counts(app, client, monkeypatch):
    assert (await client.get("/api/health/detail")).status_code == 401
    assert (await client.get("/api/health/detail", headers={"X-Dashboard-Password": "wrong"})).status_code == 401
    caches = {
        "_REALTIME_PAIR_SPREAD_CACHE": {"private-key": [None, None]},
        "_REALTIME_PAIR_FUNDING_CACHE": {"a": [None], "b": []},
        "_REALTIME_PAIR_OPEN_INTEREST_CACHE": {},
        "_REALTIME_SYMBOL_SPREAD_CACHE": {"symbol": {"one": [None], "two": [None, None]}},
    }
    for name, value in caches.items():
        monkeypatch.setattr(pair_spread_query, name, value)
    collector = MarketCollector([], app.state.snapshot_store)
    app.state.market_collector = collector
    await collector.collect_once()
    response = await client.get("/api/health/detail", headers={"X-Dashboard-Password": "test-password"})
    assert response.status_code == 200
    data = response.json()
    assert data["snapshot"]["generation"] == 1
    assert data["collector_timings"]["stages"]["publish"]["samples"] == 1
    assert data["event_loop_lag"]["window_seconds"] == 300
    assert data["realtime_caches"] == {
        "_REALTIME_PAIR_SPREAD_CACHE": {"keys": 1, "points": 2},
        "_REALTIME_PAIR_FUNDING_CACHE": {"keys": 2, "points": 1},
        "_REALTIME_PAIR_OPEN_INTEREST_CACHE": {"keys": 0, "points": 0},
        "_REALTIME_SYMBOL_SPREAD_CACHE": {"keys": 1, "points": 3},
    }
    assert "private-key" not in response.text
    assert "private-key" in pair_spread_query._REALTIME_PAIR_SPREAD_CACHE


async def test_negative_basis_failure_visible_in_detail_then_recovers(app, client):
    app.state.task_supervisor = TaskSupervisor(initial_backoff=0.1, max_backoff=0.2)
    calls = 0

    async def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("secret")
        await asyncio.Event().wait()

    app.state.task_supervisor.start("negative-basis-monitor", fail_once, restart=True, critical=False)
    await asyncio.sleep(0)
    response = await client.get("/api/health/detail", headers={"X-Dashboard-Password": "test-password"})
    assert response.json()["tasks"]["negative-basis-monitor"]["state"] == "failed"
    assert "secret" not in response.text
    async with asyncio.timeout(1):
        while calls < 2:
            await asyncio.sleep(0.001)
    response = await client.get("/api/health/detail", headers={"X-Dashboard-Password": "test-password"})
    assert response.json()["tasks"]["negative-basis-monitor"]["state"] == "running"
    assert response.json()["tasks"]["negative-basis-monitor"]["restarts"] == 1
