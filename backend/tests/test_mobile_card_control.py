import asyncio
import base64
import logging
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from auth_helpers import TEST_TOTP_SECRET
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from test_mobile_api import mobile_app as mobile_app  # noqa: PLC0414 - pytest fixture

from app.core.config import Settings, get_settings
from app.core.totp import totp_at
from app.services.astro_client import AstroClientError
from app.services.fcm import SendResult

OPS = "/api/mobile/ops"
CONTROL = "/api/mobile/cards/control"
FREE = {"card_id": "Card_1-A", "field": "disable_open", "expected": False, "value": True}
PAIR = {
    "id": FREE["card_id"], "name": "OPENAI", "type": "FF", "status": True,
    "disableOpen": False, "disableClose": False, "buyEx": "hl", "aHlDex": "xyz",
    "aRawSymbol": "xyz:OPENAI", "openPosition": "1.025", "unknown": {"amounts": [1, 2]},
}
POSTS = {
    OPS + "/devices": {"code": "123456", "public_key": "invalid", "label": "手机"},
    OPS + "/devices/revoke": {"device_id": "missing"},
    OPS + "/challenge": {"device_id": "missing"},
    OPS + "/unlock": {"code": "123456"},
    OPS + "/settings": {"unlock_minutes": 5},
    CONTROL: FREE,
}


@dataclass
class Clock:
    now: float

    def code(self, offset=0):
        return totp_at(TEST_TOTP_SECRET, int(self.now // 30) + offset)

    def wrong_code(self):
        valid = {self.code(i) for i in (-1, 0, 1)}
        return next(code for code in ("000000", "000001", "000002", "000003") if code not in valid)


@pytest.fixture
def clock(monkeypatch):
    clock = Clock(time.time())
    monkeypatch.setattr(time, "time", lambda: clock.now)
    return clock


async def login(client, code, scope="mobile_readonly"):
    return await client.post("/api/auth/login", json={"password": "fixture", "code": code, "scope": scope})


@pytest.fixture
async def phone(mobile_app, clock):
    app, client = mobile_app
    assert (await login(client, clock.code(-1))).status_code == 200
    app.state.astro_client.list_pairs = AsyncMock(return_value=[PAIR.copy()])
    app.state.astro_client.update_pair = AsyncMock(return_value={"code": 0})
    return app, client


@pytest.fixture
def enabled(phone):
    app, client = phone
    app.state.settings = replace(app.state.settings, mobile_card_control_enabled=True, astro_dry_run_only=False)
    return app, client


@pytest.fixture
async def other_client(mobile_app):
    app, _ = mobile_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        yield client


def public_key(key):
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


async def bind(client, code, key, label="测试手机"):
    return await client.post(OPS + "/devices", json={"code": code, "public_key": public_key(key), "label": label})


@pytest.fixture
async def device(enabled, clock):
    _, client = enabled
    key = ec.generate_private_key(ec.SECP256R1())
    response = await bind(client, clock.code(), key)
    assert response.status_code == 200, response.text
    device_id = response.json()["device_id"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{22}", device_id)
    return key, device_id


async def challenge(client, device_id):
    response = await client.post(OPS + "/challenge", json={"device_id": device_id})
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(base64.b64decode(data["nonce"], validate=True)) == 32
    assert data["expires_at"] == time.time() + 60
    return data


def proof(key, device_id, issued):
    message = f"astro-mobile-unlock\n{issued['challenge_id']}\n{issued['nonce']}".encode()
    signature = key.sign(message, ec.ECDSA(hashes.SHA256()))
    return {"device_id": device_id, "challenge_id": issued["challenge_id"],
            "signature": base64.b64encode(signature).decode()}


async def unlock_code(client, code):
    return await client.post(OPS + "/unlock", json={"code": code})


async def test_default_status_and_env_switch(phone, monkeypatch):
    _, client = phone
    response = await client.get(OPS + "/status")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "unlocked_until": None, "unlock_minutes": 5, "devices": []}
    assert Settings().mobile_card_control_enabled is False
    monkeypatch.setattr("app.core.config.find_dotenv", lambda **kwargs: "")
    monkeypatch.setenv("MOBILE_CARD_CONTROL_ENABLED", "true")
    assert get_settings.__wrapped__().mobile_card_control_enabled is True
    monkeypatch.setenv("MOBILE_CARD_CONTROL_ENABLED", "false")
    assert get_settings.__wrapped__().mobile_card_control_enabled is False


@pytest.mark.parametrize("switch,dry_run", [(False, False), (True, True)])
async def test_disabled_rejects_every_write_before_side_effects(phone, switch, dry_run, clock):
    app, client = phone
    app.state.settings = replace(app.state.settings, mobile_card_control_enabled=switch, astro_dry_run_only=dry_run)
    for path, payload in POSTS.items():
        response = await client.post(path, json=payload)
        assert response.status_code == 503, (path, response.text)
        assert response.json() == {"detail": "手机改卡未启用"}
    assert (await client.get(OPS + "/status")).json()["enabled"] is False
    assert (await client.get(OPS + "/settings")).json() == {"unlock_minutes": 5}
    app.state.astro_client.list_pairs.assert_not_awaited()
    app.state.astro_client.list_pairs_cached.assert_not_awaited()
    app.state.astro_client.update_pair.assert_not_awaited()
    app.state.settings = replace(app.state.settings, mobile_card_control_enabled=True, astro_dry_run_only=False)
    assert (await unlock_code(client, clock.code())).status_code == 200


async def test_session_required_and_exact_readonly_post_permissions(enabled, other_client):
    _, client = enabled
    other_client.headers["X-Api-Token"] = "a" * 64
    for path, payload in POSTS.items():
        assert (await other_client.post(path, json=payload)).status_code == 401
        assert (await client.post(path + "/", json=payload)).status_code == 403
    for path in (OPS + "/settings", OPS + "/status"):
        assert (await other_client.get(path)).status_code == 401
    for method, path in (("PUT", CONTROL), ("POST", OPS + "/extra"),
                         ("POST", "/api/astro/instrument/card/update")):
        assert (await client.request(method, path)).status_code == 403


async def test_binding_and_unlock_share_totp_replay_with_login(enabled, clock):
    app, client = enabled
    key = ec.generate_private_key(ec.SECP256R1())
    assert (await bind(client, clock.wrong_code(), key)).status_code == 401
    assert (await bind(client, clock.code(-1), key)).status_code == 401  # consumed by login
    responses = await asyncio.gather(bind(client, clock.code(), key), bind(client, clock.code(), key))
    assert sorted(r.status_code for r in responses) == [200, 401]
    device_id = next(r for r in responses if r.status_code == 200).json()["device_id"]
    status = (await client.get(OPS + "/status")).json()
    assert status == {"enabled": True, "unlocked_until": None, "unlock_minutes": 5,
                      "devices": [{"device_id": device_id, "label": "测试手机", "created_at": clock.now}]}
    assert (await unlock_code(client, clock.code())).status_code == 401
    # A different IP still cannot replay either code and has its own failure budget.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.2", 1)),
                                 base_url="https://test") as different_ip:
        assert (await login(different_ip, clock.code())).status_code == 401
        assert (await unlock_code(client, clock.code(1))).status_code == 200
        assert (await login(different_ip, clock.code(1))).status_code == 401


async def test_login_binding_and_bad_device_signatures_share_ip_lock(enabled, other_client, clock, device):
    app, client = enabled
    key, device_id = device
    wrong = clock.wrong_code()
    assert (await login(other_client, wrong)).status_code == 401
    assert (await bind(client, wrong, key)).status_code == 401
    issued = await challenge(client, device_id)
    bad = proof(ec.generate_private_key(ec.SECP256R1()), device_id, issued)
    assert (await client.post(OPS + "/unlock", json=bad)).status_code == 401
    assert (await unlock_code(client, wrong)).status_code == 401
    assert (await login(other_client, wrong)).status_code == 401  # fifth failure locks the IP
    clock.now += 899
    assert (await bind(client, clock.code(), key)).status_code == 429
    signed = proof(key, device_id, await challenge(client, device_id))
    assert (await client.post(OPS + "/unlock", json=signed)).status_code == 429
    assert (await login(other_client, clock.code())).status_code == 401
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.2", 1)),
                                 base_url="https://test") as different_ip:
        assert (await login(different_ip, clock.code(-1))).status_code == 200
    clock.now += 1
    assert (await client.post(OPS + "/unlock", json=signed)).status_code == 401
    assert (await unlock_code(client, clock.code())).status_code == 200


async def test_mobile_failures_lock_login_and_binding(enabled, clock, other_client):
    _, client = enabled
    key = ec.generate_private_key(ec.SECP256R1())
    for _ in range(4):
        assert (await bind(client, clock.wrong_code(), key)).status_code == 401
    assert (await unlock_code(client, clock.wrong_code())).status_code in (401, 429)
    assert (await login(other_client, clock.code())).status_code == 401
    assert (await bind(client, clock.code(), key)).status_code == 429


async def test_device_limit_revocation_and_pending_proof(enabled, clock, device, tmp_path):
    _, client = enabled
    key, device_id = device
    revoke = {"device_id": device_id}
    assert (await client.post(OPS + "/devices/revoke", json=revoke)).status_code == 403
    for index in range(2):
        clock.now += 30
        assert (await bind(client, clock.code(), ec.generate_private_key(ec.SECP256R1()), str(index))).status_code == 200
    clock.now += 30
    assert (await bind(client, clock.code(), key)).status_code == 409
    assert (await unlock_code(client, clock.code(1))).status_code == 200
    issued = await challenge(client, device_id)
    assert (await client.post(OPS + "/devices/revoke", json=revoke)).status_code == 200
    assert (await client.post(OPS + "/challenge", json=revoke)).status_code == 404
    assert (await client.post(OPS + "/unlock", json=proof(key, device_id, issued))).status_code == 401
    assert device_id not in {d["device_id"] for d in (await client.get(OPS + "/status")).json()["devices"]}
    with closing(sqlite3.connect(tmp_path / "radar.db")) as db:
        assert db.execute("SELECT revoked_at FROM mobile_ops_devices WHERE device_id=?", (device_id,)).fetchone() == (clock.now,)
    clock.now += 60
    assert (await bind(client, clock.code(), key)).status_code == 200


async def test_invalid_keys_labels_and_inputs_are_private(enabled, clock):
    _, client = enabled
    key = ec.generate_private_key(ec.SECP256R1())
    bad_key = ec.generate_private_key(ec.SECP384R1())
    cases = [
        (OPS + "/devices", {"code": clock.code(), "label": "secret" * 7, "public_key": public_key(key)}),
        (OPS + "/devices", {"code": clock.code(), "label": "手机", "public_key": public_key(bad_key)}),
        (OPS + "/devices", {"code": clock.code(), "label": "手机", "public_key": public_key(ed25519.Ed25519PrivateKey.generate())}),
        (OPS + "/devices", {"code": clock.code(), "label": "手机", "public_key": "private-invalid!"}),
        (OPS + "/unlock", {"code": clock.code(), "device_id": "private-extra"}),
        (OPS + "/unlock", {"challenge_id": "private-missing"}),
        (CONTROL, dict(FREE, value="true")), (CONTROL, dict(FREE, expected=0)),
        (CONTROL, dict(FREE, field="private_field")), (CONTROL, dict(FREE, card_id="private/id")),
        (CONTROL, dict(FREE, card_id="A" * 65)), (CONTROL, dict(FREE, card_id="A\n")),
    ]
    for path, body in cases:
        response = await client.post(path, json=body)
        assert response.status_code == 422, (path, response.text)
        detail = response.json()["detail"]
        assert isinstance(detail, str) and re.search(r"[\u4e00-\u9fff]", detail)
        assert "private" not in detail and clock.code() not in detail
        assert body.get("public_key", "secret") not in detail
    assert (await bind(client, clock.code(), key, "字" * 40)).status_code == 200


@pytest.mark.parametrize("invalid", ["session", "device", "expiry", "bad_base64", "bad_signature"])
async def test_challenge_failures_consume_it(enabled, device, clock, other_client, invalid):
    _, client = enabled
    key, device_id = device
    issued = await challenge(client, device_id)
    valid = proof(key, device_id, issued)
    submitter, body = client, valid.copy()
    if invalid == "session":
        assert (await login(other_client, clock.code(1), "full")).status_code == 200
        submitter = other_client
    elif invalid == "device":
        other = await bind(client, clock.code(1), ec.generate_private_key(ec.SECP256R1()))
        body["device_id"] = other.json()["device_id"]
    elif invalid == "expiry":
        clock.now += 60
    elif invalid == "bad_base64":
        body["signature"] = "private!!!"
    else:
        body["signature"] = base64.b64encode(key.sign(b"wrong domain", ec.ECDSA(hashes.SHA256()))).decode()
    assert (await submitter.post(OPS + "/unlock", json=body)).status_code == 401
    assert (await client.post(OPS + "/unlock", json=valid)).status_code == 401
    assert (await client.get(OPS + "/status")).json()["unlocked_until"] is None


async def test_challenge_capacity_and_single_use_under_concurrency(enabled, device, clock):
    _, client = enabled
    key, device_id = device
    issued = [await challenge(client, device_id) for _ in range(101)]
    assert len({c["nonce"] for c in issued}) == len(issued)
    assert (await client.post(OPS + "/unlock", json=proof(key, device_id, issued[0]))).status_code == 401
    body = proof(key, device_id, issued[1])
    responses = await asyncio.gather(*(client.post(OPS + "/unlock", json=body) for _ in range(2)))
    assert sorted(r.status_code for r in responses) == [200, 401]
    assert next(r for r in responses if r.status_code == 200).json() == {"unlocked_until": clock.now + 300}


async def test_malformed_unlock_consumes_the_presented_challenge(enabled, device, clock):
    _, client = enabled
    key, device_id = device
    body = proof(key, device_id, await challenge(client, device_id))
    response = await client.post(OPS + "/unlock", json=dict(body, code=clock.code()))
    assert response.status_code == 422 and body["signature"] not in response.text
    assert (await client.post(OPS + "/unlock", json=body)).status_code == 401


async def test_settings_are_persisted_and_apply_only_to_new_windows(enabled, clock, other_client, tmp_path):
    _, client = enabled
    assert (await client.post(OPS + "/settings", json={"unlock_minutes": 30})).status_code == 403
    assert (await unlock_code(client, clock.code())).json() == {"unlocked_until": clock.now + 300}
    for value in (0, 31, 1.5, True, "3"):
        assert (await client.post(OPS + "/settings", json={"unlock_minutes": value})).status_code == 422
    for value in (1, 30):
        assert (await client.post(OPS + "/settings", json={"unlock_minutes": value})).status_code == 200
    with closing(sqlite3.connect(tmp_path / "radar.db")) as db:
        assert db.execute("SELECT singleton, unlock_minutes FROM mobile_ops_settings").fetchall() == [(1, 30)]
    assert (await login(other_client, clock.code(1), "full")).status_code == 200
    assert (await other_client.get(OPS + "/settings")).json() == {"unlock_minutes": 30}
    assert (await other_client.post(OPS + "/settings", json={"unlock_minutes": 2})).status_code == 403
    assert (await client.get(OPS + "/status")).json()["unlocked_until"] == clock.now + 300
    clock.now += 300
    assert (await client.get(OPS + "/status")).json()["unlocked_until"] is None
    assert (await client.post(OPS + "/settings", json={"unlock_minutes": 2})).status_code == 403
    assert (await unlock_code(other_client, clock.code())).json() == {"unlocked_until": clock.now + 1800}


async def test_only_disable_open_false_to_true_is_free(enabled):
    app, client = enabled
    for field in ("status", "disable_open", "disable_close"):
        for old in (True, False):
            for new in (True, False):
                if (field, old, new) == ("disable_open", False, True):
                    continue
                response = await client.post(CONTROL, json=dict(FREE, field=field, expected=old, value=new))
                assert response.status_code == 403
                assert response.json() == {"detail": "请先解锁"}
    app.state.astro_client.list_pairs.assert_not_awaited()
    app.state.astro_client.update_pair.assert_not_awaited()
    assert (await client.post(CONTROL, json=FREE)).json() == {
        "ok": True, "changed": True, "card_id": FREE["card_id"], "field": "disable_open", "value": True,
    }


@pytest.mark.parametrize("field,astro_field,value,auth", [
    ("disable_open", "disableOpen", True, "免解锁"), ("status", "status", False, "验证码"),
    ("disable_close", "disableClose", True, "设备签名"),
])
async def test_update_uses_locked_latest_pair_and_audits_method(enabled, clock, field, astro_field, value, auth, caplog):
    app, client = enabled
    if auth == "验证码":
        assert (await unlock_code(client, clock.code())).status_code == 200
    elif auth == "设备签名":
        key = ec.generate_private_key(ec.SECP256R1())
        device_id = (await bind(client, clock.code(), key)).json()["device_id"]
        signed = proof(key, device_id, await challenge(client, device_id))
        assert (await client.post(OPS + "/unlock", json=signed)).status_code == 200

    lock = app.state.astro_alert_service.pair_lock(PAIR["name"])
    astro = app.state.astro_client
    latest = dict(PAIR, openPosition="1.037", custom=[{"raw": "xyz:OPENAI"}])
    reads_in_lock = []

    async def read():
        reads_in_lock.append(lock.locked())
        return [dict(PAIR, id="other-id"), latest if lock.locked() else PAIR.copy()]

    async def write(payload):
        assert lock.locked()
        assert payload == dict(latest, **{astro_field: value})

    astro.list_pairs.side_effect = read
    astro.update_pair.side_effect = write
    with caplog.at_level(logging.INFO, logger="astro_card_update"):
        response = await client.post(CONTROL, json=dict(FREE, field=field, expected=not value, value=value))
    assert response.json() == {"ok": True, "changed": True, "card_id": FREE["card_id"], "field": field, "value": value}
    assert any(reads_in_lock) and latest[astro_field] is not value
    astro.list_pairs_cached.assert_not_awaited()
    astro.update_pair.assert_awaited_once()
    logs = [r.getMessage() for r in caplog.records if r.name == "astro_card_update"]
    assert len(logs) == 1 and all(word in logs[0] for word in (PAIR["name"], field, auth))
    events = (await client.get("/api/mobile/alerts")).json()
    assert len(events) == 1
    event = events[0]
    assert (event["kind"], event["level"], event["card_name"]) == ("card_control", "urgent", PAIR["name"])
    if field == "disable_open":
        assert event["title"] == "手机修改：OPENAI 禁止开仓 关→开"


async def test_conflicts_missing_card_and_noop_do_not_write_or_audit(enabled, clock):
    app, client = enabled
    assert (await unlock_code(client, clock.code())).status_code == 200
    astro = app.state.astro_client
    for current in (True, 0, None, "false"):
        astro.list_pairs.return_value = [dict(PAIR, disableOpen=current)]
        response = await client.post(CONTROL, json=FREE)
        assert response.status_code == 409
        assert response.json() == {"detail": "卡片已变化，请刷新"}
    astro.list_pairs.return_value = [PAIR.copy(), dict(PAIR, name="OTHER")]
    assert (await client.post(CONTROL, json=FREE)).status_code == 409
    astro.list_pairs.return_value = [dict(PAIR, id="other")]
    assert (await client.post(CONTROL, json=FREE)).status_code == 404
    astro.list_pairs.return_value = [PAIR.copy()]
    response = await client.post(CONTROL, json=dict(FREE, value=False))
    assert response.json() == {"ok": True, "changed": False, "card_id": FREE["card_id"], "field": FREE["field"], "value": False}
    astro.list_pairs.side_effect = [[PAIR.copy()], [dict(PAIR, name="RENAMED")]]
    assert (await client.post(CONTROL, json=FREE)).status_code == 409
    astro.update_pair.assert_not_awaited()
    assert (await client.get("/api/mobile/alerts")).json() == []


async def test_unlock_expires_while_waiting_for_card_lock(enabled, clock):
    app, client = enabled
    assert (await unlock_code(client, clock.code())).status_code == 200
    located = asyncio.Event()

    async def read():
        located.set()
        return [PAIR.copy()]

    app.state.astro_client.list_pairs.side_effect = read
    async with app.state.astro_alert_service.pair_lock(PAIR["name"]):
        pending = asyncio.create_task(client.post(CONTROL, json=dict(FREE, field="status", expected=True, value=False)))
        await asyncio.wait_for(located.wait(), timeout=5)
        clock.now += 300
    response = await pending
    assert response.status_code == 403 and response.json() == {"detail": "请先解锁"}
    app.state.astro_client.update_pair.assert_not_awaited()


@pytest.mark.parametrize("operation", ["list_pairs", "update_pair"])
async def test_upstream_errors_keep_status_and_log_private_message(enabled, operation, caplog):
    app, client = enabled
    message = "来自上游的私有响应正文"
    getattr(app.state.astro_client, operation).side_effect = AstroClientError(message, 504)
    response = await client.post(CONTROL, json=FREE)
    assert response.status_code == 504 and response.json() == {"detail": "Astro 拒绝修改（HTTP 504）"}
    assert any(r.levelno == logging.WARNING and message in r.getMessage() for r in caplog.records)
    assert (await client.get("/api/mobile/alerts")).json() == []


@pytest.mark.parametrize("path,method", [("/status", "devices"), ("/settings", "unlock_minutes")])
async def test_closed_ops_database_returns_503(enabled, monkeypatch, path, method):
    app, client = enabled
    monkeypatch.setattr(app.state.mobile_card_control.repo, method, AsyncMock(side_effect=ValueError("Connection closed")))
    response = await client.get(OPS + path)
    assert response.status_code == 503
    assert response.json() == {"detail": "手机改卡服务暂不可用"}


async def test_pushes_immediately_to_all_devices_even_if_one_send_fails(enabled):
    app, client = enabled
    kinds = dict.fromkeys(("near_open", "near_close", "fill", "leg_alert", "big_spread", "stale"), False)
    tokens = {"device-one", "device-two"}
    for token in tokens:
        response = await client.post("/api/mobile/push/register", json={"token": token, "kinds": kinds, "threshold_pp": 1.0})
        assert response.status_code == 200

    async def send(token, message):
        if token == "device-one":
            raise RuntimeError("模拟推送异常")
        return SendResult(True, "")

    sender = AsyncMock(side_effect=send)
    app.state.mobile_push.fcm = SimpleNamespace(enabled=True, send=sender)
    assert (await client.post(CONTROL, json=FREE)).json()["changed"] is True
    assert {call.args[0] for call in sender.await_args_list} == tokens
    for call in sender.await_args_list:
        message = call.args[1]
        assert (message.kind, message.channel, message.card_id) == ("card_control", "urgent", FREE["card_id"])
    assert len((await client.get("/api/mobile/alerts")).json()) == 1
    app.state.mobile_push.repo.scan = AsyncMock(side_effect=RuntimeError("设备列表暂不可用"))
    assert (await client.post(CONTROL, json=FREE)).json()["changed"] is True


async def test_audit_database_closed_after_astro_write_still_notifies(enabled):
    app, client = enabled
    app.state.mobile_alert_repo.record = AsyncMock(side_effect=ValueError("Connection closed"))
    push = app.state.mobile_push.send_urgent = AsyncMock()
    response = await client.post(CONTROL, json=FREE)
    assert response.status_code == 503
    assert response.json() == {"detail": "卡片已修改，提醒记录写入失败，请刷新"}
    app.state.astro_client.update_pair.assert_awaited_once()
    push.assert_awaited_once()


async def test_rolling_rate_limit_is_per_session(enabled, other_client, clock):
    app, client = enabled
    for _ in range(20):
        assert (await client.post(CONTROL, json=FREE)).status_code == 200
    clock.now += 59
    assert (await client.post(CONTROL, json=FREE)).status_code == 429
    assert app.state.astro_client.update_pair.await_count == 20
    assert (await login(other_client, clock.code(), "full")).status_code == 200
    assert (await other_client.post(CONTROL, json=FREE)).status_code == 200
    clock.now += 1
    assert (await client.post(CONTROL, json=FREE)).status_code == 200
