import asyncio
import base64
import json
import threading
import time
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services.fcm import SCOPE, TOKEN_URL, FcmClient, PushMessage


@pytest.fixture(scope="module")
def account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    account = {
        "project_id": "test-project",
        "client_email": "fixture@example.test",
        "private_key": pem,
    }
    return key, base64.b64encode(json.dumps(account).encode()).decode()


def decode(segment):
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


async def test_signed_jwt_shared_token_and_refresh_boundary(account):
    key, encoded = account
    grants, sends = [], []
    msg = PushMessage("leg_alert", "持仓失衡", "甲所 2 / 乙所 0", "id-A", "卡甲", "urgent")

    async def respond(request):
        assert set(request.extensions["timeout"].values()) == {10}
        if str(request.url) == TOKEN_URL:
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            header, payload, signature = form["assertion"][0].split(".")
            key.public_key().verify(
                decode(signature),
                f"{header}.{payload}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            assert json.loads(decode(header))["alg"] == "RS256"
            claims = json.loads(decode(payload))
            assert abs(claims["iat"] - time.time()) < 5
            assert claims == {
                "iss": "fixture@example.test",
                "scope": SCOPE,
                "aud": TOKEN_URL,
                "iat": claims["iat"],
                "exp": claims["iat"] + 3600,
            }
            grants.append(claims)
            await asyncio.sleep(0)
            return httpx.Response(
                200, json={"access_token": f"access-{len(grants)}", "expires_in": 3600}
            )
        assert (
            str(request.url) == "https://fcm.googleapis.com/v1/projects/test-project/messages:send"
        )
        assert request.headers["Authorization"] == f"Bearer access-{len(grants)}"
        sends.append(json.loads(request.content)["message"])
        return httpx.Response(200, json={"name": "accepted"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        fcm = FcmClient(encoded, client)
        start = time.monotonic()
        assert all(
            result.sent
            for result in await asyncio.gather(*(fcm.send(t, msg) for t in ["t1", "t2"]))
        )
        assert start + 3300 <= fcm._refresh_at <= time.monotonic() + 3300
        assert len(grants) == 1
        fcm._refresh_at = time.monotonic()
        assert (await fcm.send("t3", msg)).sent and len(grants) == 2
        assert sends[-1] == {
            "token": "t3",
            "notification": {"title": "持仓失衡", "body": "甲所 2 / 乙所 0"},
            "data": {"kind": "leg_alert", "card_id": "id-A", "card_name": "卡甲"},
            "android": {
                "priority": "high",
                "notification": {"channel_id": "urgent", "tag": "leg_alert:id-A"},
            },
        }


@pytest.mark.parametrize(
    "code,error,invalid,attempts",
    [
        (404, {}, True, 1),
        (400, {"details": [{"errorCode": "UNREGISTERED"}]}, True, 1),
        (
            400,
            {
                "status": "INVALID_ARGUMENT",
                "details": [{"fieldViolations": [{"field": "message.token"}]}],
            },
            True,
            1,
        ),
        (
            400,
            {"status": "INVALID_ARGUMENT", "message": "not a valid FCM registration token"},
            True,
            1,
        ),
        (400, {"status": "INVALID_ARGUMENT", "message": "data too large"}, False, 1),
        (
            400,
            {
                "status": "INVALID_ARGUMENT",
                "message": "package name does not match registration token",
            },
            False,
            1,
        ),
        (401, {}, False, 2),
        (429, {}, False, 1),
        (503, {}, False, 1),
    ],
)
async def test_provider_failures_are_bounded_and_redacted(
    account, code, error, invalid, attempts, caplog
):
    calls = []
    token = "device-value-that-must-stay-private"

    def respond(request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "private-access", "expires_in": 3600})
        calls.append(1)
        return httpx.Response(code, json={"error": {"message": token, **error}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await FcmClient(account[1], client).send(
            token, PushMessage("test", "测试", "测试")
        )
    assert not result.sent and result.invalid == invalid and len(calls) == attempts
    assert all(
        value not in caplog.text + result.reason for value in (token, account[1], "private-access")
    )


async def test_disabled_and_invalid_account_do_not_send(caplog):
    caplog.set_level("INFO")
    for encoded in ("", "private-invalid-configuration"):
        fcm = FcmClient(encoded)
        for _ in range(2):
            result = await fcm.send("private-token", PushMessage("test", "测试", "测试"))
            assert not result.sent and fcm._client is None
    assert caplog.text.count("FCM 未配置") == 1
    assert "private-" not in caplog.text


async def test_concurrent_auth_failures_share_retry_and_block_new_requests(account, monkeypatch):
    grants, sends, signers = [], [], []
    now = 100
    message = PushMessage("test", "测试", "测试")

    async def respond(request):
        await asyncio.sleep(0)
        if str(request.url) == TOKEN_URL:
            grants.append(request)
            return httpx.Response(200, json={"access_token": "private-access", "expires_in": 3600})
        sends.append(request)
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        fcm = FcmClient(account[1], client, clock=lambda: now)
        sign = fcm._assertion

        def assertion():
            signers.append(threading.get_ident())
            return sign()

        monkeypatch.setattr(fcm, "_assertion", assertion)
        results = await asyncio.gather(*(fcm.send(str(i), message) for i in range(10)))
        assert not any(result.sent or result.invalid for result in results)
        assert 1 <= len(grants) <= 2 and len(sends) <= 2
        assert len(grants) == len(signers) and threading.get_ident() not in signers
        before = len(grants), len(sends)
        now += 59
        await fcm.send("phone", message)
        assert (len(grants), len(sends)) == before
        now += 1
        await fcm.send("phone", message)
        assert (len(grants), len(sends)) == (before[0] + 1, before[1] + 1)


async def test_forbidden_backoff_is_capped_and_success_resets_it(account):
    now, status, grants, sends = 100, 403, 0, 0
    message = PushMessage("test", "测试", "测试")

    def respond(request):
        nonlocal grants, sends
        if str(request.url) == TOKEN_URL:
            grants += 1
            return httpx.Response(200, json={"access_token": "cached", "expires_in": 36000})
        assert request.headers["Authorization"] == "Bearer cached"
        sends += 1
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        fcm = FcmClient(account[1], client, clock=lambda: now)
        for count, delay in enumerate((60, 120, 240, 480, 900, 900), 1):
            assert not (await fcm.send("phone", message)).sent
            assert sends == count
            now += delay - 1
            await fcm.send("phone", message)
            assert sends == count
            now += 1
        status = 200
        assert (await fcm.send("phone", message)).sent
        status = 403
        assert not (await fcm.send("phone", message)).sent
        assert sends == 8
        status = 200
        now += 60
        assert (await fcm.send("phone", message)).sent
        assert grants == 1 and sends == 9


async def test_exchange_failure_backs_off_and_other_send_errors_keep_token(account, caplog):
    now, grants, sends = 100, 0, 0
    message = PushMessage("test", "测试", "测试")

    def respond(request):
        nonlocal grants, sends
        if str(request.url) == TOKEN_URL:
            grants += 1
            if grants == 1:
                return httpx.Response(400, text="private-oauth-error")
            return httpx.Response(200, json={"access_token": "cached", "expires_in": 3600})
        sends += 1
        return httpx.Response(503 if sends == 1 else 200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        fcm = FcmClient(account[1], client, clock=lambda: now)
        assert not (await fcm.send("phone", message)).sent
        now += 59
        assert not (await fcm.send("phone", message)).sent
        assert (grants, sends) == (1, 0)
        now += 1
        assert not (await fcm.send("phone", message)).sent
        assert (await fcm.send("phone", message)).sent
        assert (grants, sends) == (2, 2)
    assert "private-oauth-error" not in caplog.text
