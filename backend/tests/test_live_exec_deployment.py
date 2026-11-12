"""Deployment-bound regressions; no production credentials or external services."""
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import test_live_exec_admin as admin_tests
from auth_helpers import session_headers
from fastapi.testclient import TestClient
from test_live_exec_admin import ADMIN_TOKEN, READ_TOKEN

ROOT = Path(__file__).resolve().parents[2]
# Reuse the isolated session/upstream fixtures without changing global conftest.
audit_capture = admin_tests.audit_capture
context = admin_tests.context


def docker_command():
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    command, = re.findall(r"^CMD (\[.*\])$", dockerfile, re.MULTILINE)
    return json.loads(command)


@pytest.mark.parametrize("origin,status", [
    ("https://radar.test:8443", 200),
    ("https://radar.test", 403),
    ("https://radar.test:9443", 403),
    ("http://radar.test:8443", 403),
    ("https://evil.test:8443", 403),
])
def test_8443_origin_through_repository_proxy_configuration(context, origin, status):
    app, _, calls, _ = context
    app.state.settings = replace(app.state.settings, environment="production")
    outer = (ROOT / "deploy/nginx-https.conf.example").read_text(encoding="utf-8")
    inner = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
    assert re.search(r"listen\s+8443\s+ssl\s*;", outer)
    assert "proxy_pass http://127.0.0.1:3000;" in outer
    api, = re.findall(r"location /api/\s*\{([^}]+)\}", inner)
    assert "proxy_pass http://backend:8000/api/;" in api
    # Model only the Host directives actually used by these two locations.
    host = "radar.test:8443"
    for config in (outer, api):
        directive, = re.findall(r"proxy_set_header\s+Host\s+(\S+)\s*;", config)
        assert directive in ("$host", "$http_host")
        host = host.split(":")[0] if directive == "$host" else host
    # All API locations must preserve the same public authority.
    assert re.findall(r"proxy_set_header\s+Host\s+(\S+)\s*;", inner) == ["$http_host", "$http_host"]
    assert "--no-proxy-headers" in docker_command()
    client = TestClient(app, base_url="http://backend:8000", headers={
        **session_headers("secret"), "Host": host, "Origin": origin, "X-Live-Exec-CSRF": "1",
        # Forged proxy headers must never authorize a different origin/port.
        "X-Forwarded-Host": origin.split("://")[1], "X-Forwarded-Proto": "https",
        "Forwarded": f'host="{origin.split("://")[1]}";proto=https',
    })
    response = client.put("/api/live-exec/config", json={"order_usd_max": 30, "expected_version": 2})
    assert response.status_code == status
    assert len(calls) == (1 if status == 200 else 0)


def test_audit_stdout_under_docker_uvicorn_launch(tmp_path):
    """Use Dockerfile CLI/logging defaults in a fresh process, without caplog levels."""
    command = docker_command()
    assert command[:2] == ["uvicorn", "app.main:app"]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command[1] = "live_exec_uvicorn_fixture:create_app"
    command[command.index("--host") + 1] = "127.0.0.1"
    command[command.index("--port") + 1] = str(port)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT / "backend"), str(ROOT / "backend/tests"))),
               PYTHONUNBUFFERED="1", DATABASE_URL="sqlite:///:memory:")
    stdout_path, stderr_path = tmp_path / "stdout.log", tmp_path / "stderr.log"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen([sys.executable, "-m", *command, "--factory"],
                                   cwd=ROOT / "backend", env=env, stdout=stdout, stderr=stderr)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 20
                while True:
                    assert process.poll() is None, stderr_path.read_text(encoding="utf-8")
                    try:
                        if client.get("/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, "Uvicorn startup timed out"
                    time.sleep(.05)
                headers = {**session_headers("secret"), "Host": "radar.test:8443",
                           "Origin": "https://radar.test:8443", "X-Live-Exec-CSRF": "1"}
                assert client.put("/api/live-exec/config", headers=headers,
                                  json={"order_usd_max": 30, "expected_version": 2}).status_code == 200
                assert client.put("/api/live-exec/config", headers=headers,
                                  json={"unknown": ADMIN_TOKEN}).status_code == 400
                assert client.put("/api/live-exec/config", headers=headers,
                                  json={"order_usd_max": 31, "expected_version": 2}).status_code == 403
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    output = stdout_path.read_text(encoding="utf-8")
    records = [line for line in output.splitlines() if "live_exec_admin actor=" in line]
    assert len(records) == 3, output + stderr_path.read_text(encoding="utf-8")
    for record, status in zip(records, (200, 400, 403)):
        assert "actor=session:" in record
        assert "interface=PUT /config" in record
        assert f"status={status}" in record
    assert '"order_usd_max":30' in records[0]
    assert '"validation":"rejected"' in records[1]
    combined = output + stderr_path.read_text(encoding="utf-8")
    for secret in (ADMIN_TOKEN, READ_TOKEN, session_headers("secret")["Cookie"]):
        assert secret not in combined
