import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.core.config import Settings
from app.core.security import verify_dashboard_password
from app.main import create_app


PUBLIC_PATHS = {
    "/api/health", "/api/health/live", "/api/health/ready", "/api/features",
}
PASSWORD = "route-permissions-test"


@pytest.fixture
def app():
    # Skip lifespan for route enumeration: no database, workers or external services.
    return create_app(
        settings=Settings(dashboard_password=PASSWORD, database_url="sqlite:///:memory:"),
        start_background_workers=False,
    )


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        yield client


def api_routes(app):
    return [
        route for route in app.routes
        if isinstance(route, APIRoute) and (route.path == "/api" or route.path.startswith("/api/"))
    ]


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


@pytest.mark.parametrize("headers", [{}, {"X-Dashboard-Password": "wrong"}])
async def test_every_api_route_and_method_requires_password(app, client, headers):
    routes = api_routes(app)
    public_seen = set()
    checked = 0
    for route in routes:
        for method in sorted(route.methods):
            response = await client.request(method, placeholder_path(route), headers=headers)
            checked += 1
            if route.path in PUBLIC_PATHS:
                public_seen.add(route.path)
                assert response.status_code == 200, (method, route.path, response.text)
            else:
                assert response.status_code == 401, (method, route.path, response.text)
                assert response.json() == {"detail": "Invalid dashboard password"}
    assert public_seen == PUBLIC_PATHS
    assert checked == sum(len(route.methods) for route in routes)
    assert len(routes) >= 178  # Baseline; new routes are automatically exercised above.
    print(
        f"API route inventory: {len(routes)} routes, "
        f"{len({route.path for route in routes})} paths, {checked} method/path pairs"
    )


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
    app.state.settings = Settings(dashboard_password=configured_password)
    app.state.account_position_service = None
    # Keep the write path local while verifying successful authorization.
    if method == "DELETE":
        app.state.index_component_repo = SimpleNamespace(delete_watch_item=AsyncMock(return_value=False))
    headers = {"X-Dashboard-Password": PASSWORD} if configured_password else {}
    response = await client.request(method, path, headers=headers)
    assert response.status_code == expected_status, response.text


async def test_stream_does_not_accept_query_password(client):
    response = await client.get("/api/stream", params={"password": PASSWORD})
    assert response.status_code == 401


@pytest.mark.parametrize("provided", [None, "", "wrong", "密码"])
def test_password_comparison_rejects_invalid_values(provided):
    with pytest.raises(HTTPException) as error:
        verify_dashboard_password(PASSWORD, provided)
    assert error.value.status_code == 401


def test_password_comparison_preserves_unicode_and_empty_configuration():
    verify_dashboard_password("密码", "密码")
    verify_dashboard_password("", None)
    verify_dashboard_password("", "anything")


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
        assert "all API routes are unprotected" in warnings[0].message
