import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.db.database import connect_database
from app.db.repositories import AnnouncementRepository
from app.db.schema import initialize_schema
from app.models.announcement import AnnouncementSettings
from app.services.announcements import (
    AnnouncementMonitor,
    BitgetAnnouncementProvider,
    BitgetWebsiteAnnouncementProvider,
)

NOW = datetime(2026, 10, 14, 3, 28, tzinfo=UTC)
NOTICE_ID = "12560603897463"
SECTION = "360007868532"
TITLE = "Bitget announcement on the temporary adjustment to the COPPERUSDT futures index"


def page(title=TITLE, rows=None, section=SECTION):
    if rows is None:
        rows = [{"contentId": NOTICE_ID, "title": title, "showTime": "1791948439000"}]
    data = {"queries": [{"queryKey": ["sections", section], "state": {
        "data": {"sectionArticle": {"items": rows}},
    }}]}
    return "<script>window.__ZEUS_REACT_QUERY_STATE__ = " + json.dumps(data) + ";</script>"


@pytest.mark.asyncio
async def test_website_finds_notice_even_when_public_api_has_not_published_it():
    def respond(request):
        if request.url.host == "api.bitget.com":
            return httpx.Response(200, json={"code": "00000", "data": []})
        title = "【重要】Bitget 关于COPPERUSDT 合约指数临时调整公告" if "/zh-CN/" in request.url.path else TITLE
        return httpx.Response(200, text=page(title, section=request.url.path.rsplit("/", 1)[-1]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        api = BitgetAnnouncementProvider(client=client, now_fn=lambda: NOW)
        web = BitgetWebsiteAnnouncementProvider(client=client, now_fn=lambda: NOW)
        assert await api.fetch_headlines() == []
        rows = await web.fetch_headlines()
    assert len(rows) == 1
    assert rows[0].announcement_id == NOTICE_ID
    assert rows[0].source == api.source
    assert rows[0].title == TITLE
    assert rows[0].title_zh.startswith("【重要】")
    assert rows[0].url_zh.endswith(NOTICE_ID)
    assert rows[0].published_at == datetime(2026, 10, 14, 3, 27, 19, tzinfo=UTC)
    assert web.last_source_errors == {}
    assert all(ids == [NOTICE_ID] for ids in web.last_source_articles.values())
    assert len(web.last_source_articles) == 4
    assert all(ids == [] for ids in api.last_source_articles.values())


@pytest.mark.asyncio
async def test_chinese_website_does_not_wait_for_english_translation_to_exist():
    def respond(request):
        if "/zh-CN/" in request.url.path:
            return httpx.Response(200, text=page("Bitget 关于 COPPERUSDT 合约指数调整公告"))
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        rows = await BitgetWebsiteAnnouncementProvider(client=client, now_fn=lambda: NOW).fetch()
    assert len(rows) == 1
    assert rows[0].title_zh == rows[0].title
    assert rows[0].url.startswith("https://www.bitget.com/zh-CN/")


@pytest.mark.asyncio
async def test_website_and_api_share_durable_dedup_identity():
    db = await connect_database(":memory:")
    alerts = []
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=page()),
        )) as client:
            rows = await BitgetWebsiteAnnouncementProvider(client=client, now_fn=lambda: NOW).fetch()
            api_row = BitgetAnnouncementProvider(client=client)._parse_payload({"data": [{
                "annId": NOTICE_ID, "annTitle": TITLE,
                "annUrl": rows[0].url, "cTime": "1791948439000",
            }]}, "latest_news")[0]
        await repo.create_if_new(rows[0].model_copy(update={
            "id": "baseline", "announcement_id": "baseline", "alert_status": "muted",
        }))
        monitor = AnnouncementMonitor(repo, alert_sender=alerts.append, now_fn=lambda: NOW)
        settings = AnnouncementSettings(other_alerts_enabled=True)
        created = await monitor.process(rows, settings)
        assert created[0].alert_status == "sent"
        assert await monitor.process([api_row], settings) == []
        assert len(alerts) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_challenge_page_is_failure_not_empty_success():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>Verify your browser</html>"),
    )) as client:
        with pytest.raises(ValueError, match="Bitget"):
            await BitgetWebsiteAnnouncementProvider(client=client).fetch()


def test_website_rejects_undated_articles_and_non_numeric_ids():
    provider = BitgetWebsiteAnnouncementProvider()
    rows = [{"contentId": "123", "title": TITLE},
            {"contentId": "https://evil.test", "title": TITLE, "showTime": "1791948439000"}]
    assert provider._parse_page(page(rows=rows), SECTION, chinese=False) == []


@pytest.mark.asyncio
async def test_slow_source_does_not_delay_next_poll_of_fast_source():
    from app.services.announcement_polling import run_announcement_sources

    stop = asyncio.Event()
    blocked = asyncio.Event()
    calls = []
    state = {}

    class Repository:
        async def has_any(self):
            return True

        async def get_provider_state(self, key):
            return state.get(key)

        async def set_provider_state(self, key, value):
            state[key] = value

    class Monitor:
        repository = Repository()

        async def process(self, rows, settings, bootstrap=False):
            return []

        async def process_due_event_reminders(self, settings):
            return []

    class Slow:
        exchange = "gate"

        async def fetch_headlines(self):
            await blocked.wait()
            return []

    class Fast:
        exchange = "bitget"

        async def fetch_headlines(self):
            calls.append(asyncio.get_running_loop().time())
            if len(calls) == 3:
                stop.set()
            return []

    async def settings_loader():
        return AnnouncementSettings().model_copy(update={"poll_interval_seconds": 0.01})

    await asyncio.wait_for(run_announcement_sources(
        [Slow(), Fast()], Monitor(), settings_loader, stop,
        min_interval_seconds=0.01, fetch_timeout_seconds=0.5,
    ), timeout=1)
    assert len(calls) == 3
    assert calls[-1] - calls[0] < 0.3
    assert state["poll:bitget:Fast"]["latest"]["status"] == "ok"
    assert state["poll:bitget:Fast"]["history"][-1]["polls"] == 3


@pytest.mark.asyncio
async def test_translation_timeout_preserves_headline_and_failed_fetch_recovers():
    from app.services.announcement_polling import run_announcement_sources

    stop = asyncio.Event()
    alerts = []
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=page()),
        )) as client:
            rows = await BitgetWebsiteAnnouncementProvider(client=client, now_fn=lambda: NOW).fetch()
        await repo.create_if_new(rows[0].model_copy(update={
            "id": "baseline", "announcement_id": "baseline", "alert_status": "muted",
        }))

        class Unstable:
            exchange = "bitget"
            calls = 0

            async def fetch_headlines(self):
                self.calls += 1
                if self.calls == 1:
                    await asyncio.Event().wait()
                return rows

            async def localize(self, headlines):
                await asyncio.Event().wait()

        async def settings_loader():
            return AnnouncementSettings(other_alerts_enabled=True).model_copy(update={
                "poll_interval_seconds": 0.01,
            })

        task = asyncio.create_task(run_announcement_sources(
            [Unstable()], AnnouncementMonitor(repo, alert_sender=alerts.append, now_fn=lambda: NOW),
            settings_loader, stop, min_interval_seconds=0.01,
            fetch_timeout_seconds=0.01, localize_timeout_seconds=0.01,
        ))
        try:
            async with asyncio.timeout(2):
                while True:
                    state = await repo.get_provider_state("poll:bitget:Unstable")
                    if state and state["latest"]["status"] == "ok":
                        break
                    await asyncio.sleep(0.005)
            assert state["history"][0]["result"]["error"] == "TimeoutError"
            assert state["latest"]["localize_error"] == "TimeoutError"
            assert NOTICE_ID in state["latest"]["articles"]
            assert len(alerts) == 1
        finally:
            stop.set()
            await task
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_source_diagnostics_api_and_bounded_history():
    from types import SimpleNamespace

    from fastapi import FastAPI

    from app.api.routes_announcements import router
    from app.services.announcement_polling import _record_poll

    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        provider = SimpleNamespace(exchange="bitget")
        for index in range(122):
            await _record_poll(repo, provider, {
                "started_at": NOW.isoformat(), "finished_at": NOW.isoformat(),
                "status": "ok", "articles": {str(index): NOW.isoformat()},
            })
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.state.announcement_repo = repo
        app.state.announcement_provider = SimpleNamespace(providers=[provider])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            latest = (await client.get("/api/announcements/sources?exchange=BITGET")).json()
            assert latest[0]["latest"]["articles"] == {"121": NOW.isoformat()}
            assert "history" not in latest[0]
            full = (await client.get("/api/announcements/sources?include_history=true")).json()
            assert len(full[0]["history"]) == 120
            assert full[0]["history"][0]["result"]["articles"] == {"2": NOW.isoformat()}
            assert (await client.get("/api/announcements/sources?exchange=gate")).json() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_default_window_keeps_historical_copper_muted():
    from datetime import timedelta

    db = await connect_database(":memory:")
    alerts = []
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=page()),
        )) as client:
            rows = await BitgetWebsiteAnnouncementProvider(client=client, now_fn=lambda: NOW).fetch()
        await repo.create_if_new(rows[0].model_copy(update={
            "id": "baseline", "announcement_id": "baseline", "alert_status": "muted",
        }))
        settings = AnnouncementSettings(other_alerts_enabled=True)
        assert settings.alert_max_age_minutes == 30
        monitor = AnnouncementMonitor(repo, alert_sender=alerts.append,
                                      now_fn=lambda: NOW + timedelta(minutes=85))
        created = await monitor.process(rows, settings)
        assert created[0].alert_status == "muted"
        assert alerts == []
    finally:
        await db.close()
