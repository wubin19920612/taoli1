import hashlib
import hmac

import httpx
import pytest

from app.services.astro_client import AstroClientError, AstroSdkClient, AstroSdkConfig


@pytest.mark.asyncio
async def test_list_pairs_sends_signed_raw_body_request() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content.decode("utf-8")
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {
                        "id": "Ab12Cd34Ef",
                        "name": "BTC",
                        "type": "FF",
                    }
                ],
            },
        )

    client = AstroSdkClient(
        AstroSdkConfig(
            base_url="https://astro.example",
            admin_prefix="admin",
            api_key="secret-key",
            verify_tls=False,
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client._timestamp = lambda: 1234567890000  # type: ignore[method-assign]
    client._nonce = lambda: "nonce-1234567890"  # type: ignore[method-assign]

    pairs = await client.list_pairs()

    assert pairs[0]["name"] == "BTC"
    assert captured["path"] == "/admin/api/config/sdk-update-pair"
    assert captured["body"] == '{"action":"list"}'
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-timestamp"] == "1234567890000"
    assert headers["x-nonce"] == "nonce-1234567890"
    canonical = "\n".join(
        [
            "1234567890000",
            "nonce-1234567890",
            "POST",
            "/admin/api/config/sdk-update-pair",
            '{"action":"list"}',
        ]
    )
    expected_sign = hmac.new(
        b"secret-key",
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert headers["x-sign"] == expected_sign


@pytest.mark.asyncio
async def test_add_pair_sends_signed_add_payload() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"code": 0, "message": "ok"})

    client = AstroSdkClient(
        AstroSdkConfig(
            base_url="https://astro.example",
            admin_prefix="admin",
            api_key="secret-key",
            verify_tls=False,
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    pair = {"name": "BTC", "type": "FF", "status": False, "disableOpen": True}

    result = await client.add_pair(pair)

    assert result["code"] == 0
    assert captured["path"] == "/admin/api/config/sdk-update-pair"
    assert captured["body"] == (
        '{"action":"add","pair":{"name":"BTC","type":"FF",'
        '"status":false,"disableOpen":true}}'
    )


@pytest.mark.asyncio
async def test_update_pair_sends_signed_update_payload() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"code": 0, "message": "ok"})

    client = AstroSdkClient(
        AstroSdkConfig(
            base_url="https://astro.example",
            admin_prefix="admin",
            api_key="secret-key",
            verify_tls=False,
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    pair = {"name": "BTC", "type": "FF", "openPosition": "0.008000"}

    result = await client.update_pair(pair)

    assert result["code"] == 0
    assert captured["body"] == (
        '{"action":"update","pair":{"name":"BTC","type":"FF",'
        '"openPosition":"0.008000"}}'
    )


@pytest.mark.asyncio
async def test_add_pair_raises_for_nonzero_sdk_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 1001, "message": "name already exists"})

    client = AstroSdkClient(
        AstroSdkConfig(
            base_url="https://astro.example",
            admin_prefix="admin",
            api_key="secret-key",
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(AstroClientError) as exc_info:
        await client.add_pair({"name": "BTC"})

    assert "name already exists" in exc_info.value.message


def test_paths_normalize_admin_prefix_slashes() -> None:
    config = AstroSdkConfig(
        base_url="https://astro.example",
        admin_prefix="/admin/",
        api_key="secret-key",
    )

    assert config.list_path == "/admin/api/config/sdk-update-pair"
    assert config.message_path == "/admin/api/config/sdk-send-message"


@pytest.mark.asyncio
async def test_list_pairs_wraps_http_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad signature")

    client = AstroSdkClient(
        AstroSdkConfig(
            base_url="https://astro.example",
            admin_prefix="admin",
            api_key="secret-key",
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(AstroClientError) as exc_info:
        await client.list_pairs()

    assert exc_info.value.status_code == 401
    assert "bad signature" in exc_info.value.message
