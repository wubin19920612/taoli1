import json
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.core.config import Settings
from app.core.security import constant_time_equal
from app.db.auth import AuthStore
from auth_helpers import TEST_TOTP_SECRET, seed_sessions, session_headers
from app.main import create_app
from app.services.shadow_runtime import ShadowRuntime


PUBLIC_PATHS = {
    "/api/health", "/api/health/live", "/api/health/ready", "/api/features",
}
PASSWORD = "route-permissions-test"
SHADOW_ENDPOINTS = {
    ("GET", "/api/shadow/status"),
    ("GET", "/api/shadow/trades"),
    ("GET", "/api/shadow/buckets"),
}


@pytest.fixture
def app():
    # Skip lifespan for route enumeration: no database, workers or external services.
    return create_app(
        settings=Settings(dashboard_totp_secret=TEST_TOTP_SECRET, dashboard_password=PASSWORD, database_url="sqlite:///:memory:"),
        start_background_workers=False,
    )


@pytest.fixture
async def client(app):
    app.state.auth_store = await AuthStore.open(":memory:")
    await seed_sessions(app.state.auth_store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        yield client
    await app.state.auth_store.close()


def _flatten_routes(routes, prefix=""):
    # FastAPI >= 0.16x wraps include_router() results in _IncludedRouter instead of copying APIRoutes.
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _flatten_routes(original.routes, prefix + route.include_context.prefix)
        elif isinstance(route, APIRoute):
            yield SimpleNamespace(
                path=prefix + route.path,
                path_format=prefix + route.path_format,
                param_convertors=route.param_convertors,
                methods=route.methods,
            )


def api_routes(app):
    routes = [
        route for route in _flatten_routes(app.routes)
        if route.path == "/api" or route.path.startswith("/api/")
    ]
    assert routes, "no /api routes enumerated"
    return routes


def placeholder_path(route):
    values = {
        "IntegerConvertor": "1",
        "FloatConvertor": "1.0",
        "UUIDConvertor": "00000000-0000-0000-0000-000000000001",
        "StringConvertor": "permission-test",
        "PathConvertor": "permission-test",
    }
    path = route.path_format
    for name, converter in route.param_convertors.items():
        path = path.replace("{" + name + "}", values[type(converter).__name__])
    return path


@pytest.mark.parametrize("headers", [{}, session_headers("wrong"), {"X-Dashboard-Password": PASSWORD}])
async def test_every_api_route_and_method_requires_session(app, client, headers):
    routes = api_routes(app)
    public_seen = set()
    checked = 0
    for route in routes:
        for method in sorted(route.methods):
            response = await client.request(method, placeholder_path(route), headers=headers)
            checked += 1
            if (method, route.path) == ("POST", "/api/auth/login"):
                assert response.status_code == 401
                assert response.json() == {"detail": "密码或验证码错误"}
            elif route.path in PUBLIC_PATHS:
                public_seen.add(route.path)
                assert response.status_code == 200, (method, route.path, response.text)
            else:
                assert response.status_code == 401, (method, route.path, response.text)
                assert response.json() == {"detail": "Authentication required"}
    assert public_seen == PUBLIC_PATHS
    assert checked == sum(len(route.methods) for route in routes)
    # Frozen from the pre-retirement routes, subtracting only the 64 retired endpoints.
    # A set check catches a lost retained route even if another route replaces it.
    baseline = json.loads((Path(__file__).parent / "fixtures/retained_api_endpoints.json").read_text(encoding="utf-8"))
    expected = {tuple(pair) for pair in baseline}
    observed = {(method, route.path) for route in routes for method in route.methods}
    assert len(expected) == 118
    assert expected <= observed
    assert SHADOW_ENDPOINTS <= observed
    assert len(routes) >= len(expected)  # Newly added routes are still exercised above.
    print(
        f"API route inventory: {len(routes)} routes, "
        f"{len({route.path for route in routes})} paths, {checked} method/path pairs"
    )


@pytest.mark.parametrize("shadow_enabled", [False, True])
@pytest.mark.parametrize("credential", [
    "missing", "legacy_password", "invalid_session", "invalid_token", "session", "api_token",
])
async def test_every_shadow_route_uses_session_or_api_token(
    app, client, monkeypatch, shadow_enabled, credential,
):
    app.state.settings = replace(
        app.state.settings, environment="production", dashboard_auth_disabled=False,
        dashboard_api_token="b" * 64, shadow_enabled=shadow_enabled,
    )
    app.state.shadow_runtime = ShadowRuntime(app.state.settings, app.state.snapshot_store)
    # Isolate business reads only; keep the central guard and real AuthStore intact.
    read = AsyncMock(return_value=[])
    monkeypatch.setattr("app.api.routes_shadow.read_committed", read)
    # Buckets now query through the child; isolate that business boundary too.
    app.state.shadow_runtime._published["ready"] = True
    monkeypatch.setattr(app.state.shadow_runtime, "statistics", AsyncMock(return_value=[]))
    headers = {
        "missing": {},
        "legacy_password": {"X-Dashboard-Password": PASSWORD},
        "invalid_session": session_headers("wrong"),
        "invalid_token": {"X-Api-Token": "c" * 64},
        "session": session_headers(PASSWORD),
        "api_token": {"X-Api-Token": "b" * 64},
    }[credential]
    authorized = credential in {"session", "api_token"}
    observed = set()
    for route in api_routes(app):
        if not route.path.startswith("/api/shadow"):
            continue
        for method in sorted(route.methods):
            observed.add((method, route.path))
            response = await client.request(method, placeholder_path(route), headers=headers)
            assert response.status_code == (200 if authorized else 401), (
                method, route.path, credential, response.text,
            )
            if not authorized:
                assert response.json() == {"detail": "Authentication required"}
            elif route.path == "/api/shadow/status":
                assert response.json()["enabled"] is shadow_enabled
            else:
                assert response.json() == []
    assert SHADOW_ENDPOINTS <= observed
    if not authorized or not shadow_enabled:
        read.assert_not_awaited()


@pytest.mark.parametrize("configured_password", [PASSWORD, ""])
@pytest.mark.parametrize(
    "method,path,expected_status",
    [
        ("GET", "/api/opportunities", 200),
        ("GET", "/api/health/detail", 200),
        ("GET", "/api/account-positions", 503),
        ("GET", "/api/index-components/watchlist", 503),
        ("POST", "/api/index-components/watchlist", 422),
        ("GET", "/api/astro/preview/missing", 404),
        ("DELETE", "/api/index-components/watchlist/missing", 204),
    ],
)
async def test_authorized_and_empty_password_preserve_route_behavior(
    app, client, configured_password, method, path, expected_status,
):
    app.state.settings = Settings(dashboard_totp_secret=TEST_TOTP_SECRET, dashboard_password=configured_password, dashboard_auth_disabled=not configured_password)
    app.state.account_position_service = None
    # Keep the write path local while verifying successful authorization.
    if method == "DELETE":
        app.state.index_component_repo = SimpleNamespace(delete_watch_item=AsyncMock(return_value=False))
    headers = session_headers(PASSWORD) if configured_password else {}
    response = await client.request(method, path, headers=headers)
    assert response.status_code == expected_status, response.text


async def test_stream_does_not_accept_query_password(client):
    response = await client.get("/api/stream", params={"password": PASSWORD})
    assert response.status_code == 401


@pytest.mark.parametrize("missing", ["dashboard_password", "dashboard_totp_secret"])
async def test_every_protected_route_fails_closed_in_production(app, client, missing):
    values = dict(environment="production", dashboard_password=PASSWORD,
                  dashboard_totp_secret=TEST_TOTP_SECRET, dashboard_api_token="a" * 64,
                  dashboard_auth_disabled=True)
    values[missing] = ""
    app.state.settings = Settings(**values)
    for route in api_routes(app):
        if route.path in PUBLIC_PATHS:
            continue
        for method in sorted(route.methods):
            response = await client.request(method, placeholder_path(route),
                                            headers={**session_headers(PASSWORD), "X-Api-Token": "a" * 64})
            assert response.status_code == 401, (method, route.path)


def test_password_comparison_preserves_unicode():
    assert constant_time_equal("密码", "密码")
    assert not constant_time_equal("密码", "wrong")


@pytest.mark.parametrize("password", ["", PASSWORD])
async def test_startup_warns_only_when_password_is_empty(caplog, password):
    app = create_app(
        settings=Settings(dashboard_password=password, database_url="sqlite:///:memory:"),
        start_background_workers=False,
    )
    with caplog.at_level(logging.WARNING, logger="app.main"):
        async with app.router.lifespan_context(app):
            pass
    warnings = [
        record for record in caplog.records
        if record.name == "app.main" and "DASHBOARD_PASSWORD" in record.message
    ]
    assert len(warnings) == (0 if password else 1)
    if warnings:
        assert warnings[0].levelno == logging.WARNING
        assert warnings[0].message == (
            "DASHBOARD_PASSWORD is empty; protected API routes deny access "
            "unless explicit non-production auth bypass is enabled"
        )
