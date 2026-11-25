import asyncio
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from auth_helpers import TEST_TOTP_SECRET

from app.core.config import Settings
from app.core.totp import totp_at
from app.db.auth import AuthStore, session_hash
from app.db.database import connect_database
from app.db.repositories import MobileAlertRepository
from app.db.schema import initialize_schema
from app.main import create_app
from app.services.feishu import FeishuConfig, FeishuNotifier


@pytest.fixture
async def mobile_app(tmp_path):
    settings = Settings(
        environment="production",
        dashboard_password="fixture",
        dashboard_totp_secret=TEST_TOTP_SECRET,
        dashboard_api_token="a" * 64,
        database_url=f"sqlite:///{tmp_path / 'radar.db'}",
    )
    app = create_app(settings=settings, start_background_workers=False)
    async with app.router.lifespan_context(app):
        app.state.astro_client = SimpleNamespace(
            list_pairs_cached=AsyncMock(return_value=[]),
            _pairs_cached_at=time.monotonic(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            yield app, client


async def test_scope_whitelist_full_session_token_and_replay(mobile_app):
    _app, client = mobile_app
    counter = int(time.time() // 30)
    payload = {
        "password": "fixture",
        "code": totp_at(TEST_TOTP_SECRET, counter),
        "scope": "mobile_readonly",
    }
    assert (await client.post("/api/auth/login", json=payload)).status_code == 200
    phone = client.cookies.get("taoli1_session")
    for path in ("mobile/summary", "mobile/alerts", "health", "health/live"):
        assert (await client.get("/api/" + path)).status_code == 200
    for method, path in (
        ("GET", "opportunities"),
        ("GET", "auth/sessions"),
        ("GET", "features"),
        ("POST", "auth/login"),
        ("POST", "auth/sessions/revoke-all"),
        ("GET", "stream"),
    ):
        response = await client.request(method, "/api/" + path)
        assert response.status_code == 403
        assert response.json() == {"detail": "手机只读会话无权访问"}
    client.cookies.clear()
    assert (
        await client.post("/api/auth/login", json={**payload, "scope": "full"})
    ).status_code == 401
    full = {"password": "fixture", "code": totp_at(TEST_TOTP_SECRET, counter + 1)}
    assert (await client.post("/api/auth/login", json=full)).status_code == 200
    sessions = (await client.get("/api/auth/sessions")).json()
    assert {row["scope"]: row["current"] for row in sessions} == {
        "full": True,
        "mobile_readonly": False,
    }
    assert (await client.get("/api/mobile/summary")).status_code == 200
    client.cookies.clear()
    assert (
        await client.get("/api/mobile/summary", headers={"X-Api-Token": "a" * 64})
    ).status_code == 200
    client.cookies.set("taoli1_session", phone)
    assert (await client.post("/api/auth/logout")).status_code == 200
    assert (await client.get("/api/mobile/summary")).status_code == 401


async def test_login_scopes_share_rate_limit(mobile_app):
    _, client = mobile_app
    for index in range(30):
        response = await client.post(
            "/api/auth/login",
            json={
                "scope": ("mobile_readonly", "full")[index % 2],
                "password": "wrong",
                "code": "000000",
            },
        )
        assert response.status_code == 401
    assert (
        await client.post("/api/auth/login", json={"scope": "mobile_readonly"})
    ).status_code == 429


async def test_old_db_preserves_rule_events_and_sessions(tmp_path):
    path = str(tmp_path / "legacy.db")
    token = "b" * 64
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE alert_events (id TEXT PRIMARY KEY, rule_id TEXT NOT NULL,
                                   opportunity_id TEXT NOT NULL, symbol TEXT NOT NULL,
                                   status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO alert_events VALUES ('id', 'rule', 'op', 'BTC', 'sent', 'old message', '2026-01-01');
        CREATE TABLE auth_sessions (id_hash TEXT PRIMARY KEY, created_at REAL, expires_at REAL,
                                    last_seen_at REAL, ip TEXT, user_agent TEXT, revoked_at REAL);
    """)
    connection.execute(
        "INSERT INTO auth_sessions VALUES (?,1,100,1,'ip','browser',NULL)", (session_hash(token),)
    )
    connection.commit()
    columns = connection.execute("PRAGMA table_info(alert_events)").fetchall()
    schema_query = "SELECT type, name, sql FROM sqlite_master WHERE tbl_name='alert_events' ORDER BY name"
    definition = connection.execute(schema_query).fetchall()
    connection.close()
    db = await connect_database(path)
    try:
        for _ in range(2):
            await initialize_schema(db)
        async with db.execute("PRAGMA table_info(alert_events)") as cursor:
            assert [tuple(row) for row in await cursor.fetchall()] == columns
        async with db.execute(schema_query) as cursor:
            assert [tuple(row) for row in await cursor.fetchall()] == definition
        async with db.execute("SELECT message FROM alert_events") as cursor:
            assert (await cursor.fetchone())[0] == "old message"
        assert await MobileAlertRepository(db).list(0, 200) == []
        store = await AuthStore.open(path)
        try:
            assert await store.authenticate(token, 50) == (session_hash(token), "full")
        finally:
            await store.close()
    finally:
        await db.close()


@pytest.mark.parametrize("prefix, identifier", [("", "ANTH-ANTHROPIC"), ("by-id/", "Card-A")])
async def test_history_uses_exact_route_and_labels_candle_estimates(mobile_app, prefix, identifier):
    app, client = mobile_app
    app.state.astro_client.list_pairs_cached.return_value = [
        {
            "id": "Card-A",
            "name": "ANTH-ANTHROPIC",
            "type": "FR",
            "buyEx": "hl",
            "aEffectiveHlDex": "io",
            "sellEx": "gc-okx",
            "regressionValue": 10,
            "openPosition": 10,
            "closePosition": 10,
        },
    ]
    point = SimpleNamespace(bucket_at=datetime.now(UTC), leg1_close=100, leg2_close=11)
    service = SimpleNamespace(
        query=AsyncMock(return_value=SimpleNamespace(points=[point])), aclose=AsyncMock()
    )
    app.state.pair_spread_query_service_factory = lambda: service
    client.headers["X-Api-Token"] = "a" * 64
    path = f"/api/mobile/cards/{prefix}{identifier}/history"
    result = await client.get(path)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["price_source"] == "candle_close_1m"
    assert (
        data["points"][0]["open_spread"]
        == data["points"][0]["close_spread"]
        == pytest.approx(1000 / 105)
    )
    assert data["open_condition"] == data["close_condition"] == 0
    a, b = service.query.await_args.args
    assert (a.symbol, a.dex, b.exchange) == ("ANTHUSDT", "io", "okx")
    assert service.query.await_args.kwargs["hours"] == 24
    service.aclose.assert_awaited_once()
    for hours, status in ((0, 422), (72, 200), (73, 422)):
        assert (await client.get(path, params={"hours": hours})).status_code == status
    assert (await client.get(f"/api/mobile/cards/{prefix}missing/history")).status_code == 404


async def test_mobile_readonly_history_by_id_resolves_duplicate_names(mobile_app):
    app, client = mobile_app
    pair = {"name": "BTC", "buyEx": "gate", "sellEx": "binance", "openPosition": ".01"}
    app.state.astro_client.list_pairs_cached.return_value = [
        dict(pair, id="Card-A"), dict(pair, id="Card-B", sellEx="bitget", openPosition=".02")
    ]
    service = SimpleNamespace(
        query=AsyncMock(return_value=SimpleNamespace(points=[])), aclose=AsyncMock()
    )
    app.state.pair_spread_query_service_factory = lambda: service
    login = await client.post("/api/auth/login", json={
        "password": "fixture",
        "code": totp_at(TEST_TOTP_SECRET, int(time.time() // 30)),
        "scope": "mobile_readonly",
    })
    assert login.status_code == 200

    cards = (await client.get("/api/mobile/summary")).json()["cards"]
    assert [card["id"] for card in cards] == ["Card-A", "Card-B"]
    assert (await client.get("/api/mobile/cards/BTC/history")).status_code == 409
    assert (await client.get("/api/mobile/cards/by-id/card-b/history")).status_code == 404
    service.query.assert_not_awaited()

    result = await client.get("/api/mobile/cards/by-id/Card-B/history", params={"hours": 12})
    assert result.status_code == 200 and result.json()["open_condition"] == 2
    assert service.query.await_args.args[1].exchange == "bitget"
    assert service.query.await_args.kwargs["hours"] == 12


async def test_alert_journal_order_redaction_and_retention(mobile_app):
    app, client = mobile_app
    repo = app.state.mobile_alert_repo
    notifier = app.state.feishu_notifier
    notifier.config = FeishuConfig(
        webhook_url="https://test.invalid/hook", app_secret="fixture-hidden", app_id="id",
        alert_chat_id="chat", phone_user_ids=["id"], phone_enabled=True,
    )
    notifier._tenant_access_token = AsyncMock(return_value="token")
    notifier._create_open_platform_text_message = AsyncMock(return_value="message")
    notifier._send_phone_urgent = AsyncMock()
    sent = []

    async def post(url, *, json):
        assert len(await repo.list(0, 200)) == len(sent)
        sent.append(json["content"]["text"])
        return httpx.Response(200, json={"code": 0}, request=httpx.Request("POST", url))

    notifier._client = SimpleNamespace(post=post, aclose=AsyncMock())
    body = (
        "大价差提醒\nfixture-hidden token=sensitive Authorization: Basic private-encoded\n"
        + "测" * 2100
    )
    await notifier.send_text(body)
    await asyncio.gather(*notifier._record_tasks)
    await notifier.send_alert(None, SimpleNamespace(symbol="ETH"), prebuilt_text="合约价差")
    await asyncio.gather(*notifier._record_tasks)
    await notifier.send_phone_urgent_text("电话")
    await asyncio.gather(*notifier._record_tasks)
    assert sent == [body, "合约价差"]
    headers = {"X-Api-Token": "a" * 64}
    events = (await client.get("/api/mobile/alerts", headers=headers)).json()
    urgent, spread, normal = events
    assert urgent["level"] == "urgent" and urgent["kind"] == "general"
    assert spread["symbol"] == "ETH" and spread["kind"] == "spread"
    assert normal["level"] == "normal" and normal["kind"] == "big_spread"
    assert len(normal["body"]) == 2000 and normal["title"] == "大价差提醒"
    assert all(
        secret not in normal["body"]
        for secret in ("fixture-hidden", "sensitive", "private-encoded")
    )
    assert (await client.get("/api/mobile/alerts?limit=1", headers=headers)).json() == [urgent]
    assert (await client.get("/api/mobile/alerts?limit=201", headers=headers)).status_code == 422
    assert (
        await client.get(f"/api/mobile/alerts?since={urgent['created_at']}", headers=headers)
    ).json() == []

    now = datetime.now(UTC)
    for event, days in ((normal, 31), (spread, 30)):
        await repo.db.execute(
            "UPDATE mobile_alert_events SET created_at=? WHERE id=?",
            ((now - timedelta(days=days)).timestamp(), event["id"]),
        )
    await repo.db.commit()
    await repo.prune(now)
    assert [row["id"] for row in await repo.list(0, 200)] == [urgent["id"], spread["id"]]


async def test_event_failure_preserves_all_feishu_sends(caplog):
    response = httpx.Response(200, json={"code": 0}, request=httpx.Request("POST", "https://test"))
    transport = SimpleNamespace(post=AsyncMock(return_value=response))
    notifier = FeishuNotifier(
        FeishuConfig(
            "https://test",
            app_id="id",
            app_secret="secret",
            alert_chat_id="chat",
            phone_user_ids=["id"],
            phone_enabled=True,
        ),
        transport,
    )
    notifier.event_repo = SimpleNamespace(
        record=AsyncMock(side_effect=RuntimeError("private detail"))
    )
    notifier._tenant_access_token = AsyncMock(return_value="fixture-token")
    notifier._create_open_platform_text_message = AsyncMock(return_value="message")
    notifier._send_phone_urgent = AsyncMock()
    await notifier.send_text("普通")
    await notifier.send_alert(None, SimpleNamespace(symbol="ETH"), prebuilt_text="价差")
    await notifier.send_phone_urgent_text("加急")
    await notifier.aclose()
    assert notifier.event_repo.record.await_count == 3 and transport.post.await_count == 2
    notifier._send_phone_urgent.assert_awaited_once()
    assert caplog.text.count("Failed to record mobile alert event") == 3
    assert "private detail" not in caplog.text


async def test_slow_journal_never_blocks_feishu_and_times_out(caplog):
    sent = []
    cancelled = []

    async def record(*, body, **event):
        assert body in sent
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(body)
            raise

    async def post(url, *, json):
        sent.append(json["content"]["text"])
        return httpx.Response(200, json={"code": 0}, request=httpx.Request("POST", url))

    notifier = FeishuNotifier(FeishuConfig(
        "https://test", app_id="id", app_secret="secret", alert_chat_id="chat",
        phone_user_ids=["id"], phone_enabled=True,
    ), SimpleNamespace(post=post))
    notifier.event_repo = SimpleNamespace(record=record)
    notifier._tenant_access_token = AsyncMock(return_value="token")
    notifier._create_open_platform_text_message = AsyncMock(return_value="message")
    notifier._send_phone_urgent = AsyncMock(side_effect=lambda *args: sent.append("电话"))
    for send in (
        notifier.send_text("普通"),
        notifier.send_alert(None, SimpleNamespace(symbol="BTC"), prebuilt_text="价差"),
        notifier.send_phone_urgent_text("电话"),
    ):
        started = time.perf_counter()
        await send
        assert time.perf_counter() - started < 0.2
    await asyncio.wait_for(notifier.aclose(), timeout=2)
    assert sorted(cancelled) == sorted(sent) == sorted(["普通", "价差", "电话"])
    assert not notifier._record_tasks
    assert caplog.text.count("Failed to record mobile alert event") == 3


async def test_unconfigured_webhook_rejected_before_build_or_record():
    notifier = FeishuNotifier(FeishuConfig(""))
    notifier.event_repo = SimpleNamespace(record=AsyncMock())
    with pytest.raises(RuntimeError, match="Feishu webhook is not configured"):
        await notifier.send_alert(None, None)
    await notifier.send_phone_urgent_text("未启用")
    await notifier.aclose()
    notifier.event_repo.record.assert_not_awaited()


async def test_locked_journal_does_not_commit_or_block_rule_delivery(mobile_app, caplog):
    app, _ = mobile_app
    db = app.state.db
    notifier = app.state.feishu_notifier
    notifier.config = FeishuConfig("https://test")
    response = httpx.Response(200, json={"code": 0}, request=httpx.Request("POST", "https://test"))
    notifier._client = SimpleNamespace(post=AsyncMock(return_value=response), aclose=AsyncMock())
    await db.execute("BEGIN IMMEDIATE")
    try:
        await asyncio.wait_for(notifier.send_text("locked"), timeout=0.2)
        await asyncio.wait_for(asyncio.gather(*notifier._record_tasks), timeout=2)
        assert db.in_transaction
        assert "Failed to record mobile alert event" in caplog.text
        # A cancelled await can leave SQLite finishing its busy handler. Queue
        # this read behind the INSERT before releasing the simulated writer.
        assert await app.state.mobile_alert_repo.list(0, 200) == []
    finally:
        await db.rollback()
    await notifier.send_text("unlocked")
    await asyncio.gather(*notifier._record_tasks)
    assert [row["body"] for row in await app.state.mobile_alert_repo.list(0, 200)] == ["unlocked"]
    assert notifier._client.post.await_count == 2


@pytest.mark.parametrize("credential", [
    "passphrase: 'first second'", "access_key=first", "access-key=first", "accessKey=first",
    '"OK-ACCESS-KEY": "first"', "OK-ACCESS-SIGN: first", "OK-ACCESS-TIMESTAMP: first",
    "OK-ACCESS-PASSPHRASE: first", "X-MBX-APIKEY: first", "sign=first", "signature=first",
    "private_key=first", "private-key=first", "privateKey=first",
    "Cookie: key=first; other=second", "Set-Cookie: session=first; Path=/second",
    "private material 0x" + "ab" * 32,
])
async def test_credentials_hidden_in_persisted_title_body_and_symbol(credential):
    response = httpx.Response(200, json={"code": 0}, request=httpx.Request("POST", "https://test"))
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    notifier = FeishuNotifier(FeishuConfig("https://test"), client)
    notifier.event_repo = SimpleNamespace(record=AsyncMock())
    await notifier.send_alert(None, SimpleNamespace(symbol=credential), prebuilt_text=credential)
    await notifier.aclose()
    assert client.post.await_args.kwargs["json"]["content"]["text"] == credential
    event = notifier.event_repo.record.await_args.kwargs
    for field in ("title", "body", "symbol"):
        assert "[已隐藏]" in event[field]
        assert all(value not in event[field] for value in ("first", "second", "ab" * 32))


async def test_history_selects_type_for_same_name(mobile_app):
    app, client = mobile_app
    pair = {"name": "BTC", "buyEx": "gate", "sellEx": "binance", "openPosition": ".01"}
    app.state.astro_client.list_pairs_cached.return_value = [
        dict(pair, type="FF"), dict(pair, type="SF", openPosition=".02")
    ]
    service = SimpleNamespace(query=AsyncMock(return_value=SimpleNamespace(points=[])), aclose=AsyncMock())
    app.state.pair_spread_query_service_factory = lambda: service
    client.headers["X-Api-Token"] = "a" * 64
    path = "/api/mobile/cards/BTC/history"
    ambiguous = await client.get(path)
    assert ambiguous.status_code == 409 and ambiguous.json()["detail"]["types"] == ["FF", "SF"]
    assert (await client.get(path, params={"type": "FR"})).status_code == 404
    service.query.assert_not_awaited()
    for kind, market_type, threshold in (("FF", "future", 1), ("SF", "spot", 2)):
        result = await client.get(path, params={"type": kind})
        assert result.status_code == 200 and result.json()["open_condition"] == threshold
        assert service.query.await_args.args[0].market_type == market_type


async def test_malformed_cards_leave_summary_available(mobile_app):
    app, client = mobile_app
    pair = {"name": "BTC", "type": "FF", "buyEx": "gate", "sellEx": "binance", "aExPosition": 2}
    app.state.astro_client.list_pairs_cached.return_value = [
        pair, dict(pair, type=123), dict(pair, name=None),
        dict(pair, openPosition="oops"), dict(pair, buyEx=123), dict(pair, bHlDex=["io"]),
    ]
    response = await client.get("/api/mobile/summary", headers={"X-Api-Token": "a" * 64})
    assert response.status_code == 200
    cards = response.json()["cards"]
    assert len(cards) == 6 and all(card["qty_a"] == 2 and card["holding"] for card in cards)
    assert cards[0]["metric_error"] == "未找到 Gate 永续实时行情"
    assert all(card["metric_error"] == "卡片数据格式异常" for card in cards[1:])
    assert cards[2]["name"] == "" and cards[3]["open_condition"] is None
