"""Offline contract tests: no dependency on a running crossex-exec service."""
import json
import logging
import re
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import routes_live_exec
from app.core.config import Settings, get_settings
from app.main import create_app
from auth_helpers import IsolatedSessionLookup, TEST_TOTP_SECRET, session_headers


TOKEN = "synthetic-live-exec-token"
PATHS = ("summary", "positions", "trades", "stats")
CONTRACT = Path(__file__).resolve().parents[2] / "docs/live-exec-contract-2026-11-11.md"
EXAMPLES = [json.loads(block) for block in re.findall(r"```json\s*(.*?)```", CONTRACT.read_text(encoding="utf-8"), re.S)]
PAYLOADS = dict(zip(PATHS, (EXAMPLES[0], [EXAMPLES[1]], [EXAMPLES[2]], EXAMPLES[3])))
REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
def context(monkeypatch):
    app = create_app(settings=Settings(
        environment="test", database_url="sqlite:///:memory:",
        dashboard_totp_secret=TEST_TOTP_SECRET,
        crossex_exec_url="http://exec.test:8795/", crossex_exec_api_key=TOKEN,
    ))
    app.state.auth_store = IsolatedSessionLookup()
    calls = []

    def install(handler):
        def factory(**kwargs):
            assert kwargs["timeout"] == 3.0
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
            return REAL_ASYNC_CLIENT(**kwargs, transport=httpx.MockTransport(handler))
        monkeypatch.setattr(routes_live_exec.httpx, "AsyncClient", factory)

    def success(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["X-Api-Token"] == TOKEN
        return httpx.Response(200, json=PAYLOADS[request.url.path.rsplit("/", 1)[-1]])

    install(success)
    return app, TestClient(app, headers=session_headers("secret")), calls, install


@pytest.mark.parametrize("path", PATHS)
def test_anonymous_is_401_even_when_unconfigured(context, path):
    app, _, calls, _ = context
    app.state.settings = replace(app.state.settings, crossex_exec_url="")
    assert TestClient(app).get(f"/api/live-exec/{path}").status_code == 401
    assert not calls


@pytest.mark.parametrize("field", ["crossex_exec_url", "crossex_exec_api_key"])
@pytest.mark.parametrize("path", PATHS)
def test_unconfigured_is_available_false(context, field, path):
    app, client, calls, _ = context
    app.state.settings = replace(app.state.settings, **{field: ""})
    response = client.get(f"/api/live-exec/{path}")
    assert response.status_code == 200
    assert response.json() == {"available": False, "error": "not_configured"}
    assert not calls


@pytest.mark.parametrize("path", PATHS)
def test_passes_contract_examples_unchanged(context, path):
    _, client, calls, _ = context
    response = client.get(f"/api/live-exec/{path}")
    assert response.status_code == 200
    assert response.json() == {"available": True, "data": PAYLOADS[path]}
    assert calls[0].url.path == f"/api/v1/{path}"
    assert TOKEN not in response.text


def test_query_whitelist_and_boundary_values(context):
    _, client, calls, _ = context
    before = "2026-11-11T08:00:00Z"
    assert client.get("/api/live-exec/trades", params={"limit": 500, "before": before}).status_code == 200
    assert dict(calls[-1].url.params) == {"limit": "500", "before": before}
    assert client.get("/api/live-exec/trades?limit=1").status_code == 200
    for days in (1, 90):
        assert client.get(f"/api/live-exec/stats?days={days}").status_code == 200
        assert dict(calls[-1].url.params) == {"days": str(days)}


@pytest.mark.parametrize("url", [
    "trades?limit=0", "trades?limit=501", "trades?limit=no", "trades?limit=1.5",
    "stats?days=0", "stats?days=91", "stats?days=no", "stats?days=1.5",
    "trades?before=not-a-date", "trades?before=2026-11-11",
    "trades?before=2026-11-11T08:00:00", "trades?before=../../summary",
    "summary?path=/orders", "positions?limit=1", "trades?days=7",
    "stats?before=x", "trades?limit=1&limit=2",
])
def test_invalid_query_never_contacts_upstream(context, url):
    _, client, calls, _ = context
    assert client.get(f"/api/live-exec/{url}").status_code == 422
    assert not calls


@pytest.mark.parametrize(("kind", "reason"), [
    ("timeout", "timeout"), ("connect", "unreachable"), ("status", "upstream_http_401"),
    ("redirect", "upstream_http_302"), ("json", "invalid_json"), ("nan", "invalid_json"),
])
def test_failures_are_200_and_never_expose_credentials(context, caplog, kind, reason):
    _, client, _, install = context

    def failing(request):
        if kind == "timeout":
            raise httpx.ReadTimeout(TOKEN, request=request)
        if kind == "connect":
            raise httpx.ConnectError(TOKEN, request=request)
        if kind == "status":
            return httpx.Response(401, text=TOKEN)
        if kind == "redirect":
            return httpx.Response(302, headers={"location": "http://other.test/"})
        return httpx.Response(200, text=TOKEN if kind == "json" else '{"value": NaN}')

    install(failing)
    with caplog.at_level(logging.DEBUG):
        response = client.get("/api/live-exec/summary")
    assert response.status_code == 200
    assert response.json() == {"available": False, "error": reason}
    assert TOKEN not in caplog.text
    assert TOKEN not in response.text


def test_only_four_get_routes_exist(context):
    _, client, calls, _ = context
    assert client.get("/api/live-exec/orders").status_code == 404
    assert client.post("/api/live-exec/summary").status_code == 405
    assert not calls


def test_environment_configuration(monkeypatch):
    assert Settings().crossex_exec_url == ""
    assert Settings().crossex_exec_api_key == ""
    monkeypatch.setenv("CROSSEX_EXEC_URL", "http://exec.test:8795")
    monkeypatch.setenv("CROSSEX_EXEC_API_KEY", TOKEN)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.crossex_exec_url == "http://exec.test:8795"
        assert settings.crossex_exec_api_key == TOKEN
    finally:
        get_settings.cache_clear()
