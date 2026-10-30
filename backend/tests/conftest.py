import os
import socket
import sys
from pathlib import Path

import httpx
import httpx._transports.default as httpx_transport
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture
async def astro_attempts_for_services(monkeypatch):
    """Give existing standalone service regressions a real, isolated ledger."""
    from app.db.database import connect_database
    from app.db.schema import initialize_schema
    from app.services.astro_alerts import AstroAlertService
    from app.services.astro_card_attempts import AstroCardAttemptRepository

    db = await connect_database(":memory:")
    await initialize_schema(db)
    repository = AstroCardAttemptRepository(db)
    original_init = AstroAlertService.__init__

    def initialize(self, *args, **kwargs):
        kwargs.setdefault("attempts", repository)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(AstroAlertService, "__init__", initialize)
    try:
        yield repository
    finally:
        await db.close()


@pytest.fixture(scope="session")
def offline_tls_contexts():
    # Parsing the same CA bundle for every client/proxy dominates Windows tests.
    # Retain certificate verification and explicit TLS options; reuse only the
    # default verified context within this offline test session.
    return {}


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch, request, offline_tls_contexts):
    if request.node.get_closest_marker("network") is not None:
        yield
        return

    attempts = []

    def block(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Offline test attempted external network")

    def is_loopback(host):
        return host in {"localhost", "127.0.0.1", "::1"}

    def guard_socket(original):
        def guarded(sock, address, *args, **kwargs):
            # Windows asyncio implements socketpair through loopback.
            if isinstance(address, tuple) and is_loopback(address[0]):
                return original(sock, address, *args, **kwargs)
            return block()
        return guarded

    original_create_connection = socket.create_connection

    def create_connection(address, *args, **kwargs):
        if isinstance(address, tuple) and is_loopback(address[0]):
            return original_create_connection(address, *args, **kwargs)
        return block()

    original_http = httpx.HTTPTransport.handle_request
    original_async_http = httpx.AsyncHTTPTransport.handle_async_request

    def handle_request(transport, http_request):
        if is_loopback(http_request.url.host):
            return original_http(transport, http_request)
        return block()

    async def handle_async_request(transport, http_request):
        if is_loopback(http_request.url.host):
            return await original_async_http(transport, http_request)
        return block()

    original_ssl_context = httpx_transport.create_ssl_context

    def create_ssl_context(verify=True, cert=None, trust_env=True):
        if verify is not True or cert is not None:
            return original_ssl_context(verify=verify, cert=cert, trust_env=trust_env)
        key = (
            trust_env,
            os.environ.get("SSL_CERT_FILE") if trust_env else None,
            os.environ.get("SSL_CERT_DIR") if trust_env else None,
        )
        if key not in offline_tls_contexts:
            offline_tls_contexts[key] = original_ssl_context(trust_env=trust_env)
        return offline_tls_contexts[key]

    monkeypatch.setattr(socket.socket, "connect", guard_socket(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard_socket(socket.socket.connect_ex))
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle_async_request)
    monkeypatch.setattr(httpx_transport, "create_ssl_context", create_ssl_context)
    yield
    # Fail even if application retry/fallback code swallowed the first exception.
    assert not attempts, "Offline test attempted external network"
