"""Offline regressions for PM decisions 1-6; credentials never enter snapshots."""
import asyncio
import ipaddress
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.requests import Request

from app.api import routes_health, stream
from app.core.config import Settings, get_settings
from app.core.security import client_ip, require_authentication
from app.core.totp import totp_at
from app.db.auth import AuthStore, LoginResult
from app.main import create_app
from app.services.snapshot_store import SnapshotStore
from auth_helpers import TEST_TOTP_SECRET
from test_login_2fa import NOW, context, sign_in

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("changing", [False, True], ids=["idle", "active"])
@pytest.mark.parametrize("cause", ["logout", "revoke_all", "expiry", "token_rotation", "token_disabled",
                                       "config_removed", "storage_failure"])
async def test_established_sse_closes_after_auth_invalidated(context, monkeypatch, caplog, cause, changing):
    app, client, store = context
    clock = [NOW]
    monkeypatch.setattr("app.core.security.time", SimpleNamespace(time=lambda: clock[0]))
    assert (await sign_in(app, client)).status_code == 200
    token = client.cookies.get("taoli1_session")
    headers = [(b"cookie", ("taoli1_session=" + token).encode())]
    if cause.startswith("token_"):
        headers = [(b"x-api-token", app.state.settings.dashboard_api_token.encode())]
    if cause == "expiry":
        await store.db.execute("UPDATE auth_sessions SET expires_at = ?", (NOW + 2,))
        await store.db.commit()
    app.state.snapshot_store = SnapshotStore()
    request = Request({"type": "http", "method": "GET", "path": "/api/stream", "app": app,
                       "headers": headers}, receive=AsyncMock(return_value={"type": "http.request"}))
    await require_authentication(request)
    response = await stream.stream(request)
    body = response.body_iterator
    assert (await anext(body)).startswith("event: snapshot\n")

    async def tick(seconds):
        clock[0] += seconds
        assert clock[0] - NOW <= 30, "SSE failed to close within the authorization deadline"
        if cause in {"logout", "revoke_all"}:
            path = "/api/auth/logout" if cause == "logout" else "/api/auth/sessions/revoke-all"
            assert (await client.post(path)).status_code == 200
        elif cause.startswith("token_"):
            app.state.settings = replace(app.state.settings, dashboard_api_token="b" * 64 if cause == "token_rotation" else "")
        elif cause == "config_removed":
            app.state.settings = replace(app.state.settings, dashboard_totp_secret="")
        elif cause == "storage_failure":
            monkeypatch.setattr(store, "authenticate", AsyncMock(side_effect=RuntimeError("private-storage-diagnostic")))
        if changing:
            app.state.snapshot_store.set_exchange_errors({"example": "changed"})

    monkeypatch.setattr(stream, "asyncio", SimpleNamespace(sleep=tick))
    with pytest.raises(StopAsyncIteration):
        await anext(body)
    assert 0 < clock[0] - NOW <= 30
    assert "private-storage-diagnostic" not in caplog.text
    assert all(value not in caplog.text for value in (
        token, app.state.settings.dashboard_password, TEST_TOTP_SECRET,
    ))


async def test_sse_valid_credentials_rechecked_before_every_changed_snapshot(context, monkeypatch):
    app, client, store = context
    assert (await sign_in(app, client)).status_code == 200
    app.state.snapshot_store = SnapshotStore()
    request = Request({"type": "http", "app": app, "headers": [
        (b"cookie", ("taoli1_session=" + client.cookies.get("taoli1_session")).encode()),
    ]}, receive=AsyncMock(return_value={"type": "http.request"}))
    check = AsyncMock(wraps=store.authenticate)
    monkeypatch.setattr(store, "authenticate", check)

    async def tick(_):
        app.state.snapshot_store.set_exchange_errors({"example": "changed"})

    monkeypatch.setattr(stream, "asyncio", SimpleNamespace(sleep=tick))
    body = (await stream.stream(request)).body_iterator
    try:
        await anext(body)
        await anext(body)
        assert check.await_count == 2
    finally:
        await body.aclose()


def test_proxy_defaults_match_fixed_compose_network(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    get_settings.cache_clear()
    try:
        assert Settings().trusted_proxy_ips == get_settings().trusted_proxy_ips == "172.18.0.0/24"
    finally:
        get_settings.cache_clear()
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"networks:\s+default:\s+ipam:\s+config:\s+- subnet: 172\.18\.0\.0/24\s+gateway: 172\.18\.0\.1", compose)
    assert '"127.0.0.1:${FRONTEND_PORT:-3000}:80"' in compose
    assert '"127.0.0.1:${BACKEND_PORT:-8000}:8000"' in compose
    assert "--no-proxy-headers" in (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")


@pytest.mark.parametrize("actual", ["198.51.100.42", "2001:db8::42"])
@pytest.mark.parametrize("forged", ["192.0.2.99", "192.0.2.99, 172.18.0.1", "invalid"])
def test_double_proxy_chain_overwrites_client_xff(actual, forged):
    # Bind this offline header-chain model to both checked-in nginx configurations.
    host = (ROOT / "deploy/nginx-https.conf.example").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
    for config, count in ((host, 1), (frontend, 2)):
        values = re.findall(r"proxy_set_header\s+X-Forwarded-For\s+([^;]+);", config)
        assert values == ["$remote_addr"] * count
        assert "$proxy_add_x_forwarded_for" not in config
    assert re.findall(r"set_real_ip_from\s+([^;]+);", host) == []
    trusted = re.findall(r"set_real_ip_from\s+([^;]+);", frontend)
    assert trusted == ["172.18.0.1/32"]
    assert "real_ip_header X-Forwarded-For;" in frontend
    assert "real_ip_recursive on;" in frontend
    client_headers = {"x-forwarded-for": forged}
    host_headers = {**client_headers, "x-forwarded-for": actual}
    gateway = "172.18.0.1"
    frontend_remote = host_headers["x-forwarded-for"] if any(
        ipaddress.ip_address(gateway) in ipaddress.ip_network(net) for net in trusted
    ) else gateway
    app = SimpleNamespace(state=SimpleNamespace(settings=Settings()))
    request = Request({"type": "http", "app": app, "client": ("172.18.0.2", 123),
                       "headers": [(b"x-forwarded-for", frontend_remote.encode())]})
    assert client_ip(request) == actual
    # A direct untrusted client cannot use XFF to impersonate another IP.
    request.scope["client"] = (actual, 123)
    request.scope["headers"] = [(b"x-forwarded-for", forged.encode())]
    assert client_ip(Request(request.scope)) == actual


@pytest.mark.parametrize("mode", ["anonymous", "old_header", "bad_cookie", "bad_token", "expired", "revoked",
                                     "missing_config", "storage_failure", "development_bypass"])
async def test_health_public_representation_is_exactly_status(context, monkeypatch, caplog, mode):
    app, client, store = context
    app.include_router(routes_health.router, prefix="/api")
    app.state.snapshot_store = SnapshotStore()
    app.state.snapshot_store.set_exchange_errors({"example": "internal-collector-diagnostic"})
    # The unauthenticated path must not even invoke diagnostics or settings retrieval.
    app.state.settings_repo = SimpleNamespace(get_risk_settings=AsyncMock(side_effect=AssertionError("diagnostics accessed")))
    headers = {}
    if mode in {"expired", "revoked", "missing_config", "storage_failure"}:
        assert (await sign_in(app, client)).status_code == 200
    if mode == "old_header":
        headers = {"X-Dashboard-Password": app.state.settings.dashboard_password}
    elif mode == "bad_cookie":
        headers = {"Cookie": "taoli1_session=invalid"}
    elif mode == "bad_token":
        headers = {"X-Api-Token": "invalid"}
    elif mode == "expired":
        await store.db.execute("UPDATE auth_sessions SET expires_at = ?", (NOW,))
        await store.db.commit()
    elif mode == "revoked":
        await store.revoke(None, NOW, all_sessions=True)
    elif mode == "missing_config":
        app.state.settings = replace(app.state.settings, dashboard_totp_secret="")
        headers = {"X-Api-Token": app.state.settings.dashboard_api_token}
    elif mode == "storage_failure":
        monkeypatch.setattr(store, "authenticate", AsyncMock(side_effect=RuntimeError("internal-auth-diagnostic")))
    elif mode == "development_bypass":
        app.state.settings = replace(app.state.settings, environment="test", dashboard_auth_disabled=True)
    result = await client.get("/api/health", headers=headers)
    assert result.status_code == 200 and result.json() == {"status": "ok"}
    assert result.headers["cache-control"] == "no-store"
    assert "internal-auth-diagnostic" not in caplog.text


@pytest.mark.parametrize("credential", ["session", "api_token"])
async def test_health_authenticated_representation_retains_diagnostics(context, credential):
    app, client, _ = context
    app.include_router(routes_health.router, prefix="/api")
    app.state.snapshot_store = SnapshotStore()
    app.state.snapshot_store.set_exchange_errors({"example": "offline"})
    app.state.market_collector = SimpleNamespace(exchange_states=lambda: {"example": {"status": "failed"}})
    headers = {"X-Api-Token": app.state.settings.dashboard_api_token} if credential == "api_token" else {}
    if credential == "session":
        assert (await sign_in(app, client)).status_code == 200
    result = await client.get("/api/health", headers=headers)
    assert result.status_code == 200
    assert result.json() == {"status": "ok", "markets": 0, "opportunities": 0,
                             "exchange_errors": {"example": "offline"},
                             "exchange_states": {"example": {"status": "failed"}}}
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("ready", [False, True])
async def test_public_liveness_readiness_have_minimal_fields(context, ready):
    app, client, _ = context
    app.include_router(routes_health.router, prefix="/api")
    app.state.start_collector = True
    app.state.task_supervisor = SimpleNamespace(snapshot=lambda: {
        name: {"state": "running" if ready else "failed", "error": "private-diagnostic"}
        for name in ("market-collector", "alert-loop")
    })
    app.state.snapshot_store = SnapshotStore()
    app.state.snapshot_store.mark_published()
    result = await client.get("/api/health/ready")
    assert result.status_code == (200 if ready else 503)
    assert result.json() == {"status": "ready" if ready else "not_ready"}
    result = await client.get("/api/health/live")
    assert result.status_code == 200 and result.json() == {"status": "ok"}


async def test_global_rate_budget_includes_successes_and_recovers_at_sixty_seconds(context, monkeypatch):
    app, client, store = context
    assert (await sign_in(app, client)).status_code == 200
    for index in range(29):
        assert (await sign_in(app, client, code="bad", headers={"x-forwarded-for": f"192.0.2.{index + 1}"})).status_code == 401
    for elapsed in (0, 30, 59.999):
        monkeypatch.setattr("app.api.routes_auth.time", SimpleNamespace(time=lambda: NOW + elapsed))
        result = await sign_in(app, client, code=totp_at(TEST_TOTP_SECRET, int(NOW // 30) + 1))
        assert result.status_code == 429
        assert result.headers["retry-after"] == "60"
        assert "set-cookie" not in result.headers
    assert app.state.feishu_notifier.send_text.call_count == 2  # success + one rate warning
    async with store.db.execute("SELECT COUNT(*) FROM auth_login_attempts") as cursor:
        assert (await cursor.fetchone())[0] == 30
    async with store.db.execute("SELECT COUNT(*) FROM auth_locks") as cursor:
        assert (await cursor.fetchone())[0] == 0
    monkeypatch.setattr("app.api.routes_auth.time", SimpleNamespace(time=lambda: NOW + 60))
    # The otherwise valid code rejected by the rate limit was not consumed.
    result = await sign_in(app, client, code=totp_at(TEST_TOTP_SECRET, int(NOW // 30) + 1))
    assert result.status_code == 200


async def test_ip_locked_and_malformed_attempts_still_consume_global_budget(context):
    app, client, store = context
    for _ in range(30):
        result = await client.post("/api/auth/login", content="malformed")
        assert result.status_code == 401
    assert (await sign_in(app, client, headers={"x-forwarded-for": "192.0.2.99"})).status_code == 429
    async with store.db.execute("SELECT until_at FROM auth_locks WHERE scope = 'ip:127.0.0.1'") as cursor:
        assert (await cursor.fetchone())[0] == NOW + 900
    assert app.state.feishu_notifier.send_text.call_count == 2  # one IP lock + one rate warning


async def test_shared_rate_budget_and_notice_cooldown_survive_reopen(context, tmp_path):
    _, _, store = context
    args = dict(password_ok=False, secret=TEST_TOTP_SECRET, code="bad", browser="Test", now=NOW, days=30)
    other = await AuthStore.open(str(tmp_path / "auth.db"))
    try:
        results = await asyncio.gather(*(
            (store if index % 2 else other).login(**args, ip=f"192.0.2.{index + 1}") for index in range(40)
        ))
        assert sum(result.rate_limited for result in results) == 10
        assert sum(result.notify_rate_limit for result in results) == 1
    finally:
        await other.close()
    reopened = await AuthStore.open(str(tmp_path / "auth.db"))
    try:
        denied = await reopened.login(**args, ip="198.51.100.1")
        assert denied.rate_limited and not denied.notify_rate_limit
        for elapsed, notify in ((60, False), (899.999, False), (900, True)):
            # Renew the budget only when the previous window has actually expired.
            if elapsed != 900:
                for index in range(30):
                    result = await reopened.login(**{**args, "now": NOW + elapsed}, ip=f"198.51.100.{index + 1}")
                    assert not result.rate_limited
            result = await reopened.login(**{**args, "now": NOW + elapsed}, ip="203.0.113.1")
            assert result.rate_limited and result.notify_rate_limit is notify
        async with reopened.db.execute("SELECT COUNT(*) FROM auth_locks WHERE scope = 'global'") as cursor:
            assert (await cursor.fetchone())[0] == 0
    finally:
        await reopened.close()


async def test_legacy_global_lock_removed_but_ip_lock_preserved(context, tmp_path):
    _, _, store = context
    await store.db.executemany("INSERT INTO auth_locks VALUES (?, ?)", [("global", NOW + 900), ("ip:192.0.2.1", NOW + 900)])
    await store.db.commit()
    reopened = await AuthStore.open(str(tmp_path / "auth.db"))
    try:
        async with reopened.db.execute("SELECT scope, until_at FROM auth_locks") as cursor:
            assert [tuple(row) for row in await cursor.fetchall()] == [("ip:192.0.2.1", NOW + 900)]
    finally:
        await reopened.close()


async def test_rate_warning_and_failed_delivery_never_leak_credentials(context, caplog):
    app, client, _ = context
    assert (await sign_in(app, client)).status_code == 200
    token = client.cookies.get("taoli1_session")
    sensitive = (token, app.state.settings.dashboard_password, app.state.settings.dashboard_api_token,
                 TEST_TOTP_SECRET, totp_at(TEST_TOTP_SECRET, int(NOW // 30)))
    for index in range(29):
        assert (await sign_in(app, client, code="bad", headers={"x-forwarded-for": f"192.0.2.{index + 1}"})).status_code == 401
    app.state.feishu_notifier.send_text.side_effect = RuntimeError(" ".join(sensitive))
    response = await sign_in(app, client, headers={"user-agent": "Chrome/123 Windows " + " ".join(sensitive)})
    assert response.status_code == 429
    assert app.state.feishu_notifier.send_text.call_count == 2
    outputs = caplog.text + response.text + repr(LoginResult(token=token)) + "".join(
        call.args[0] for call in app.state.feishu_notifier.send_text.call_args_list
    )
    assert all(value not in outputs for value in sensitive)
    assert "Authentication notification could not be delivered" in caplog.text


@pytest.mark.parametrize("environment,bypass,status", [("production", True, 401), ("test", False, 401), ("test", True, 200)])
async def test_empty_password_warning_matches_actual_access(caplog, environment, bypass, status):
    app = create_app(settings=Settings(environment=environment, dashboard_password="", dashboard_auth_disabled=bypass,
                                       database_url="sqlite:///:memory:", feishu_live_send_enabled=False),
                     start_background_workers=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://test") as client:
            assert (await client.get("/api/settings/risk")).status_code == status
    assert "all API routes are unprotected" not in caplog.text
    assert "protected API routes deny access unless explicit non-production auth bypass is enabled" in caplog.text
