import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from auth_helpers import TEST_TOTP_SECRET

from app.core.config import Settings
from app.core.totp import totp_at
from app.main import create_app
from app.services.astro_client import AstroClientError
from app.services.fcm import SendResult
from app.services.feishu import FeishuConfig, FeishuNotifier
from app.services.mobile_push import MobilePushService

KINDS = dict.fromkeys(("near_open", "near_close", "fill", "leg_alert", "big_spread", "stale"), True)


def card(**changes):
    values = {
        "id": "A",
        "name": "OPENAI",
        "holding": False,
        "open_auto": True,
        "close_auto": True,
        "gap_to_open": -1,
        "gap_to_close": -1,
        "open_spread_now": 3.42,
        "close_spread_now": 3.58,
        "open_condition": 3.5,
        "close_condition": 3.5,
        "qty_a": 0,
        "qty_b": 0,
        "state": "running",
        "leg_imbalance": False,
        "buy_ex": "bitget",
        "sell_ex": "rh-lighter",
        "dex_a": None,
        "dex_b": None,
        "raw_symbol_a": None,
        "raw_symbol_b": None,
    }
    return SimpleNamespace(**(values | changes))


def device(token="phone", threshold=0.1, **kinds):
    return {
        "token": token,
        "session_hash": "session",
        "kinds": KINDS | kinds,
        "threshold_pp": threshold,
        "last_error": None,
        "updated_at": 0,
    }


class Scanner:
    def __init__(self, devices):
        self.sent = []
        self.summary = SimpleNamespace(stale=False, cards=[])
        repo = SimpleNamespace(scan=AsyncMock(return_value=devices))
        fcm = SimpleNamespace(enabled=True, send=self.send)
        self.service = MobilePushService(
            repo, fcm, AsyncMock(return_value=self.summary), lambda: self.now
        )

    async def send(self, token, message):
        self.sent.append((token, message))
        return SendResult(True, "已发送")

    async def scan(self, now, current=None, stale=False):
        self.now = now
        self.summary.cards = [current or card()]
        self.summary.stale = stale
        await self.service.scan()


@pytest.mark.parametrize("kind", ["near_open", "near_close"])
async def test_threshold_edges_device_filter_and_ten_minute_cooldown(kind):
    s = Scanner([device("wide", 0.5), device("narrow"), device("off", **{kind: False})])
    holding = kind == "near_close"

    def current(gap):
        return card(
            holding=holding,
            qty_a=int(holding),
            qty_b=int(holding),
            **{"gap_to_close" if holding else "gap_to_open": gap},
        )

    await s.scan(0, current(-0.08))
    await s.scan(10, current(-0.08))
    assert not s.sent
    await s.scan(20, current(0))
    await s.scan(30, current(-0.5))
    await s.scan(40, current(-0.1))
    assert [(t, m.kind, m.channel) for t, m in s.sent] == [
        ("wide", kind, "normal"),
        ("narrow", kind, "normal"),
    ]
    assert "条件 3.50%" in s.sent[0][1].body
    await s.scan(50, current(0))
    await s.scan(60, current(-0.01))
    assert len(s.sent) == 2
    await s.scan(620, current(-1))
    await s.scan(630, current(-0.08))
    assert [t for t, _ in s.sent] == ["wide", "narrow", "wide"]


@pytest.mark.parametrize(
    "initial,current,kind",
    [
        (card(state="paused", gap_to_open=-0.05), card(gap_to_open=-0.05), "near_open"),
        (card(open_auto=False, gap_to_open=-0.05), card(gap_to_open=-0.05), "near_open"),
        (
            card(holding=True, close_auto=False, gap_to_close=-0.05),
            card(holding=True, gap_to_close=-0.05),
            "near_close",
        ),
    ],
)
async def test_eligibility_changes_are_edges(initial, current, kind):
    s = Scanner([device()])
    await s.scan(0, initial)
    await s.scan(10, current)
    assert [m.kind for _, m in s.sent] == [kind]


async def test_fill_open_add_reduce_close_and_leg_states():
    s = Scanner([device(), device("off", fill=False, leg_alert=False)])
    await s.scan(0)
    await s.scan(10, card(qty_a=1, holding=True, leg_imbalance=True))
    assert [(m.kind, m.channel) for _, m in s.sent] == [("fill", "normal"), ("leg_alert", "urgent")]
    assert "开仓成交 +1 / +0" in s.sent[0][1].title
    assert s.sent[1][1].body == "bitget 1 / rh-lighter 0"
    await s.scan(20, card(qty_a=2, qty_b=2, holding=True, state="paused"))
    assert len(s.sent) == 2
    await s.scan(610, card(qty_a=3, qty_b=3, holding=True, state="close_disabled"))
    await s.scan(620, card(qty_a=3, qty_b=3, holding=True, state="close_disabled"))
    await s.scan(1210, card(qty_a=2, qty_b=3, holding=True, state="paused"))
    await s.scan(1810)
    assert all(t == "phone" for t, _ in s.sent)
    assert [m.title.split()[1] for _, m in s.sent] == [
        "开仓成交",
        "单腿持仓",
        "加仓成交",
        "持仓已禁平",
        "减仓成交",
        "持仓已暂停",
        "平仓成交",
    ]


async def test_stale_requires_continuity_and_recovery_pairs_with_delivered_alarm():
    s = Scanner([device(), device("off", stale=False)])
    for now, stale in [(0, True), (59, True), (60, False), (70, True), (129, True)]:
        await s.scan(now, stale=stale)
    assert not s.sent
    for now, stale in [
        (130, True),
        (140, True),
        (150, False),
        (160, False),
        (170, True),
        (230, True),
        (240, False),
        (670, True),
        (730, True),
    ]:
        await s.scan(now, stale=stale)
    assert [(t, m.kind, m.channel) for t, m in s.sent] == [
        ("phone", "stale", "urgent"),
        ("phone", "stale", "normal"),
        ("phone", "stale", "urgent"),
    ]


async def test_feishu_hook_requires_success_and_only_queues_until_background_scan():
    s = Scanner([device(), device("off", big_spread=False)])
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))
    notifier.event_repo = SimpleNamespace(record=AsyncMock())
    notifier.mobile_push = s.service

    async def record(kind="big_spread"):
        await notifier._record("大价差提醒\n内容", kind=kind, symbol="OPENAI", level="normal")

    await record()
    await s.scan(0)
    notifier.event_repo.record.side_effect = RuntimeError()
    await record()
    notifier.event_repo.record.side_effect = None
    await record("spread")
    await s.scan(10)
    assert not s.sent
    await record()
    await record()
    assert not s.sent
    await s.scan(20)
    await record()
    await s.scan(30)
    await record()
    await s.scan(620)
    assert [(t, m.kind, m.card_id, m.channel) for t, m in s.sent] == [
        ("phone", "big_spread", "", "normal"),
        ("phone", "big_spread", "", "normal"),
    ]


async def test_big_spread_same_symbol_distinct_dex_and_fill_leg_identity():
    s = Scanner([device()])
    await s.scan(0)
    for dex, price in [("io", 10), ("xyz", 11), ("io", 12)]:
        body = f"大价差\nHL {dex}（{dex}:ANT） 买 {price} → Bitget 卖 13"
        s.service.queue_big_spread("大价差", body, "ANTUSDT")
    await s.scan(10, card(qty_a=1, holding=True, buy_ex="hl", dex_a="xyz", raw_symbol_a="xyz:ANT"))
    assert len(s.sent) == 3
    assert s.sent[0][1].body.startswith("hl xyz xyz:ANT 1 /")
    assert [m.body.splitlines()[1].split("（")[0] for _, m in s.sent[1:]] == ["HL io", "HL xyz"]


async def test_scan_failure_and_slow_round_are_isolated(caplog):
    stop_event = asyncio.Event()
    service = MobilePushService(None, None, None, Mock(side_effect=[10, 12, 20, 20]))

    async def scan():
        if service.scan.await_count == 1:
            raise ValueError("sensitive details")
        stop_event.set()

    service.scan = AsyncMock(side_effect=scan)
    # Wake the loop immediately for its second scan.
    stop_event.wait = AsyncMock(return_value=False)
    await service.run(stop_event)
    assert service.scan.await_count == 2
    assert "扫描失败" in caplog.text and "2.00 秒" in caplog.text and "sensitive" not in caplog.text


@pytest.fixture
async def push_api(tmp_path):
    settings = Settings(
        environment="production",
        dashboard_password="fixture",
        dashboard_totp_secret=TEST_TOTP_SECRET,
        database_url=f"sqlite:///{tmp_path / 'radar.db'}",
    )
    app = create_app(settings=settings, start_background_workers=False)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client,
    ):
        login = await client.post(
            "/api/auth/login",
            json={
                "password": "fixture",
                "scope": "mobile_readonly",
                "code": totp_at(TEST_TOTP_SECRET, int(time.time() // 30)),
            },
        )
        assert login.status_code == 200
        yield client, app


REGISTER = "/api/mobile/push/register"
TEST = "/api/mobile/push/test"
REGISTRATION = {"token": "phone", "threshold_pp": 0.1, "kinds": KINDS}


async def test_session_api_validation_upsert_delete_and_revocation(push_api):
    client, app = push_api
    service = app.state.mobile_push
    for path in ("/api/mobile/push/register/", "/api/mobile/push/other", "/api/mobile/summary"):
        assert (await client.post(path, json=REGISTRATION)).status_code == 403
    assert (await client.delete(TEST)).status_code == 403
    for changes in (
        {"token": ""},
        {"token": "secret" * 683},
        {"threshold_pp": 0.009},
        {"threshold_pp": 5.01},
        {"kinds": {"fill": True}},
        {"kinds": KINDS | {"fill": 2}},
    ):
        response = await client.post(REGISTER, json=REGISTRATION | changes)
        assert response.status_code == 422 and "secret" not in response.text
    for threshold in (0.01, 5):
        assert (
            await client.post(REGISTER, json=REGISTRATION | {"threshold_pp": threshold})
        ).status_code == 200
        (row,) = await service.repo.scan()
        assert row["threshold_pp"] == threshold
    assert (await client.post(TEST, json={"token": "phone"})).json() == {
        "sent": False,
        "reason": "手机推送未启用",
    }
    assert (await client.request("DELETE", REGISTER, json={"token": "phone"})).status_code == 200
    assert not await service.repo.scan()
    await client.post(REGISTER, json=REGISTRATION)
    assert (await client.post("/api/auth/logout")).status_code == 200
    await service.scan()
    assert await service.repo.get("phone", row["session_hash"]) is None
    assert (await client.post(REGISTER, json=REGISTRATION)).status_code == 401


async def test_test_send_cooldown_invalid_device_deletion_and_expiry(push_api):
    client, app = push_api
    service = app.state.mobile_push
    clock = [100]
    service.clock = lambda: clock[0]
    service.fcm.send = AsyncMock(
        side_effect=[
            SendResult(False, "推送服务暂不可用"),
            SendResult(True, "已发送"),
            SendResult(False, "设备令牌已失效", invalid=True),
        ]
    )
    await client.post(REGISTER, json=REGISTRATION)
    for now, sent, reason in [
        (100, False, "推送服务暂不可用"),
        (159, False, "每个设备每分钟最多测试一次"),
        (160, True, "已发送"),
        (220, False, "设备令牌已失效"),
    ]:
        clock[0] = now
        response = await client.post(TEST, json={"token": "phone"})
        assert response.json() == {"sent": sent, "reason": reason}
        if now in (100, 160):
            (row,) = await service.repo.scan()
            assert row["last_error"] == (None if sent else reason)
    assert service.fcm.send.await_count == 3 and not await service.repo.scan()
    await client.post(REGISTER, json=REGISTRATION)
    await service.repo.db.execute("UPDATE auth_sessions SET expires_at=0")
    await service.repo.db.commit()
    await service.scan()
    assert await service.repo.get("phone", row["session_hash"]) is None


async def test_registered_phone_uses_shared_summary_cache_and_observes_outage(push_api):
    client, app = push_api
    service = app.state.mobile_push
    await client.post(REGISTER, json=REGISTRATION)
    astro = app.state.astro_client
    astro._pairs_cache = [
        {
            "id": "id-A",
            "name": "OPENAI",
            "buyEx": "bitget",
            "sellEx": "rh-lighter",
            "aExPosition": 1,
            "bExPosition": 1,
            "status": True,
        }
    ]
    astro._pairs_cached_at = time.monotonic()
    astro.list_pairs = AsyncMock(side_effect=AssertionError("unexpected upstream refresh"))
    initial = await service.load_summary()
    assert (
        initial.model_dump(mode="json")["cards"]
        == (await client.get("/api/mobile/summary")).json()["cards"]
    )
    astro.list_pairs.assert_not_awaited()
    astro._pairs_cached_at -= 70
    astro.list_pairs.side_effect = AstroClientError("unavailable")
    cached = await service.load_summary()
    assert cached.stale and cached.cards[0].qty_a == 1
    astro._pairs_cache = None
    astro._pairs_cached_at = time.monotonic()
    missing = await service.load_summary()
    assert missing.stale and missing.cards == []
