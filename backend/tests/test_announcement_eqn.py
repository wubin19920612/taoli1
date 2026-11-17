import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes_announcements import router
from app.core.config import get_settings
from app.db.database import connect_database
from app.db.repositories import AnnouncementRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.announcement import AnnouncementSettings, ExchangeAnnouncement
from app.services import announcement_eqn, announcement_polling
from app.services.announcement_eqn import EqnAnnouncementProvider, EqnNewsClient
from app.services.announcements import (
    AnnouncementMonitor,
    build_announcement_alert_message,
    default_announcement_provider,
    load_announcement_settings,
)

FIXTURES = Path(__file__).parent / "fixtures" / "eqn"
NOW = datetime(2026, 11, 17, 3, tzinfo=UTC)
NEW_EXCHANGES = {"upbit", "bithumb", "mexc", "aster"}


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def news_result(items):
    return {"result": {"content": [{"type": "text", "text": json.dumps({"items": items})}]}}


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    async def sleep(self, seconds):
        self.value += seconds


def news_client(client, clock=None):
    clock = clock or Clock()
    return EqnNewsClient(client=client, monotonic_fn=clock, sleep_fn=clock.sleep,
                         now_fn=lambda: NOW)


@pytest.mark.parametrize("sse", [False, True])
async def test_fixtures_json_sse_session_channels_and_throttle(sse):
    calls = []
    clock = Clock()

    def respond(request):
        body = json.loads(request.content)
        calls.append((body, dict(request.headers), clock()))
        if body["method"] == "initialize":
            payload = fixture("initialize")
        else:
            assert request.headers["Mcp-Session-Id"] == "test-session"
            assert body["params"]["name"] == "list_news"
            assert body["params"]["arguments"]["limit"] == 20
            payload = fixture("list_news_" + body["params"]["arguments"]["channel"])
        payload["id"] = body["id"]
        headers = {"Mcp-Session-Id": "test-session"}
        if sse:
            headers["Content-Type"] = "text/event-stream"
            return httpx.Response(200, text=": keepalive\r\nevent: message\r\ndata: "
                                  + json.dumps(payload) + "\r\n\r\n", headers=headers)
        return httpx.Response(200, json=payload, headers=headers)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = EqnAnnouncementProvider(client=news_client(client, clock), now_fn=lambda: NOW)
        rows = await provider.fetch_headlines()
        await provider.aclose()
        assert not client.is_closed  # Caller owns injected HTTP clients.
    assert rows
    assert {row.exchange for row in rows} >= NEW_EXCHANGES
    assert all(row.source == "eqn" for row in rows)
    assert len({(row.exchange, row.announcement_id) for row in rows}) == len(rows)
    assert [call[0]["params"]["arguments"]["channel"] for call in calls[1:]] == [
        "binance", "korea", "alpha", "tier2", "tier3",
    ]
    assert all(b[2] - a[2] >= 0.2 for a, b in pairwise(calls))
    assert set(provider.last_source_articles) == {"binance", "korea", "alpha", "tier2", "tier3"}
    assert provider.last_source_errors == {}


@pytest.mark.parametrize("failure", ["http", "rpc"])
@pytest.mark.parametrize("permanent", [False, True])
async def test_session_reinitializes_only_once(failure, permanent):
    calls = []
    attempts = 0

    def respond(request):
        nonlocal attempts
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == "initialize":
            assert "Mcp-Session-Id" not in request.headers
            return httpx.Response(200, json=fixture("initialize"),
                                  headers={"Mcp-Session-Id": str(len(calls))})
        attempts += 1
        if permanent or attempts == 1:
            if failure == "http":
                return httpx.Response(404)
            return httpx.Response(200, json={"error": {"code": -32001, "message": "expired"}})
        assert request.headers["Mcp-Session-Id"] == "3"
        return httpx.Response(200, json=news_result([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        eqn = news_client(client)
        if permanent:
            with pytest.raises((ValueError, httpx.HTTPStatusError)):
                await eqn.list_news("binance")
        else:
            assert await eqn.list_news("binance") == []
    assert calls == ["initialize", "tools/call", "initialize", "tools/call"]


@pytest.mark.parametrize("retry_after", ["30", "Tue, 17 Nov 2026 03:00:30 GMT"])
async def test_retry_after_blocks_all_requests_until_deadline(retry_after):
    calls = []
    clock = Clock()

    def respond(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == "initialize":
            return httpx.Response(200, json=fixture("initialize"))
        if len(calls) == 2:
            return httpx.Response(429, headers={"Retry-After": retry_after})
        return httpx.Response(200, json=news_result([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        eqn = news_client(client, clock)
        with pytest.raises(httpx.HTTPStatusError):
            await eqn.list_news("binance")
        for channel in ("korea", "alpha", "tier2"):
            with pytest.raises(ValueError, match="backoff"):
                await eqn.list_news(channel)
        assert len(calls) == 2
        clock.value += 30
        assert await eqn.list_news("binance") == []
        assert len(calls) == 3


async def test_tool_is_error_is_not_empty_success_or_session_retry():
    calls = []

    def respond(request):
        method = json.loads(request.content)["method"]
        calls.append(method)
        return httpx.Response(200, json=fixture("initialize") if method == "initialize" else {
            "result": {"isError": True, "content": [{"type": "text", "text": "failed"}]},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match="list_news"):
            await news_client(client).list_news("binance")
    assert calls == ["initialize", "tools/call"]


@pytest.mark.parametrize("source,exchange", [
    ("Binance EN", "binance"), ("Binance Alpha", "binance"),
    ("Bitget Announcements", "bitget"), ("Bybit Announcements", "bybit"),
    ("OKX Announcements", "okx"), ("Gate.io Announcements", "gate"),
    ("Hyperliquid Perp Listing", "hyperliquid"), ("Mexc Announcements", "mexc"),
    ("Upbit", "upbit"), ("UPBIT LISTING", "upbit"),
    ("Bithumb", "bithumb"), ("Bithumb Listing", "bithumb"),
    ("Aster Perp Listing", "aster"), ("Aster Spot Listing", "aster"),
])
async def test_source_mapping_and_fields(source, exchange):
    item = json.loads(fixture("list_news_korea")["result"]["content"][0]["text"])["items"][0]
    item.update(source=source.swapcase(), coins=[" btc ", "BTC", "eth"],
                en="Will list Bitcoin (BTC)", zh="上线比特币", no=123)
    client = AsyncMock()
    client.list_news.return_value = [item]
    rows = await EqnAnnouncementProvider(client=client, now_fn=lambda: NOW).fetch()
    assert len(rows) == 1
    row = rows[0]
    assert (row.exchange, row.source, row.announcement_id) == (exchange, "eqn", "eqn-123")
    assert (row.title, row.title_zh) == (item["en"], item["zh"])
    assert row.url == item["source_url"]
    assert row.symbols == ["BTC", "ETH"]
    assert row.kind == "listing"
    assert row.category == "binance"
    assert row.published_at == datetime.fromisoformat(item["time"])
    assert row.fetched_at == NOW


@pytest.mark.parametrize("source_url", ["", "https://api.eqn.news/a", "https://EQN.NEWS/a"])
async def test_field_fallbacks_unknown_sources_and_debug_counts(source_url, caplog):
    item = {"no": 1, "source": "Binance EN", "en": "", "zh": "上线 Bitcoin (BTC)",
            "coins": [], "time": NOW.isoformat(), "source_url": source_url,
            "url": "https://eqn.news/monitor/1"}
    client = AsyncMock()
    client.list_news.return_value = [item] + [dict(item, no=i + 2, source=source) for i, source in
        enumerate(["Binance Wallet Pre-TGE", "Price Monitor", "Twitter", "News"])]
    with caplog.at_level(logging.DEBUG):
        rows = await EqnAnnouncementProvider(client=client).fetch()
    assert len(rows) == 1
    assert rows[0].title == item["zh"]
    assert rows[0].title_zh == item["zh"]
    assert rows[0].url == item["url"]
    assert rows[0].symbols == ["BTC"]
    assert "Price Monitor" in caplog.text
    assert "count=5" in caplog.text


@pytest.fixture
async def repo():
    db = await connect_database(":memory:")
    await initialize_schema(db)
    try:
        yield AnnouncementRepository(db)
    finally:
        await db.close()


def notice(source="official", **updates):
    row = ExchangeAnnouncement(
        exchange="binance", source=source, announcement_id=source + "-1",
        title="Will list Bitcoin (BTC)", title_zh="上线比特币", kind="listing",
        url="https://exchange.example/notice/1", published_at=NOW, fetched_at=NOW,
        event_time=NOW + timedelta(minutes=10), event_reminder_status="pending",
    )
    return row.model_copy(update=updates)


@pytest.mark.parametrize("first_source,second_source", [
    ("official", "eqn"), ("eqn", "official"), ("website", "api"),
])
@pytest.mark.parametrize("match", ["title", "title_zh", "url"])
async def test_cross_source_duplicate_has_no_side_effects(repo, first_source, second_source, match, caplog):
    alerts = []
    researcher = AsyncMock(return_value=[])
    monitor = AnnouncementMonitor(repo, alert_sender=alerts.append, asset_researcher=researcher,
                                  now_fn=lambda: NOW)
    settings = AnnouncementSettings(bootstrap_alerts_enabled=True, alert_exchanges=["binance"])
    first = notice(first_source)
    changes = {"fetched_at": NOW + timedelta(seconds=3)}
    if match == "title":
        changes.update(title="will LIST bitcoin BTC!!!", title_zh=None, url="https://different.example")
    elif match == "title_zh":
        changes.update(title="上 线，比特币！", title_zh=None, url="https://different.example")
    else:
        changes.update(title="Different translation", title_zh=None)
    second = notice(second_source, **changes)
    with caplog.at_level(logging.INFO):
        assert (await monitor.process([first], settings))[0].alert_status == "sent"
        created = await monitor.process([second], settings)
        assert created[0].alert_status == "duplicate"
        assert created[0].event_reminder_status == "not_applicable"
        assert await monitor.process([second], settings) == []
    assert researcher.await_count == 1
    assert len(alerts) == 1
    assert f"first_source={first_source} second_source={second_source} lead_seconds=3.0" in caplog.text
    # Replaying richer metadata must never restore a duplicate reminder.
    await repo.create_if_new(second)
    due = await repo.list_due_event_reminders(exchanges={"binance"}, now=NOW,
                                             reminder_due_before=NOW + timedelta(hours=1))
    assert [row.id for row in due] == [first.id]
    app = FastAPI()
    app.state.announcement_repo = repo
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/announcements")
        assert [row["id"] for row in response.json()] == [first.id]
        options = (await client.get("/announcements/exchanges")).json()
        assert NEW_EXCHANGES <= {option["value"] for option in options}


@pytest.mark.parametrize("exchange,official_url,eqn_url,expected_duplicate", [
    ("okx", "https://www.okx.com/zh-hans/help/new-listing",
     "https://www.okx.com/help/new-listing", True),
    ("okx", "https://www.okx.com/zh-hans/help/new-listing",
     "https://www.okx.com/help/another-listing", False),
    ("bitget", "https://www.bitget.com/en/support/articles/123",
     "https://www.bitget.com/zh-CN/support/articles/123", True),
    ("okx", "https://www.okx.com/help/new-listing",
     "https://other.example/help/new-listing", False),
    ("okx", "https://WWW.OKX.COM/zh-HANS/help/new-listing/?from=official#details",
     "https://okx.com/help/new-listing?from=eqn#summary", True),
    ("okx", "https://www.okx.com/help/en",
     "https://www.okx.com/help/ja", False),
    ("okx", "https://www.okx.com/help/en/",
     "https://www.okx.com/help", False),
    ("okx", "https://www.okx.com/english/help/new-listing",
     "https://www.okx.com/help/new-listing", False),
    ("okx", "https://www.okx.com/zh-hans-extra/help/new-listing",
     "https://www.okx.com/help/new-listing", False),
    ("okx", "https://www.okx.com/help/New-Listing",
     "https://www.okx.com/help/new-listing", False),
    ("okx", "", "", False),
    ("okx", "", "https://www.okx.com/help/new-listing", False),
    ("okx", "https://www.okx.com/help/new-listing", "", False),
] + [
    ("okx", f"https://www.okx.com/{locale}/help/new-listing",
     "https://www.okx.com/help/new-listing", True)
    for locale in ("zh-cn", "zh-tw", "en", "en-us", "en-gb", "ko", "ja")
] + [
    ("okx", "https://www.okx.com/zh-hans/help/en-us/new-listing",
     "https://www.okx.com/help/new-listing", True),
])
async def test_cross_source_duplicate_by_locale_agnostic_url(
    repo, exchange, official_url, eqn_url, expected_duplicate,
):
    monitor = AnnouncementMonitor(repo, now_fn=lambda: NOW)
    settings = AnnouncementSettings()
    await monitor.process([
        notice(exchange=exchange, title="即将上线新币", title_zh=None, url=official_url),
    ], settings)

    created = await monitor.process([
        notice("eqn", exchange=exchange, title="A new token listing", title_zh=None, url=eqn_url),
    ], settings)

    assert (created[0].alert_status == "duplicate") is expected_duplicate


@pytest.mark.parametrize("case", ["same_source", "different_exchange", "older", "newer", "unrelated"])
async def test_dedup_boundaries(repo, case):
    first = notice()
    second = notice("eqn")
    if case == "same_source":
        second = notice(announcement_id="another")
    elif case == "different_exchange":
        second = notice("eqn", exchange="upbit")
    elif case in {"older", "newer"}:
        sign = -1 if case == "older" else 1
        second = notice("eqn", published_at=NOW + sign * timedelta(hours=6, seconds=1))
    else:
        second = notice("eqn", title="Unrelated", title_zh=None, url="https://different.example")
    monitor = AnnouncementMonitor(repo, now_fn=lambda: NOW)
    settings = AnnouncementSettings()
    await monitor.process([first], settings)
    assert (await monitor.process([second], settings))[0].alert_status != "duplicate"
    assert len(await repo.list()) == 2


async def test_concurrent_sources_only_send_once(repo):
    alerts = []
    monitor = AnnouncementMonitor(repo, alert_sender=alerts.append, now_fn=lambda: NOW)
    settings = AnnouncementSettings(bootstrap_alerts_enabled=True)
    results = await asyncio.gather(monitor.process([notice()], settings),
                                   monitor.process([notice("eqn")], settings))
    assert sorted(batch[0].alert_status for batch in results) == ["duplicate", "sent"]
    assert len(alerts) == 1


async def test_settings_migrate_once_and_keep_user_deselection(repo):
    old = {"enabled": False, "record_exchanges": ["binance", "okx", "bybit", "gate", "bitget", "hyperliquid"],
           "alert_exchanges": ["okx"], "poll_interval_seconds": 90}
    await repo.db.execute("INSERT INTO app_settings(key, payload) VALUES ('announcements', ?)",
                          (json.dumps(old),))
    await repo.db.commit()
    settings_repo = SettingsRepository(repo.db)
    migrated = await load_announcement_settings(settings_repo)
    assert set(migrated.record_exchanges) == set(old["record_exchanges"]) | NEW_EXCHANGES
    assert not migrated.enabled
    assert migrated.alert_exchanges == ["okx"]
    assert migrated.poll_interval_seconds == 90
    cursor = await repo.db.execute("SELECT payload FROM app_settings WHERE key='announcements'")
    assert json.loads((await cursor.fetchone())["payload"])["exchange_catalog_version"] == 2
    await settings_repo.set_announcement_settings(migrated.model_copy(update={"record_exchanges": ["okx"]}))
    assert (await load_announcement_settings(SettingsRepository(repo.db))).record_exchanges == ["okx"]


@pytest.mark.parametrize("enabled", [True, False])
async def test_factory_respects_env_switch_and_min_interval(monkeypatch, enabled):
    monkeypatch.setenv("EQN_NEWS_ENABLED", str(enabled).lower())
    monkeypatch.setenv("EQN_NEWS_POLL_SECONDS", "1")
    get_settings.cache_clear()
    providers = default_announcement_provider()
    try:
        eqn = [p for p in providers.providers if p.exchange == "eqn"]
        assert len(eqn) == int(enabled)
        if enabled:
            assert eqn[0].poll_interval_seconds == 5
    finally:
        await providers.aclose()
        get_settings.cache_clear()


@pytest.mark.parametrize("enabled,eqn_enabled", [(True, True), (False, True), (True, False)])
async def test_polling_activation_and_own_interval(repo, monkeypatch, enabled, eqn_enabled):
    client = AsyncMock()
    client.list_news.return_value = []
    provider = EqnAnnouncementProvider(client=client, enabled=eqn_enabled, poll_interval_seconds=12)
    stop = asyncio.Event()
    waits = []

    async def wait(event, seconds):
        if seconds > 80:  # The independent reminder loop must not stop the source mid-fetch.
            await event.wait()
            return
        waits.append(seconds)
        stop.set()

    monkeypatch.setattr(announcement_polling, "_wait", wait)
    await announcement_polling.run_announcement_sources(
        [provider], AnnouncementMonitor(repo),
        AsyncMock(return_value=AnnouncementSettings(enabled=enabled, record_exchanges=[],
                                                     poll_interval_seconds=90)), stop,
    )
    assert client.list_news.await_count == (5 if enabled and eqn_enabled else 0)
    assert 11 < waits[0] <= 12


def test_eqn_message_annotation_only():
    assert "[BINANCE（eqn.news）]" in build_announcement_alert_message(notice("eqn"))
    assert "[BINANCE]" in build_announcement_alert_message(notice())


@pytest.mark.parametrize("hours", [-6, 6])
async def test_exact_six_hour_boundary_and_timezone_offsets(repo, hours):
    await repo.create_if_new(notice())
    row = notice("eqn", published_at=NOW + timedelta(hours=hours))
    assert (await repo.find_cross_source_duplicate(row)).source == "official"
    row = notice("eqn", published_at=datetime.fromisoformat("2026-11-17T12:00:00+09:00"))
    assert await repo.find_cross_source_duplicate(row) is not None


async def test_record_scope_still_filters_multi_exchange_batch(repo):
    monitor = AnnouncementMonitor(repo)
    created = await monitor.process([
        notice("eqn", exchange="upbit"), notice("eqn", exchange="bithumb"),
    ], AnnouncementSettings(record_exchanges=["upbit"]))
    assert [row.exchange for row in created] == ["upbit"]


@pytest.mark.parametrize("all_failed", [False, True])
async def test_partial_channel_failure_keeps_other_announcements(all_failed):
    client = AsyncMock()

    async def list_news(channel, **kwargs):
        if all_failed or channel != "korea":
            raise ValueError("unavailable")
        return json.loads(fixture("list_news_korea")["result"]["content"][0]["text"])["items"]

    client.list_news.side_effect = list_news
    provider = EqnAnnouncementProvider(client=client)
    if all_failed:
        with pytest.raises(ValueError, match="All eqn"):
            await provider.fetch()
    else:
        rows = await provider.fetch()
        assert rows and {row.exchange for row in rows} == {"upbit", "bithumb"}
        assert set(provider.last_source_articles) == {"korea"}
    assert len(provider.last_source_errors) == (5 if all_failed else 4)


@pytest.mark.parametrize("hanging_channel", announcement_eqn.CHANNELS)
async def test_channel_timeout_keeps_other_announcements(monkeypatch, hanging_channel):
    monkeypatch.setattr(announcement_eqn, "CHANNEL_TIMEOUT_SECONDS", 0.01)
    client = AsyncMock()
    item = json.loads(fixture("list_news_korea")["result"]["content"][0]["text"])["items"][0]

    async def list_news(channel, **kwargs):
        if channel == hanging_channel:
            await asyncio.Event().wait()
        return [dict(item, no=announcement_eqn.CHANNELS.index(channel))]

    client.list_news.side_effect = list_news
    provider = EqnAnnouncementProvider(client=client)
    rows = await asyncio.wait_for(provider.fetch(), timeout=1.0)

    successful_channels = set(announcement_eqn.CHANNELS) - {hanging_channel}
    assert len(rows) == len(successful_channels)
    assert {row.category for row in rows} == successful_channels
    assert set(provider.last_source_articles) == successful_channels
    assert provider.last_source_errors == {hanging_channel: "TimeoutError"}
    assert [call.args[0] for call in client.list_news.await_args_list] == list(
        announcement_eqn.CHANNELS
    )


async def test_fetch_propagates_outer_cancellation():
    client = AsyncMock()
    started = asyncio.Event()

    async def list_news(channel, **kwargs):
        started.set()
        await asyncio.Event().wait()

    client.list_news.side_effect = list_news
    provider = EqnAnnouncementProvider(client=client)
    async with asyncio.TaskGroup() as tasks:
        task = tasks.create_task(provider.fetch())
        await asyncio.wait_for(started.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert client.list_news.await_count == 1
    assert provider.last_source_errors == {}


async def test_only_unknown_sources_are_empty_success(caplog):
    client = AsyncMock()
    client.list_news.return_value = [{"source": "Price Monitor"}]
    provider = EqnAnnouncementProvider(client=client)
    with caplog.at_level(logging.DEBUG):
        assert await provider.fetch() == []
    assert provider.last_source_errors == {}
    assert "source=Price Monitor count=5" in caplog.text


@pytest.mark.parametrize("result", [{}, {"content": []}, {"content": [{"text": "{}"}]},
                                    {"content": [{"text": "not json"}]}])
async def test_malformed_tool_results_are_failures(result):
    def respond(request):
        if json.loads(request.content)["method"] == "initialize":
            return httpx.Response(200, json=fixture("initialize"))
        return httpx.Response(200, json={"result": result})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises((ValueError, TypeError)):
            await news_client(client).list_news("binance")


async def test_multiline_sse_ignores_notifications():
    def respond(request):
        method = json.loads(request.content)["method"]
        payload = fixture("initialize") if method == "initialize" else news_result([])
        data = "\n".join("data: " + line for line in json.dumps(payload, indent=2).splitlines())
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=
                              'data: {"method":"notifications/message"}\n\n' + data + "\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await news_client(client).list_news("binance") == []


async def test_owned_client_is_closed_and_defaults_are_ten_seconds():
    provider = EqnAnnouncementProvider()
    assert provider.poll_interval_seconds == 10
    assert not hasattr(provider, "localize")
    await provider.aclose()
    assert provider.client.client.is_closed


async def test_settings_migration_preserves_old_deselections_and_concurrent_reads(repo):
    await repo.db.execute("INSERT INTO app_settings(key, payload) VALUES ('announcements', ?)",
                          (json.dumps({"record_exchanges": [], "exchange_catalog_version": 1}),))
    await repo.db.commit()
    readers = [SettingsRepository(repo.db) for _ in range(4)]
    results = await asyncio.gather(*(load_announcement_settings(reader) for reader in readers))
    assert all(set(settings.record_exchanges) == NEW_EXCHANGES for settings in results)
    await readers[0].set_announcement_settings(AnnouncementSettings(record_exchanges=[]))
    assert (await load_announcement_settings(readers[-1])).record_exchanges == []
