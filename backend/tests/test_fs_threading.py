import json
import threading
from datetime import UTC, datetime

import httpx
import pytest

from app.services.fs_bybit import BybitFsProvider, FsDataError
from app.services.fs_public import PublicFsProvider


@pytest.mark.parametrize("exchange", ["binance", "okx", "bitget", "gate", "bybit"])
async def test_get_json_decoding_runs_in_worker_thread(monkeypatch, exchange):
    main_thread = threading.get_ident()
    decoded_in = []
    original_loads = json.loads

    def loads(content, *args, **kwargs):
        decoded_in.append(threading.get_ident())
        return original_loads(content, *args, **kwargs)

    payload = {"symbols": []}
    if exchange in {"okx", "bitget"}:
        payload = {"code": "0" if exchange == "okx" else "00000", "data": payload}
    elif exchange == "bybit":
        payload = {"retCode": 0, "result": payload,
                   "time": int(datetime.now(UTC).timestamp() * 1000)}
    monkeypatch.setattr(json, "loads", loads)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as client:
        provider = (BybitFsProvider(client) if exchange == "bybit"
                    else PublicFsProvider(exchange, None, client))
        data, at = await provider.get("/v5/market/tickers" if exchange == "bybit" else "/info")
        assert data == {"symbols": []}
        assert at.tzinfo == UTC
    assert decoded_in and all(ident != main_thread for ident in decoded_in)


@pytest.mark.parametrize("exchange,status,body,reason", [
    ("binance", 500, b"{}", "public_query_failed"),
    ("binance", 200, b"{bad", "public_query_failed"),
    ("okx", 200, b'{"code":"1","msg":"private"}', "exchange_query_failed"),
    ("bitget", 200, b'{"code":"bad","msg":"private"}', "exchange_query_failed"),
    ("bybit", 401, b"{}", "permission_denied"),
    ("bybit", 403, b"{}", "permission_denied"),
    ("bybit", 429, b"{}", "rate_limited"),
    ("bybit", 500, b"{}", "query_failed"),
    ("bybit", 200, b"{bad", "query_failed"),
    ("bybit", 200, b'{"retCode":10005,"retMsg":"private"}', "permission_denied"),
    ("bybit", 200, b'{"retCode":10006,"retMsg":"private"}', "rate_limited"),
    ("bybit", 200, b'{"retCode":1,"retMsg":"private"}', "exchange_query_failed"),
    ("bybit", 200, b'{"retCode":0,"result":{}}', "missing_source_time"),
    ("bybit", 200, b'{"retCode":0,"result":{},"time":1}', "stale_response"),
])
async def test_get_error_mapping_unchanged(exchange, status, body, reason):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, content=body)
    )) as client:
        provider = (BybitFsProvider(client) if exchange == "bybit"
                    else PublicFsProvider(exchange, None, client))
        with pytest.raises(FsDataError, match=f"^{reason}$"):
            await provider.get("/v5/market/tickers" if exchange == "bybit" else "/info")
