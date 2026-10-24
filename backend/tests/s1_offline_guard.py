"""Opt-in pytest plugin: fail tests that attempt real external requests."""

import socket

import httpx
import pytest


@pytest.fixture(autouse=True)
def s1_no_external_network(monkeypatch):
    attempts = []

    def block(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("S1 offline test attempted external network")

    async def async_block(*args, **kwargs):
        return block()

    original_connect = socket.socket.connect

    def connect(sock, address):
        # Windows asyncio implements socketpair through the loopback interface.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        return block()

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", block)
    monkeypatch.setattr(socket, "create_connection", block)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", block)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_block)
    yield
    assert not attempts, "S1 offline test attempted external network"
