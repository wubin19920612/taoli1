"""Offline contract tests: no dependency on a running crossex-exec service."""
import json
import logging
import re
from copy import deepcopy
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
PATHS = ("summary", "positions", "trades", "stats", "remote-positions")
CONTRACT = Path(__file__).resolve().parents[2] / "docs/live-exec-contract-2026-11-11.md"
EXAMPLES = [json.loads(block) for block in re.findall(r"```json\s*(.*?)```", CONTRACT.read_text(encoding="utf-8"), re.S)]
PAYLOADS = dict(zip(PATHS, (EXAMPLES[0], [EXAMPLES[1]], [EXAMPLES[2]], EXAMPLES[3])))
PAYLOADS["remote-positions"] = {
    "available": True, "snapshot_at": "2026-11-12T00:00:00Z", "snapshot_version": 17, "stale": False,
    "positions": [{
        "position_key": "crossex|HYPERLIQUID_FUTURE_io:OAI_USDC|short", "route": "crossex",
        "exchange_type": "HYPERLIQUID", "business": "FUTURE", "symbol": "HYPERLIQUID_FUTURE_io:OAI_USDC",
        "quote": "USDC", "dex": "io", "identity_verified": True, "side": "short", "quantity": "2.00000001",
        "unrealized_pnl_native": "-2.25", "unrealized_pnl_usd": "-2.2545",
        "valuation": "public_spot_lower_bound", "valuation_verified": True, "valuation_error": None,
        "ownership": "unregistered", "snapshot_at": "2026-11-12T00:00:00Z", "snapshot_version": 17, "stale": False,
    }],
}
REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
def context(monkeypatch):
    app = create_app(settings=Settings(
        environment="test", database_url="sqlite:///:memory:",
        dashboard_password="test-password",
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


def test_round_seven_contract_includes_all_added_fields():
    summary = PAYLOADS["summary"]
    assert {key: summary[key] for key in (
        "baseline_ready_pairs", "structural_spread_pairs", "cooldown_pairs",
    )} == {"baseline_ready_pairs": 12, "structural_spread_pairs": 2, "cooldown_pairs": 1}
    assert summary["opening_skip_counts"] == {
        "book_skew": 2, "book_stale": 1, "funding_diff": 3,
        "baseline_not_flat": 4, "not_single_venue_outlier": 5, "too_few_venues": 6,
    }
    for path, close_skew in (("positions", None), ("trades", 2300)):
        row = PAYLOADS[path][0]
        assert {key: row[key] for key in (
            "baseline_bps", "excess_bps", "funding_cost_bps", "open_book_skew_ms", "close_book_skew_ms",
        )} == {"baseline_bps": 20.0, "excess_bps": 20.1, "funding_cost_bps": 1.0,
              "open_book_skew_ms": 250, "close_book_skew_ms": close_skew}


@pytest.mark.parametrize("path", ("summary", "positions", "trades"))
@pytest.mark.parametrize("variant", ("zero", "null", "missing"))
def test_passes_new_fields_and_legacy_responses_unchanged(context, path, variant):
    _, client, _, install = context
    payload = deepcopy(PAYLOADS[path])
    row = payload if path == "summary" else payload[0]
    fields = ("baseline_ready_pairs", "structural_spread_pairs", "cooldown_pairs", "opening_skip_counts") \
        if path == "summary" else (
            "baseline_bps", "excess_bps", "funding_cost_bps", "open_book_skew_ms", "close_book_skew_ms",
        )
    for key in fields:
        if variant == "missing":
            row.pop(key)
        elif key == "opening_skip_counts" and variant == "zero":
            row[key] = {reason: 0 for reason in row[key]}
        else:
            row[key] = 0 if variant == "zero" else None
    install(lambda request: httpx.Response(200, json=payload))
    response = client.get(f"/api/live-exec/{path}")
    assert response.status_code == 200
    assert response.json() == {"available": True, "data": payload}


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
    "remote-positions?limit=1", "remote-positions?path=/orders", "remote-positions?a=1&a=2",
])
def test_invalid_query_never_contacts_upstream(context, url):
    _, client, calls, _ = context
    assert client.get(f"/api/live-exec/{url}").status_code == 422
    assert not calls


@pytest.mark.parametrize(("kind", "reason"), [
    ("timeout", "timeout"), ("connect", "unreachable"), ("status", "upstream_http_401"),
    ("redirect", "upstream_http_302"), ("json", "invalid_json"), ("nan", "invalid_json"),
])
@pytest.mark.parametrize("path", ("summary", "remote-positions"))
def test_failures_are_200_and_never_expose_credentials(context, caplog, kind, reason, path):
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
        response = client.get(f"/api/live-exec/{path}")
    assert response.status_code == 200
    assert response.json() == {"available": False, "error": reason}
    assert TOKEN not in caplog.text
    assert TOKEN not in response.text


def test_unknown_routes_and_read_only_methods(context):
    _, client, calls, _ = context
    assert client.get("/api/live-exec/orders").status_code == 404
    assert client.post("/api/live-exec/summary").status_code == 405
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert client.request(method, "/api/live-exec/remote-positions").status_code == 405
    assert not calls


@pytest.mark.parametrize("available,stale", [(False, True), (True, True), (True, False)])
def test_remote_snapshot_availability_is_nested_and_empty_is_not_assumed(context, available, stale):
    _, client, calls, install = context
    payload = {**PAYLOADS["remote-positions"], "available": available, "stale": stale, "positions": [],
               "future_risk_scope": None}
    install(lambda request: httpx.Response(200, json=payload, headers={"X-Upstream-Secret": TOKEN}))
    response = client.get("/api/live-exec/remote-positions?")
    assert response.status_code == 200
    assert response.json() == {"available": True, "data": payload}
    assert response.headers["Cache-Control"] == "no-store"
    assert "X-Upstream-Secret" not in response.headers


def test_remote_get_rejects_body_without_contacting_upstream(context):
    _, client, calls, _ = context
    assert client.request("GET", "/api/live-exec/remote-positions", json={"refresh": True}).status_code == 422
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
