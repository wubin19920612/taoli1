import asyncio
import base64
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.api.routes_auth import LOGIN_ERROR, router
from app.core.config import Settings
from app.core.security import browser_label, client_ip, constant_time_equal, require_authentication
from app.core.totp import matching_counter, totp_at
from app.db.auth import AuthStore, session_hash
from auth_helpers import TEST_TOTP_SECRET

NOW = 1800000000.0


@pytest.fixture
async def context(tmp_path, monkeypatch):
    # Use a real file to exercise persistence and concurrent connections, no lifespan/workers.
    store = await AuthStore.open(str(tmp_path / "auth.db"))
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.settings = Settings(
        environment="production", dashboard_password="synthetic-login-password",
        dashboard_totp_secret=TEST_TOTP_SECRET, dashboard_api_token="a" * 64,
        trusted_proxy_ips="127.0.0.1",
    )
    app.state.auth_store = store
    app.state.feishu_notifier = SimpleNamespace(send_text=AsyncMock())

    @app.get("/private")
    async def private(request: Request):
        await require_authentication(request)
        return {"ok": True}

    monkeypatch.setattr("app.core.security.time.time", lambda: NOW)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
                                 base_url="https://test") as client:
        yield app, client, store
    await store.close()


async def sign_in(app, client, *, code=None, password=None, headers=None):
    return await client.post("/api/auth/login", json={
        "password": app.state.settings.dashboard_password if password is None else password,
        "code": totp_at(TEST_TOTP_SECRET, int(NOW // 30)) if code is None else code,
    }, headers=headers)


@pytest.mark.parametrize("seconds,expected", [
    (59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
    (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130"),
])
def test_rfc6238_sha1_vectors(seconds, expected):
    assert totp_at(TEST_TOTP_SECRET, seconds // 30, 8) == expected
    assert totp_at(TEST_TOTP_SECRET, seconds // 30) == expected[-6:]


def test_window_edges_and_monotonic_replay():
    code = totp_at(TEST_TOTP_SECRET, 100)
    assert matching_counter(TEST_TOTP_SECRET, code, 2970, -1) == 100
    assert matching_counter(TEST_TOTP_SECRET, code, 3059.999, -1) == 100
    assert matching_counter(TEST_TOTP_SECRET, code, 2969.999, -1) is None
    assert matching_counter(TEST_TOTP_SECRET, code, 3060, -1) is None
    assert matching_counter(TEST_TOTP_SECRET, code, 3000, 100) is None
    assert matching_counter(TEST_TOTP_SECRET, code, 3000, 101) is None
    assert matching_counter(TEST_TOTP_SECRET, "１２３４５６", 3000, -1) is None


def test_password_uses_constant_time_bytes():
    with patch("app.core.security.hmac.compare_digest", return_value=True) as compare:
        assert constant_time_equal("unicode-密码", "candidate")
        assert compare.call_count == 1
        assert all(isinstance(arg, bytes) for arg in compare.call_args.args)


def test_configuration_repr_contains_no_credentials():
    settings = Settings(dashboard_password="synthetic-private-value", dashboard_totp_secret=TEST_TOTP_SECRET)
    assert repr(settings) == "Settings(<redacted>)"


async def test_cookie_hash_only_notification_and_replay(context, caplog):
    app, client, store = context
    response = await sign_in(app, client, headers={"user-agent": "Chrome/123 Windows secret-in-header"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=2592000"))
    token = client.cookies.get("taoli1_session")
    assert len(bytes.fromhex(token)) == 32
    async with store.db.execute("SELECT * FROM auth_sessions") as cursor:
        row = dict(await cursor.fetchone())
    assert row["id_hash"] == session_hash(token)
    assert token not in str(row)
    sessions = await client.get("/api/auth/sessions")
    assert sessions.json()[0]["current"] is True
    assert set(sessions.json()[0]) == {"created_at", "expires_at", "last_seen_at", "ip", "user_agent", "current"}
    assert row["expires_at"] - row["created_at"] == 30 * 86400
    assert (await client.get("/private")).status_code == 200
    assert (await sign_in(app, client)).json() == {"detail": LOGIN_ERROR}
    text = app.state.feishu_notifier.send_text.call_args.args[0] + caplog.text
    assert "Chrome / Windows" in text and "127.0.0.1" in text
    for value in (token, TEST_TOTP_SECRET, app.state.settings.dashboard_password,
                  totp_at(TEST_TOTP_SECRET, int(NOW // 30)), "secret-in-header"):
        assert value not in text


async def test_same_code_concurrent_connections_and_reopen(context, tmp_path):
    app, client, store = context
    other = await AuthStore.open(str(tmp_path / "auth.db"))
    args = dict(password_ok=True, secret=TEST_TOTP_SECRET, code=totp_at(TEST_TOTP_SECRET, int(NOW // 30)),
                ip="127.0.0.1", browser="Test", now=NOW, days=30)
    try:
        results = await asyncio.gather(store.login(**args), other.login(**args))
        assert sum(result.token is not None for result in results) == 1
    finally:
        await other.close()
    reopened = await AuthStore.open(str(tmp_path / "auth.db"))
    try:
        assert (await reopened.login(**args)).token is None
    finally:
        await reopened.close()


async def test_ip_lock_uniform_errors_unlock_and_notification_failure(context, monkeypatch):
    app, client, store = context
    app.state.feishu_notifier.send_text.side_effect = RuntimeError("sensitive-exception-text")
    failures = [await sign_in(app, client, password="invalid") for _ in range(5)]
    assert all(r.status_code == 401 and r.json() == {"detail": LOGIN_ERROR} for r in failures)
    assert (await sign_in(app, client)).status_code == 401
    assert app.state.feishu_notifier.send_text.call_count == 1
    # A separate connection sees the persisted lock.
    async with store.db.execute("SELECT until_at FROM auth_locks WHERE scope = 'ip:127.0.0.1'") as cursor:
        assert (await cursor.fetchone())[0] == NOW + 900
    monkeypatch.setattr("app.core.security.time.time", lambda: NOW + 900)
    result = await sign_in(app, client, code=totp_at(TEST_TOTP_SECRET, int((NOW + 900) // 30)))
    assert result.status_code == 200


async def test_twenty_failures_do_not_lock_other_ips(context):
    app, client, store = context
    for index in range(20):
        result = await sign_in(app, client, code="bad", headers={"x-forwarded-for": f"192.0.2.{index + 1}"})
        assert result.status_code == 401
    assert (await sign_in(app, client, headers={"x-forwarded-for": "198.51.100.1"})).status_code == 200
    assert app.state.feishu_notifier.send_text.call_count == 1
    async with store.db.execute("SELECT COUNT(*) FROM auth_locks WHERE scope = 'global'") as cursor:
        assert (await cursor.fetchone())[0] == 0


async def test_expiry_logout_and_revoke_all(context, monkeypatch):
    app, client, _ = context
    assert (await sign_in(app, client)).status_code == 200
    first = client.cookies.get("taoli1_session")
    assert (await client.post("/api/auth/logout")).status_code == 200
    assert (await client.get("/private", headers={"Cookie": "taoli1_session=" + first})).status_code == 401
    assert (await sign_in(app, client, code=totp_at(TEST_TOTP_SECRET, int(NOW // 30) + 1))).status_code == 200
    second = client.cookies.get("taoli1_session")
    assert (await client.post("/api/auth/sessions/revoke-all")).status_code == 200
    assert (await client.get("/private", headers={"Cookie": "taoli1_session=" + second})).status_code == 401
    monkeypatch.setattr("app.core.security.time.time", lambda: NOW + 60)
    assert (await sign_in(app, client, code=totp_at(TEST_TOTP_SECRET, int(NOW // 30) + 2))).status_code == 200
    third = client.cookies.get("taoli1_session")
    monkeypatch.setattr("app.core.security.time.time", lambda: NOW + 60 + 30 * 86400)
    assert (await client.get("/private", headers={"Cookie": "taoli1_session=" + third})).status_code == 401


@pytest.mark.parametrize("field,value", [("dashboard_password", ""), ("dashboard_totp_secret", ""),
                                         ("dashboard_totp_secret", "invalid")])
async def test_production_missing_configuration_fails_closed(context, field, value):
    app, client, _ = context
    assert (await sign_in(app, client)).status_code == 200
    app.state.settings = replace(app.state.settings, **{field: value}, dashboard_auth_disabled=True)
    for path in ("/private", "/api/auth/sessions"):
        assert (await client.get(path, headers={"X-Api-Token": "a" * 64})).status_code == 401


async def test_old_header_disabled_api_token_valid_and_session_management_cookie_only(context):
    app, client, _ = context
    assert (await client.get("/private", headers={"X-Dashboard-Password": app.state.settings.dashboard_password})).status_code == 401
    assert (await client.get("/private", headers={"X-Api-Token": "a" * 64})).status_code == 200
    for path in ("/api/auth/sessions",):
        assert (await client.get(path, headers={"X-Api-Token": "a" * 64})).status_code == 401
    for value in ("", "short", app.state.settings.dashboard_password):
        app.state.settings = replace(app.state.settings, dashboard_api_token=value)
        assert (await client.get("/private", headers={"X-Api-Token": value})).status_code == 401


async def test_multiple_devices_current_marker_and_revoke_all(context):
    app, client, _ = context
    assert (await sign_in(app, client)).status_code == 200
    first = client.cookies.get("taoli1_session")
    assert (await sign_in(app, client, code=totp_at(TEST_TOTP_SECRET, int(NOW // 30) + 1))).status_code == 200
    second = client.cookies.get("taoli1_session")
    rows = (await client.get("/api/auth/sessions")).json()
    assert len(rows) == 2 and sum(row["current"] for row in rows) == 1
    assert (await client.post("/api/auth/sessions/revoke-all")).status_code == 200
    for token in (first, second):
        assert (await client.get("/private", headers={"Cookie": "taoli1_session=" + token})).status_code == 401


async def test_local_bypass_is_explicit_and_never_production(context):
    app, client, _ = context
    app.state.settings = Settings()
    assert (await client.get("/private")).status_code == 401
    app.state.settings = Settings(environment="test", dashboard_auth_disabled=True)
    assert (await client.get("/private")).status_code == 200
    app.state.settings = replace(app.state.settings, environment="production")
    assert (await client.get("/private")).status_code == 401


async def test_storage_failure_and_invalid_unicode_are_sanitized(context, monkeypatch):
    app, client, store = context
    result = await client.post("/api/auth/login", content=r'{"password":"\ud800","code":"000000"}')
    assert result.status_code == 401 and result.json() == {"detail": LOGIN_ERROR}
    monkeypatch.setattr(store, "login", AsyncMock(side_effect=RuntimeError("sensitive-storage-error")))
    result = await sign_in(app, client)
    assert result.status_code == 503
    assert result.json() == {"detail": "Authentication unavailable"}


@pytest.mark.parametrize("peer,forwarded,expected", [
    ("172.18.0.2", "192.0.2.1, 198.51.100.2", "198.51.100.2"),
    ("172.18.0.3", "192.0.2.1", "192.0.2.1"),
    ("172.20.0.2", "192.0.2.1", "172.20.0.2"),
    ("127.0.0.1", "192.0.2.1", "127.0.0.1"),
    ("198.51.100.3", "192.0.2.1", "198.51.100.3"),
    ("172.18.0.2", "invalid", "172.18.0.2"),
    ("172.18.0.2", "2001:db8::1", "2001:db8::1"),
])
def test_trusted_proxy(peer, forwarded, expected):
    app = SimpleNamespace(state=SimpleNamespace(settings=Settings()))
    request = Request({"type": "http", "client": (peer, 123), "app": app,
                       "headers": [(b"x-forwarded-for", forwarded.encode())]})
    assert client_ip(request) == expected


async def test_malformed_requests_do_not_echo_credentials(context):
    _, client, _ = context
    for body in ('{"password": ["private-data"], "code": {}}', 'private-data', 'x' * 8200):
        result = await client.post("/api/auth/login", content=body)
        assert result.status_code == 401
        assert result.json() == {"detail": LOGIN_ERROR}


def test_setup_only_prints_valid_key_and_uri(capsys, monkeypatch):
    from app.cli.totp_setup import main
    monkeypatch.setattr("builtins.open", lambda *a, **k: pytest.fail("Setup must not write files"))
    main()
    output = capsys.readouterr()
    secret, uri = output.out.strip().splitlines()
    assert len(base64.b32decode(secret)) == 20
    assert uri == f"otpauth://totp/taoli1:admin?secret={secret}&issuer=taoli1"
    assert not output.err
