"""Round 8 gateway security and contract tests, using only synthetic sessions/tokens."""
import json
import logging
from dataclasses import replace

import httpx
import pytest
from auth_helpers import TEST_TOTP_SECRET, IsolatedSessionLookup, session_headers
from fastapi.testclient import TestClient

from app.api import routes_live_exec
from app.core.config import Settings, get_settings
from app.main import create_app

READ_TOKEN = "synthetic-read-token"
ADMIN_TOKEN = "synthetic-admin-token"
MACHINE_TOKEN = "a" * 64
MAPPING = "0123456789abcdef01234567"
READS = ("config", "config/audit", "universe", "mappings")
WRITES = [
    ("PUT", "config", {"order_usd_max": 30}),
    ("POST", "universe/SKHYNIX/exclude", {}),
    ("POST", "universe/SKHYNIX/unexclude", {}),
    ("POST", "universe/SKHYNIX/classify", {"category": "listed_equity", "underlying_market": "KRX",
                                         "markets": ["binance|future|SKHYNIXUSDT"]}),
    ("POST", f"mappings/{MAPPING}/confirm", {}),
    ("POST", f"mappings/{MAPPING}/reject", {}),
]
CSRF = {"Origin": "https://radar.test", "X-Live-Exec-CSRF": "1"}
REAL_CLIENT = httpx.AsyncClient
# Explicit contract oracle, independent of the production validator.
BOUNDS = [
    ("min_quote_volume_usd", 100000, None), ("min_venues_with_volume", 2, None),
    ("sampling_min_quote_volume_usd", 0, None), ("order_usd_max", 5, 150),
    ("depth_fraction", .01, .5), ("oi_fraction", .00001, .005),
    ("max_total_open_usd", 0, 600), ("max_open_pairs", 0, 3),
    ("max_equity_drop_usd", 10, 150), ("daily_loss_stop_usd", 1, 20),
    ("total_loss_stop_usd", 1, 100), ("min_excess_bps", 15, None),
    ("max_book_skew_ms", 0, 300), ("max_book_age_seconds", 0, 1),
    ("max_funding_diff_bps_h", 0, .5), ("max_funding_rate_8h", 0, .0005),
    ("max_baseline_bps", 0, 10), ("max_baseline_mad_bps", 0, 10),
    ("max_normal_deviation_bps", 0, 10), ("max_reference_age_seconds", 0, 90),
    ("min_outlier_share", .7, 1), ("min_venues", 3, 6),
]


@pytest.fixture
def context(monkeypatch):
    app = create_app(settings=Settings(environment="test", database_url="sqlite:///:memory:",
        dashboard_password="test-password", dashboard_totp_secret=TEST_TOTP_SECRET,
        dashboard_api_token=MACHINE_TOKEN, crossex_exec_url="http://exec.test:8795/",
        crossex_exec_api_key=READ_TOKEN))
    # The fixture still starts before the new Settings field exists (TDD red phase).
    object.__setattr__(app.state.settings, "crossex_exec_admin_token", ADMIN_TOKEN)
    app.state.auth_store = IsolatedSessionLookup()
    calls = []

    def install(handler):
        def factory(**kwargs):
            assert kwargs == {"timeout": 3.0, "trust_env": False, "follow_redirects": False}
            return REAL_CLIENT(**kwargs, transport=httpx.MockTransport(handler))
        monkeypatch.setattr(routes_live_exec.httpx, "AsyncClient", factory)

    def success(request):
        calls.append(request)
        return httpx.Response(200, json={"example": None, "market_key": "hyperliquid|future|xyz:XYZ"})

    install(success)
    client = TestClient(app, base_url="https://radar.test", headers={**session_headers("secret"), **CSRF})
    return app, client, calls, install


@pytest.mark.parametrize("method,path,body", WRITES)
@pytest.mark.parametrize("auth", ("anonymous", "machine", "development", "invalid_session"))
def test_write_requires_real_session(context, method, path, body, auth):
    app, _, calls, _ = context
    headers = dict(CSRF)
    if auth == "machine": headers["X-Api-Token"] = MACHINE_TOKEN
    if auth == "invalid_session": headers["Cookie"] = "taoli1_session=" + "f" * 64
    if auth == "development":
        app.state.settings = replace(app.state.settings, dashboard_auth_disabled=True)
    client = TestClient(app, base_url="https://radar.test", headers=headers)
    assert client.request(method, f"/api/live-exec/{path}", json=body).status_code == 401
    assert not calls


@pytest.mark.parametrize("headers", [
    {}, {"Origin": CSRF["Origin"]}, {"X-Live-Exec-CSRF": "1"},
    {**CSRF, "Origin": "https://evil.test"}, {**CSRF, "Origin": "null"},
    {**CSRF, "Origin": "http://radar.test"}, {**CSRF, "Origin": "https://radar.test:444"},
    {**CSRF, "Origin": "https://radar.test.evil.test"},
    {**CSRF, "Origin": "https://radar.test/path"},
    {**CSRF, "Origin": "https://radar.test", "Sec-Fetch-Site": "cross-site"},
    {**CSRF, "X-Live-Exec-CSRF": "wrong"},
    {**CSRF, "Origin": "https://evil.test", "X-Forwarded-Host": "evil.test"},
])
def test_csrf_is_required(context, headers):
    app, _, calls, _ = context
    client = TestClient(app, base_url="https://radar.test", headers=session_headers("secret"))
    response = client.put("/api/live-exec/config", headers=headers, json={"order_usd_max": 30})
    assert response.status_code == 403
    assert "来源" in response.json()["detail"]
    assert not calls


def test_production_https_origin_behind_plain_http_proxy(context):
    app, _, calls, _ = context
    app.state.settings = replace(app.state.settings, environment="production")
    client = TestClient(app, base_url="http://radar.test", headers={**session_headers("secret"), **CSRF})
    assert client.put("/api/live-exec/config", json={"order_usd_max": 30}).status_code == 200
    assert len(calls) == 1
    assert client.put("/api/live-exec/config", json={"order_usd_max": 30},
                      headers={"Origin": "http://radar.test"}).status_code == 403


@pytest.mark.parametrize("method,path,body", WRITES)
def test_write_passes_exact_path_body_admin_token_and_audits(context, caplog, method, path, body):
    _, client, calls, _ = context
    with caplog.at_level(logging.INFO, logger=routes_live_exec.__name__):
        response = client.request(method, f"/api/live-exec/{path}", json=body)
    assert response.status_code == 200
    assert response.json()["data"]["market_key"] == "hyperliquid|future|xyz:XYZ"
    sent = calls[0]
    assert sent.url.path == f"/api/v1/{path}"
    assert sent.method == method
    assert json.loads(sent.content) == body
    assert sent.headers["X-Api-Token"] == ADMIN_TOKEN
    assert sent.headers["Content-Type"] == "application/json"
    assert int(sent.headers["Content-Length"]) == len(sent.content)
    assert "cookie" not in sent.headers
    assert "live_exec_admin" in caplog.text
    assert "actor=session:" in caplog.text
    assert "parameters=" in caplog.text
    for secret in (ADMIN_TOKEN, READ_TOKEN, MACHINE_TOKEN, session_headers("secret")["Cookie"]):
        assert secret not in caplog.text + response.text


@pytest.mark.parametrize("path", READS)
def test_new_reads_keep_read_token_and_work_without_admin(context, path):
    app, client, calls, _ = context
    object.__setattr__(app.state.settings, "crossex_exec_admin_token", "")
    assert client.get(f"/api/live-exec/{path}").json()["available"] is True
    assert calls[-1].headers["X-Api-Token"] == READ_TOKEN
    assert calls[-1].url.path == f"/api/v1/{path}"
    assert TestClient(app).get(f"/api/live-exec/{path}").status_code == 401
    assert client.get(f"/api/live-exec/{path}?unexpected=1").status_code == 422
    machine = TestClient(app, headers={"X-Api-Token": MACHINE_TOKEN})
    assert machine.get(f"/api/live-exec/{path}").status_code == 200


@pytest.mark.parametrize("method,path,body", WRITES)
def test_unconfigured_admin_returns_explicit_503(context, method, path, body):
    app, client, calls, _ = context
    object.__setattr__(app.state.settings, "crossex_exec_admin_token", "")
    response = client.request(method, f"/api/live-exec/{path}", json=body)
    assert response.status_code == 503
    assert "未配置" in response.json()["detail"]
    assert not calls


@pytest.mark.parametrize("field,value", [("crossex_exec_url", ""),
    ("crossex_exec_url", "http://user:password@exec.test"),
    ("crossex_exec_admin_token", READ_TOKEN)])
def test_bad_write_configuration_is_closed(context, field, value):
    app, client, calls, _ = context
    object.__setattr__(app.state.settings, field, value)
    assert client.put("/api/live-exec/config", json={"order_usd_max": 30}).status_code == 503
    assert not calls


@pytest.mark.parametrize("body", [
    {}, {"unexpected": 1}, {"order_usd_max": 30, "token": "secret"},
    {"order_usd_max": 151}, {"order_usd_max": 4}, {"order_usd_max": True},
    {"order_usd_max": "30"}, {"min_venues": 3.5}, {"min_venues": 3.0},
    {"order_usd_max": None}, {"order_usd_max": []},
])
def test_config_whitelist_and_strict_types(context, body):
    _, client, calls, _ = context
    assert client.put("/api/live-exec/config", json=body).status_code == 400
    assert not calls


@pytest.mark.parametrize("name,low,high", BOUNDS)
def test_all_parameter_bounds_are_atomic_and_unclamped(context, name, low, high):
    _, client, calls, _ = context
    for value in (low, high if high is not None else max(1000000, low * 2)):
        response = client.put("/api/live-exec/config", json={name: value})
        assert response.status_code == 200
        assert json.loads(calls[-1].content) == {name: value}
    before = len(calls)
    for value in (low - 1, high + 1 if high is not None else False):
        assert client.put("/api/live-exec/config", json={"order_usd_max": 30, name: value}).status_code == 400
    assert len(calls) == before


@pytest.mark.parametrize("header,value", [("Origin", "https://radar.test"), ("X-Live-Exec-CSRF", "1")])
def test_duplicate_csrf_headers_are_rejected(context, header, value):
    _, client, calls, _ = context
    response = client.put("/api/live-exec/config", json={"order_usd_max": 30}, headers=[(header, value), (header, value)])
    assert response.status_code == 403
    assert not calls


def test_rejected_update_is_audited_without_untrusted_field_values(context, caplog):
    _, client, calls, _ = context
    with caplog.at_level(logging.INFO, logger=routes_live_exec.__name__):
        response = client.put("/api/live-exec/config", json={"unknown": ADMIN_TOKEN})
    assert response.status_code == 400
    assert "live_exec_admin" in caplog.text and "status=400" in caplog.text
    assert ADMIN_TOKEN not in caplog.text + response.text
    assert not calls


@pytest.mark.parametrize("body", [b'[]', b'null', b'{', b'{"order_usd_max":NaN}',
    b'{"order_usd_max":1e999}', b'{"order_usd_max":30,"order_usd_max":40}',
    b'{"order_usd_max":"\xff"}', b' ' * 8193, b'[' * 1500, b''])
def test_bounded_strict_json(context, body):
    _, client, calls, _ = context
    response = client.put("/api/live-exec/config", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code in (400, 413)
    assert not calls


@pytest.mark.parametrize("headers", [
    {"Content-Type": "text/plain"}, {"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
    [("Content-Type", "application/json"), ("Content-Length", "2"), ("Content-Length", "2")],
    {"Content-Type": "application/json", "Content-Length": "99999"},
    {"Content-Type": "application/json", "Content-Length": "1"},
])
def test_bad_body_framing(context, headers):
    _, client, calls, _ = context
    assert client.put("/api/live-exec/config", content=b'{}', headers=headers).status_code in (400, 413)
    assert not calls


@pytest.mark.parametrize("path,body", [
    ("universe/BTC/exclude", {"category": "crypto"}),
    (f"mappings/{MAPPING}/confirm", {"status": "confirmed"}),
    ("universe/BTC/classify", {"category": "invalid", "markets": ["a|future|BTCUSDT"]}),
    ("universe/BTC/classify", {"category": "listed_equity", "markets": ["a|future|BTCUSDT"]}),
    ("universe/BTC/classify", {"category": "listed_equity", "underlying_market": "CN", "markets": ["a|future|BTCUSDT"]}),
    ("universe/BTC/classify", {"category": "crypto", "underlying_market": "US", "markets": ["a|future|BTCUSDT"]}),
    ("universe/BTC/classify", {"category": "crypto", "markets": []}),
    ("universe/BTC/classify", {"category": "crypto", "markets": ["same", "same"]}),
    ("universe/BTC/classify", {"category": "crypto", "markets": [123]}),
    ("universe/BTC/classify", {"category": "crypto", "markets": ["a|future|BTCUSDT"], "token": "secret"}),
    ("universe/BTC/exclude?unexpected=1", {}),
])
def test_action_body_whitelists(context, path, body):
    _, client, calls, _ = context
    assert client.post(f"/api/live-exec/{path}", json=body).status_code in (400, 422)
    assert not calls


@pytest.mark.parametrize("path", ["universe/btc/exclude", "universe/BTC/enable", "mappings/xyz/confirm",
                                  f"mappings/{MAPPING}/enable", "summary", "orders"])
def test_no_arbitrary_write_routes(context, path):
    _, client, calls, _ = context
    assert client.post(f"/api/live-exec/{path}", json={}).status_code in (400, 404, 405)
    assert not calls


@pytest.mark.parametrize("status,code,chinese", [
    (400, "invalid_update", "更新无效"), (401, "unauthorized", "认证失败"),
    (403, "admin_required", "管理权限"), (503, "temporarily_unavailable", "暂不可用"),
    (302, "unknown", "异常"),
])
def test_upstream_errors_keep_status_and_translate_without_secrets(context, caplog, status, code, chinese):
    _, client, _, install = context
    install(lambda r: httpx.Response(status, json={"error": code, "detail": ADMIN_TOKEN, "token": READ_TOKEN}))
    with caplog.at_level(logging.INFO):
        response = client.put("/api/live-exec/config", json={"order_usd_max": 30})
    assert response.status_code == (status if status != 302 else 502)
    assert chinese in response.json()["detail"]
    assert ADMIN_TOKEN not in response.text + caplog.text
    assert READ_TOKEN not in response.text + caplog.text


@pytest.mark.parametrize("kind,status", [("timeout", 504), ("connect", 502), ("invalid_json", 502)])
def test_write_transport_errors_are_safe_and_not_retried(context, caplog, kind, status):
    _, client, _, install = context
    requests = []
    def fail(request):
        requests.append(request)
        if kind == "timeout": raise httpx.ReadTimeout(ADMIN_TOKEN, request=request)
        if kind == "connect": raise httpx.ConnectError(ADMIN_TOKEN, request=request)
        return httpx.Response(200, text=ADMIN_TOKEN)
    install(fail)
    with caplog.at_level(logging.INFO):
        response = client.put("/api/live-exec/config", json={"order_usd_max": 30})
    assert response.status_code == status
    assert len(requests) == 1
    assert "live_exec_admin" in caplog.text
    assert ADMIN_TOKEN not in response.text + caplog.text


def test_admin_token_environment_and_redacted_repr(monkeypatch):
    assert getattr(Settings(), "crossex_exec_admin_token", None) == ""
    monkeypatch.setenv("CROSSEX_EXEC_ADMIN_TOKEN", ADMIN_TOKEN)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.crossex_exec_admin_token == ADMIN_TOKEN
        assert ADMIN_TOKEN not in repr(settings)
    finally:
        get_settings.cache_clear()
