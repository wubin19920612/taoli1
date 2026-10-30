import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models.announcement import AnnouncementKind, AnnouncementSettings, ExchangeAnnouncement
from app.services import announcement_localization, announcement_polling
from app.services.announcement_localization import AnnouncementLocalizationService
from app.services.announcements import BybitAnnouncementProvider


def announcement(exchange="bybit", announcement_id="article-1"):
    return ExchangeAnnouncement(
        exchange=exchange,
        announcement_id=announcement_id,
        kind=AnnouncementKind.LISTING,
        title="New listing: TEST",
        url=f"https://announcements.bybit.com/en-US/article/{announcement_id}/",
        source="test-source",
        symbols=["TEST"],
        published_at=datetime(2026, 10, 29, tzinfo=UTC),
        fetched_at=datetime(2026, 10, 29, tzinfo=UTC),
    )


def chinese_payload(title="新币上架 TEST", announcement_id="article-1"):
    return {"result": {"list": [{
        "id": announcement_id,
        "title": title,
        "url": f"https://announcements.bybit.com/zh-TW/article/{announcement_id}/",
    }]}}


def http_error(status):
    response = httpx.Response(status, request=httpx.Request("GET", "https://source.test/feed"))
    return httpx.HTTPStatusError("source unavailable", request=response.request, response=response)


@pytest.mark.asyncio
async def test_slow_chinese_source_preserves_fast_translation_within_outer_budget():
    timeouts = []
    cancelled = asyncio.Event()

    async def respond(request):
        timeouts.append(request.extensions["timeout"])
        if request.url.params.get("type") == "new_crypto":
            try:
                await asyncio.sleep(3.2)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return httpx.Response(200, json=chinese_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = BybitAnnouncementProvider(client)
        original = announcement()
        started = time.monotonic()
        async with asyncio.timeout(3.0):
            rows = await provider.localize([original])
        elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert cancelled.is_set()
    assert rows[0].title_zh == "新币上架 TEST"
    assert rows[0].url_zh == "https://announcements.bybit.com/zh-TW/article/article-1/"
    assert rows[0].title == original.title
    assert rows[0].announcement_id == original.announcement_id
    assert timeouts and all(
        budget == {"connect": 2.5, "read": 2.5, "write": 2.5, "pool": 2.5}
        for budget in timeouts
    )
    assert announcement_localization.LOCALIZATION_SOURCE_TIMEOUT_SECONDS == 2.5


@pytest.mark.asyncio
async def test_all_failed_sources_keep_cached_translation_and_retry_after_30_seconds(monkeypatch):
    clock = SimpleNamespace(monotonic=lambda: 1000.0)
    monkeypatch.setattr(announcement_localization, "time", clock)
    failing = False
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if failing:
            raise httpx.ConnectTimeout("offline source", request=request)
        return httpx.Response(200, json=chinese_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("bybit", client)
        first = await service.enrich([announcement()])
        assert first[0].title_zh == "新币上架 TEST"
        assert service._expires_at == 1120.0

        failing = True
        clock.monotonic = lambda: 1121.0
        rows = await service.enrich([announcement()])
        assert rows[0].title_zh == first[0].title_zh
        assert rows[0].url_zh == first[0].url_zh
        assert service._expires_at == 1151.0
        assert calls == 6

        clock.monotonic = lambda: 1150.0
        await service.enrich([announcement()])
        assert calls == 6
        clock.monotonic = lambda: 1151.0
        await service.enrich([announcement()])
        assert calls == 9


@pytest.mark.asyncio
async def test_successful_source_updates_keys_while_failed_source_retains_cached_keys(monkeypatch):
    clock = SimpleNamespace(monotonic=lambda: 1000.0)
    monkeypatch.setattr(announcement_localization, "time", clock)
    refreshed = False

    def respond(request):
        if request.url.params.get("type") == "delistings":
            if refreshed:
                raise httpx.ReadTimeout("offline source", request=request)
            payload = chinese_payload("旧的下架公告", "delisting-1")
        else:
            payload = chinese_payload("更新的上架公告" if refreshed else "旧的上架公告")
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("bybit", client)
        original = [announcement(), announcement(announcement_id="delisting-1")]
        await service.enrich(original)
        refreshed = True
        clock.monotonic = lambda: 1121.0
        rows = await service.enrich(original)

    assert [row.title_zh for row in rows] == ["更新的上架公告", "旧的下架公告"]
    assert service._expires_at == 1241.0


@pytest.mark.asyncio
async def test_chinese_source_logs_dedupe_per_url_and_error_type_and_reset_on_recovery(caplog):
    caplog.set_level(logging.DEBUG, logger=announcement_localization.__name__)
    outcomes = {
        "/first": [httpx.ConnectTimeout("one"), httpx.ConnectTimeout("two"),
                   httpx.ReadTimeout("changed"), None, None, httpx.ReadTimeout("again")],
        "/second": [httpx.ConnectTimeout("other source"), httpx.ConnectTimeout("repeated")],
    }

    def respond(request):
        outcome = outcomes[request.url.path].pop(0)
        if outcome is not None:
            raise outcome
        return httpx.Response(200, json=chinese_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("bybit", client)
        for path in ("first", "first", "second", "first", "second", "first", "first", "first"):
            await service._fetch(f"https://source.test/{path}", {})

    records = [record for record in caplog.records
               if record.name == announcement_localization.__name__]
    failures = [record for record in records if "source unavailable" in record.getMessage()]
    assert [record.levelno for record in failures] == [
        logging.WARNING, logging.DEBUG, logging.WARNING,
        logging.WARNING, logging.DEBUG, logging.WARNING,
    ]
    recovered = [record for record in records if "recover" in record.getMessage().lower()]
    assert len(recovered) == 1
    assert recovered[0].levelno == logging.INFO
    assert "https://source.test/first" in recovered[0].getMessage()


@pytest.mark.asyncio
async def test_chinese_source_cancellation_is_not_swallowed():
    async def respond(request):
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("bybit", client)
        with pytest.raises(asyncio.CancelledError):
            await service._fetch("https://source.test/feed", {})


class ScriptedProvider:
    def __init__(self, outcomes, stage, exchange="bybit"):
        self.outcomes = outcomes
        self.stage = stage
        self.exchange = exchange
        self.calls = 0
        self.last_source_articles = {"fake-source": ["article-1"]}
        self.last_source_errors = {}

    async def fetch_headlines(self):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if self.stage == "poll" and outcome is not None:
            raise outcome
        return [announcement(self.exchange)]

    async def localize(self, rows):
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if self.stage == "localization" and outcome is not None:
            raise outcome
        return rows


async def run_scripted_polls(monkeypatch, providers):
    stop = asyncio.Event()
    states = {}
    observations = {provider.exchange: [] for provider in providers}

    async def get_state(key):
        return states.get(key)

    async def set_state(key, state):
        states[key] = state
        observations[state["exchange"]].append(state["latest"])
        if all(len(observations[provider.exchange]) >= len(provider.outcomes)
               for provider in providers):
            stop.set()

    repository = SimpleNamespace(
        has_any=AsyncMock(return_value=True),
        get_provider_state=get_state,
        set_provider_state=set_state,
    )
    monitor = SimpleNamespace(
        repository=repository,
        process=AsyncMock(return_value=[]),
        process_due_event_reminders=AsyncMock(),
    )
    settings = AnnouncementSettings(record_exchanges=[provider.exchange for provider in providers])
    waits = []

    async def wait(stop_event, seconds):
        waits.append(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(announcement_polling, "_wait", wait)
    await asyncio.wait_for(announcement_polling.run_announcement_sources(
        providers, monitor, AsyncMock(return_value=settings), stop,
    ), timeout=2.0)
    assert waits and all(0 < seconds <= 30 for seconds in waits)
    return observations, states, monitor


def stage_records(caplog, stage, text):
    return [record for record in caplog.records
            if record.name == announcement_polling.__name__
            and f"Announcement {stage} {text}" in record.getMessage()]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["poll", "localization"])
async def test_repeated_provider_timeouts_log_one_traceback_and_recovery(monkeypatch, caplog, stage):
    caplog.set_level(logging.INFO, logger=announcement_polling.__name__)
    provider = ScriptedProvider([
        httpx.ConnectTimeout("first"), httpx.ConnectTimeout("second"),
        httpx.ConnectTimeout("third"), None, None, httpx.ConnectTimeout("after recovery"),
    ], stage)
    observations, states, monitor = await run_scripted_polls(monkeypatch, [provider])

    failures = stage_records(caplog, stage, "failed")
    assert [bool(record.exc_info) for record in failures] == [True, False, False, True]
    assert all(record.levelno == logging.WARNING for record in failures)
    assert re.search(r"\b2\b", failures[1].getMessage())
    assert re.search(r"\b3\b", failures[2].getMessage())
    recovered = stage_records(caplog, stage, "recovered")
    assert len(recovered) == 1
    assert recovered[0].levelno == logging.INFO
    assert "recovered after 3 failures" in recovered[0].getMessage()

    rows = observations[provider.exchange]
    assert len(rows) == 6
    assert all(row["source_articles"] == provider.last_source_articles for row in rows)
    assert all(row["source_errors"] == {} for row in rows)
    if stage == "poll":
        assert [row["status"] for row in rows] == ["error"] * 3 + ["ok", "ok", "error"]
        assert [row.get("error") for row in rows] == ["ConnectTimeout"] * 3 + [None, None, "ConnectTimeout"]
        assert monitor.process.await_count == 2
    else:
        assert all(row["status"] == "ok" for row in rows)
        assert [row.get("localize_error") for row in rows] == ["ConnectTimeout"] * 3 + [None, None, "ConnectTimeout"]
        assert all("article-1" in row["articles"] for row in rows)
        assert monitor.process.await_count == 6
        assert all(call.args[0][0].title == "New listing: TEST"
                   for call in monitor.process.await_args_list)
    history = states[announcement_polling.poll_state_key(provider)]["history"]
    assert [entry["polls"] for entry in history] == [3, 2, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["poll", "localization"])
async def test_provider_signature_includes_http_status(monkeypatch, caplog, stage):
    caplog.set_level(logging.INFO, logger=announcement_polling.__name__)
    provider = ScriptedProvider([
        httpx.ConnectTimeout("offline"), http_error(500), http_error(500),
        http_error(503), http_error(503), None,
    ], stage)
    await run_scripted_polls(monkeypatch, [provider])

    failures = stage_records(caplog, stage, "failed")
    assert [bool(record.exc_info) for record in failures] == [True, True, False, True, False]
    recovered = stage_records(caplog, stage, "recovered")
    assert len(recovered) == 1
    assert "recovered after 5 failures" in recovered[0].getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["poll", "localization"])
async def test_provider_failure_state_is_independent_per_provider(monkeypatch, caplog, stage):
    caplog.set_level(logging.INFO, logger=announcement_polling.__name__)
    providers = [ScriptedProvider([
        httpx.ConnectTimeout("first"), httpx.ConnectTimeout("second"), None,
    ], stage, exchange) for exchange in ("bybit", "bitget")]
    await run_scripted_polls(monkeypatch, providers)

    failures = stage_records(caplog, stage, "failed")
    assert sum(bool(record.exc_info) for record in failures) == 2
    assert sum(not record.exc_info for record in failures) == 2
    recovered = stage_records(caplog, stage, "recovered")
    assert len(recovered) == 2
    assert all("recovered after 2 failures" in record.getMessage() for record in recovered)


@pytest.mark.asyncio
async def test_successful_poll_does_not_clear_localization_failure_state(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=announcement_polling.__name__)
    provider = ScriptedProvider([
        httpx.ConnectTimeout("translation failed"), None, None,
    ], "localization")
    fetch = provider.fetch_headlines

    async def fetch_with_failure():
        rows = await fetch()
        if provider.calls == 2:
            raise httpx.ConnectTimeout("headlines failed")
        return rows

    provider.fetch_headlines = fetch_with_failure
    await run_scripted_polls(monkeypatch, [provider])

    assert len(stage_records(caplog, "poll", "failed")) == 1
    assert len(stage_records(caplog, "localization", "failed")) == 1
    for stage in ("poll", "localization"):
        recovered = stage_records(caplog, stage, "recovered")
        assert len(recovered) == 1
        assert "recovered after 1 failures" in recovered[0].getMessage()


@pytest.mark.asyncio
async def test_source_cache_replaces_all_entries_across_1000_successful_refreshes(monkeypatch):
    source_url = "https://source.test/rotating"
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=chinese_payload(announcement_id=f"article-{calls}"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("bybit", client)
        monkeypatch.setattr(service, "_requests", lambda: [(source_url, {})])
        for _ in range(1000):
            await service._refresh()

    expected = {"article-1000": (
        "新币上架 TEST", "https://announcements.bybit.com/zh-TW/article/article-1000/",
    )}
    assert calls == 1000
    assert service._cache == expected
    assert service._source_cache == {source_url: expected}


@pytest.mark.asyncio
async def test_source_cache_replaces_successful_source_and_preserves_failed_source(monkeypatch):
    source_a = "https://source.test/a"
    source_b = "https://source.test/b"
    refreshed = False
    calls = []

    def respond(request):
        calls.append(str(request.url))
        if str(request.url) == source_b:
            if refreshed:
                raise httpx.ReadTimeout("source B unavailable", request=request)
            payload = chinese_payload("源 B 上次成功的公告", "article-b")
        else:
            payload = chinese_payload("源 A 公告", "article-a-new" if refreshed else "article-a-old")
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("bybit", client)
        monkeypatch.setattr(service, "_requests", lambda: [(source_a, {}), (source_b, {})])
        await service._refresh()
        refreshed = True
        await service._refresh()
        assert set(service._cache) == {"article-a-new", "article-b"}
        rows = await service.enrich([
            announcement(announcement_id="article-a-new"),
            announcement(announcement_id="article-b"),
        ])

    assert [row.title_zh for row in rows] == ["源 A 公告", "源 B 上次成功的公告"]
    assert rows[1].url_zh == "https://announcements.bybit.com/zh-TW/article/article-b/"
    assert calls == [source_a, source_b, source_a, source_b]


@pytest.mark.asyncio
@pytest.mark.parametrize("keep_source_a", [True, False])
async def test_source_cache_removes_entries_for_sources_no_longer_requested(monkeypatch, keep_source_a):
    source_a = "https://source.test/a"
    source_b = "https://source.test/b"
    requests = [(source_a, {}), (source_b, {})]
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=chinese_payload(announcement_id=request.url.path[1:]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("bybit", client)
        monkeypatch.setattr(service, "_requests", lambda: requests)
        await service._refresh()
        assert set(service._cache) == {"a", "b"}
        requests[:] = [(source_a, {})] if keep_source_a else []
        await service._refresh()

    assert set(service._cache) == ({"a"} if keep_source_a else set())
    assert set(service._source_cache) == ({source_a} if keep_source_a else set())
    assert calls == [source_a, source_b] + ([source_a] if keep_source_a else [])


@pytest.mark.asyncio
async def test_empty_success_clears_source_cache_and_keeps_failed_source_with_short_retry(monkeypatch):
    clock = SimpleNamespace(monotonic=lambda: 1000.0)
    monkeypatch.setattr(announcement_localization, "time", clock)
    source_a = "https://source.test/a"
    source_b = "https://source.test/b"
    refreshed = False

    def respond(request):
        if refreshed:
            if str(request.url) == source_b:
                raise httpx.ConnectTimeout("source B unavailable", request=request)
            return httpx.Response(200, json={"result": {"list": []}})
        return httpx.Response(200, json=chinese_payload(announcement_id=request.url.path[1:]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("bybit", client)
        monkeypatch.setattr(service, "_requests", lambda: [(source_a, {}), (source_b, {})])
        await service._refresh()
        assert set(service._cache) == {"a", "b"}
        assert service._expires_at == 1120.0
        refreshed = True
        await service._refresh()

    assert set(service._cache) == {"b"}
    assert service._source_cache[source_a] == {}
    assert service._cache == service._source_cache[source_b]
    assert service._expires_at == 1030.0
