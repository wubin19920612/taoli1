import asyncio
import time
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from auth_helpers import TEST_TOTP_SECRET

from app.core.config import Settings
from app.core.totp import totp_at
from app.main import create_app
from app.services import mobile_push
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
        "token_hash": sha256(token.encode()).hexdigest(),
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
        registered = {(d["token"], d["session_hash"]): d for d in devices}
        repo = SimpleNamespace(
            scan=AsyncMock(return_value=devices),
            get=AsyncMock(side_effect=lambda *key: registered.get(key)),
            record_result=AsyncMock(),
        )
        fcm = SimpleNamespace(enabled=True, send=self.send)
        self.service = MobilePushService(
            repo, fcm, AsyncMock(return_value=self.summary), lambda: self.now
        )

    async def send(self, token, message):
        self.sent.append((token, message))
        return SendResult(True, "已发送")

    async def scan(self, now, current=None, stale=False, *, drain=True):
        self.now = now
        self.summary.cards = [current or card()]
        self.summary.stale = stale
        await self.service.scan()
        if drain and not self.service._queue.empty():
            sender = asyncio.create_task(self.service._send_queued())
            try:
                await asyncio.wait_for(self.service._queue.join(), timeout=1)
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)


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


async def test_feishu_push_preferences_channels_and_category_title_cooldown():
    s = Scanner([device(price=True, funding=True), device("off", price=False), device("legacy")])
    await s.scan(0)
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))
    notifier.event_repo = SimpleNamespace(record=AsyncMock())
    notifier.mobile_push = s.service
    title = "[Phone price alert] BTC"
    body = title + "\n" + "价" * 800

    await notifier._record(body, kind=None, symbol=None, level="urgent")
    s.service.queue_feishu("price", title, "duplicate", urgent=False)
    s.service.queue_feishu("funding", title, "funding", urgent=False)
    s.service.queue_feishu("price", "[Phone price alert] ETH", "other", urgent=False)
    assert not s.sent
    await s.scan(10)

    assert [(token, msg.kind, msg.title, msg.channel) for token, msg in s.sent] == [
        ("phone", "price", title, "urgent"),
        ("phone", "funding", title, "normal"),
        ("phone", "price", "[Phone price alert] ETH", "normal"),
    ]
    assert s.sent[0][1].body == body[:720]
    s.service.queue_feishu("price", title, "duplicate", urgent=False)
    await s.scan(609)
    assert len(s.sent) == 3
    s.service.queue_feishu("price", title, "new alert", urgent=False)
    await s.scan(610)
    assert len(s.sent) == 4 and s.sent[-1][1].body == "new alert"


async def test_feishu_push_drops_messages_with_no_devices_at_scan_or_fcm_disabled():
    s = Scanner([device(announcement=True)])
    await s.scan(0)
    s.service.fcm.enabled = False
    s.service.queue_feishu("announcement", "[OKX] 上币公告", "disabled", urgent=False)
    s.service.fcm.enabled = True
    await s.scan(10)
    assert not s.sent

    s.service.queue_feishu("announcement", "[OKX] 上币公告", "no recipients at scan", urgent=False)
    devices = s.service.repo.scan.return_value
    s.service.repo.scan.return_value = []
    await s.scan(20)
    s.service.repo.scan.return_value = devices
    await s.scan(30)
    assert not s.sent


@pytest.mark.parametrize("scanned_without_devices", [False, True])
async def test_feishu_push_uses_devices_and_preferences_at_next_scan(scanned_without_devices):
    phone = device(announcement=False)
    s = Scanner([phone])
    s.service.repo.scan.return_value = []
    if scanned_without_devices:
        await s.scan(0)

    s.service.queue_feishu("announcement", "[OKX] 上币公告", "new listing", urgent=False)
    phone["kinds"]["announcement"] = True
    s.service.repo.scan.return_value = [phone]
    await s.scan(10)

    assert [(token, msg.body) for token, msg in s.sent] == [("phone", "new listing")]


async def test_feishu_scan_limits_device_deliveries_and_resumes_partial_message():
    s = Scanner([device(f"phone-{i}", general=True) for i in range(3)])
    await s.scan(0)
    messages = [f"提醒 {i}" for i in range(18)]
    for title in messages:
        s.service.queue_feishu("general", title, "内容", urgent=False)

    await s.scan(10)
    assert len(s.sent) == 50
    assert len(s.service._feishu_messages) == 2

    await s.scan(620)
    actual = [(token, msg.title) for token, msg in s.sent]
    expected = [(f"phone-{i}", title) for title in messages for i in range(3)]
    assert sorted(actual) == sorted(expected)
    assert not s.service._feishu_messages


async def test_full_feishu_push_queue_drops_oldest_message(caplog):
    s = Scanner([device(general=True)])
    await s.scan(0)
    s.service.queue_feishu("general", "加急提醒", "内容", urgent=True)
    for i in range(mobile_push.QUEUE_LIMIT):
        s.service.queue_feishu("general", f"提醒 {i}", "内容", urgent=False)
    assert len(s.service._feishu_messages) == mobile_push.QUEUE_LIMIT
    for now in range(10, 50, 10):
        await s.scan(now)
    assert [msg.title for _, msg in s.sent] == ["加急提醒"] + [
        f"提醒 {i}" for i in range(1, mobile_push.QUEUE_LIMIT)
    ]
    assert "丢弃最旧的飞书提醒" in caplog.text


@pytest.mark.parametrize("replaceable", [False, True])
async def test_full_send_queue_feishu_preserves_card_spread_stale_and_urgent_pushes(
    monkeypatch, replaceable,
):
    monkeypatch.setattr(mobile_push, "QUEUE_LIMIT", 7 if replaceable else 5)
    s = Scanner([device(general=True)])
    await s.scan(0, stale=True)
    s.service.queue_big_spread("【大价差】BTC", "价差内容", "BTCUSDT")
    current = card(qty_a=1, holding=True, leg_imbalance=True)
    await s.scan(60, current, stale=True, drain=False)

    s.service.queue_feishu("general", "飞书加急", "内容", urgent=True)
    if replaceable:
        s.service.queue_feishu("general", "最旧普通飞书", "内容", urgent=False)
        s.service.queue_feishu("general", "较新普通飞书", "内容", urgent=False)
    await s.scan(70, current, stale=True, drain=False)
    assert s.service._queue.full()

    s.service.queue_feishu("general", "新飞书", "内容", urgent=False)
    await s.scan(80, current, stale=True)

    assert [msg.kind for _, msg in s.sent[:4]] == ["fill", "leg_alert", "big_spread", "stale"]
    expected_titles = ["飞书加急", "较新普通飞书", "新飞书"] if replaceable else ["飞书加急"]
    assert [msg.title for _, msg in s.sent[4:]] == expected_titles
    assert s.sent[4][1].channel == "urgent"


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
    assert len({m.tag for _, m in s.sent[1:]}) == 2


@pytest.mark.parametrize("urgent_first,scan_between", [(False, False), (True, False), (False, True)])
async def test_feishu_normal_and_phone_records_share_urgent_push_and_cooldown(
    urgent_first, scan_between
):
    s = Scanner([device()])
    await s.scan(0)
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))
    notifier.event_repo = SimpleNamespace(record=AsyncMock())
    notifier.mobile_push = s.service

    async def record(symbol, urgent, price):
        title = f"【大价差】{symbol}  开 3.42% / 平 2.00%"
        route = f"Gate 买 {price} → Bitget 卖 14"
        card_line = "卡片：未创建（自动建卡未启用）"
        lines = [title, route]
        if not urgent:
            lines += ["资金费：买 - · 卖 -", "24h 成交额：1 / 2 U"]
        await notifier._record(
            "\n".join([*lines, card_line]),
            kind=None if urgent else "big_spread",
            symbol=None if urgent else f"{symbol}-usdt",
            level="urgent" if urgent else "normal",
        )

    await record("OPENAI", urgent_first, 10)
    if scan_between:
        await s.scan(5, drain=False)
    await record("OPENAI", not urgent_first, 11)
    await record("ANTH", False, 10)
    await s.scan(10)
    queued_at = 5 if scan_between else 10
    await record("OPENAI", True, 12)
    await s.scan(queued_at + 599)
    assert [(m.kind, m.channel) for _, m in s.sent] == [
        ("big_spread", "urgent"), ("big_spread", "normal")
    ]
    tags = [m.payload("phone")["message"]["android"]["notification"]["tag"] for _, m in s.sent]
    assert tags == ["big_spread:OPENAI:Gate→Bitget", "big_spread:ANTH:Gate→Bitget"]
    await record("OPENAI", False, 13)
    await s.scan(queued_at + 600)
    assert [m.tag for _, m in s.sent].count(tags[0]) == 2


async def test_background_scans_continue_during_slow_fcm_with_four_senders(monkeypatch):
    monkeypatch.setattr(mobile_push, "SCAN_INTERVAL", 0.05)
    s = Scanner([device(f"phone-{i}") for i in range(8)])
    service = s.service
    service.clock = time.monotonic
    stop = asyncio.Event()
    scanned = asyncio.Event()
    four_in_flight = asyncio.Event()
    starts = []
    active = 0
    peak = 0

    async def load_summary():
        starts.append(time.monotonic())
        if len(starts) >= 4:
            scanned.set()
        return SimpleNamespace(
            stale=False, cards=[card(qty_a=int(len(starts) >= 2), holding=len(starts) >= 2)]
        )

    async def send(token, message):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 4:
            four_in_flight.set()
        try:
            await asyncio.sleep(5)
            return SendResult(True, "已发送")
        finally:
            active -= 1

    service.load_summary = load_summary
    service.fcm.send = send
    runner = asyncio.create_task(service.run(stop))
    try:
        await asyncio.wait_for(four_in_flight.wait(), timeout=1)
        await asyncio.wait_for(scanned.wait(), timeout=1)
        assert active == peak == 4
        assert starts[3] - starts[0] < 1
        assert service._queue.qsize() == 4
    finally:
        stop.set()
        await asyncio.wait_for(runner, timeout=1)
    assert active == 0


async def test_full_push_queue_drops_oldest_messages(caplog):
    s = Scanner([device(f"phone-{i}") for i in range(205)])
    await s.scan(0)
    await s.scan(10, card(qty_a=1, holding=True), drain=False)
    assert s.service._queue.qsize() == 200 and "丢弃最旧的待发送通知" in caplog.text
    await s.scan(20, card(qty_a=1, holding=True))
    assert [token for token, _ in s.sent] == [f"phone-{i}" for i in range(5, 205)]


@pytest.mark.parametrize("delivered", [False, True])
async def test_stale_recovery_can_be_detected_before_alarm_send_finishes(delivered):
    s = Scanner([device()])
    service = s.service
    started, release = asyncio.Event(), asyncio.Event()

    async def send(token, message):
        if message.channel == "urgent":
            started.set()
            await release.wait()
            if not delivered:
                return SendResult(False, "推送服务暂不可用")
        return await s.send(token, message)

    service.fcm.send = send
    await s.scan(0, stale=True)
    await s.scan(60, stale=True, drain=False)
    sender = asyncio.create_task(service._send_queued())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await s.scan(70, drain=False)
        release.set()
        await asyncio.wait_for(service._queue.join(), timeout=1)
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
    assert [msg.channel for _, msg in s.sent] == (["urgent", "normal"] if delivered else [])


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
        {"kinds": KINDS | {"price": "true"}},
        {"kinds": KINDS | {"unknown": False}},
    ):
        response = await client.post(REGISTER, json=REGISTRATION | changes)
        assert response.status_code == 422 and "secret" not in response.text
    for threshold in (0.01, 5):
        assert (
            await client.post(REGISTER, json=REGISTRATION | {"threshold_pp": threshold})
        ).status_code == 200
        (row,) = await service.repo.scan()
        assert row["threshold_pp"] == threshold
        assert row["kinds"] == KINDS | dict.fromkeys(
            ("announcement", "price", "funding", "auth", "trade_status", "index_component",
             "premium", "spread", "general"),
            False,
        )
    assert (await client.post(TEST, json={"token": "phone"})).json() == {
        "sent": False,
        "reason": "手机推送未启用",
    }
    assert (await client.request("DELETE", REGISTER, json={"token": "phone"})).status_code == 200
    assert not await service.repo.scan()
    registration = REGISTRATION | {"kinds": KINDS | {"price": True}}
    assert (await client.post(REGISTER, json=registration)).status_code == 200
    (row,) = await service.repo.scan()
    assert row["kinds"]["price"] is True and row["kinds"]["announcement"] is False
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
