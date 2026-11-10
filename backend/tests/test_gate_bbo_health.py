"""Offline coverage of the authenticated Gate BBO health summary."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from auth_helpers import TEST_TOTP_SECRET, IsolatedSessionLookup, seed_sessions, session_headers

from app.core.config import Settings
from app.main import create_app
from app.services.gate_bbo_runtime import GateBboRuntime


@pytest.fixture
def app():
    instance = create_app(
        settings=Settings(
            environment="production",
            dashboard_password="test-password",
            dashboard_totp_secret=TEST_TOTP_SECRET,
            dashboard_api_token="b" * 64,
            database_url="sqlite:///:memory:",
        ),
        start_collector=False,
        start_background_workers=False,
    )
    instance.state.auth_store = IsolatedSessionLookup()
    return instance


@pytest.fixture
async def client(app):
    # ASGITransport skips lifespan, so route-only tests never start sockets or workers.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as transport:
        yield transport


@pytest.mark.parametrize("credential", ["session", "api_token"])
async def test_health_exposes_live_runtime_summary(app, client, monkeypatch, credential):
    runtime = GateBboRuntime(replace(app.state.settings, gate_bbo_enabled=True))
    monkeypatch.setattr("app.services.gate_bbo_runtime.monotonic", lambda: 100)
    runtime._process = SimpleNamespace(pid=4321, is_alive=lambda: True)
    runtime.set_subscriptions({"spot": ["AKE_USDT", "BTC_USDT"],
                               "future": ["AKE_USDT", "JCT_USDT", "BTC_USDT"]})
    runtime._connections = {
        kind: {"connected": True, "last_message_at": 99, "subscribed": count,
               "messages": 100, "messages_per_second": 50.0, "reconnects": 1}
        for kind, count in (("spot", 2), ("future", 3))
    }
    runtime._rss, runtime._cpu = 32.5, 1.25
    runtime.batches, runtime.batch_size, runtime.restarts = 9, 5, 2
    runtime.record_merge(True)
    runtime.record_merge(False)
    runtime.record_merge(False)
    app.state.gate_bbo_runtime = runtime
    expected = runtime.status()
    headers = (session_headers("test-password") if credential == "session"
               else {"X-API-Token": "b" * 64})

    response = await client.get("/api/health", headers=headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "status": "ok", "markets": 0, "opportunities": 0,
        "exchange_errors": {}, "exchange_states": {}, "gate_bbo": expected,
    }
    summary = response.json()["gate_bbo"]
    assert summary["enabled"] is True and summary["pid"] == 4321
    assert summary["merge_hits"] == 1 and summary["merge_fallbacks"] == 2
    assert summary["connections"]["spot"]["messages_per_second"] == 50
    assert summary["connections"]["future"]["requested"] == 3
    assert all(connection["live"] for connection in summary["connections"].values())
    assert runtime.status() == expected  # Reading health must not change counters.


@pytest.mark.parametrize("headers", [
    {}, session_headers("wrong"), {"X-API-Token": "invalid"},
    {"X-Dashboard-Password": "test-password"},
])
@pytest.mark.parametrize("development_bypass", [False, True])
async def test_public_health_never_reads_or_exposes_runtime(
    app, client, headers, development_bypass,
):
    if development_bypass:
        app.state.settings = replace(
            app.state.settings, environment="development", dashboard_auth_disabled=True,
        )
    status = Mock(side_effect=AssertionError("Anonymous health read private metrics"))
    app.state.gate_bbo_runtime = SimpleNamespace(status=status)

    response = await client.get("/api/health", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    status.assert_not_called()


@pytest.mark.parametrize("runtime_is_none", [False, True])
async def test_health_without_runtime_preserves_exact_legacy_fields(app, client, runtime_is_none):
    if runtime_is_none:
        app.state.gate_bbo_runtime = None
    response = await client.get("/api/health", headers=session_headers("test-password"))
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "markets": 0, "opportunities": 0,
        "exchange_errors": {}, "exchange_states": {},
    }


async def test_normal_lifespan_exposes_disabled_runtime_summary(app, client):
    async with app.router.lifespan_context(app):
        await seed_sessions(app.state.auth_store)
        runtime = app.state.gate_bbo_runtime
        runtime.record_merge(False)
        response = await client.get("/api/health", headers=session_headers("test-password"))
        assert response.status_code == 200
        assert response.json()["gate_bbo"] == runtime.status()
        summary = response.json()["gate_bbo"]
        assert summary["enabled"] is False
        assert summary["batch_quotes_executable"] is False
        assert summary["merge_fallbacks"] == 1 and summary["merge_hits"] == 0
        assert summary["pid"] is None and runtime._process is None
        assert all(not connection["live"] for connection in summary["connections"].values())

        anonymous = await client.get("/api/health")
        assert anonymous.json() == {"status": "ok"}
