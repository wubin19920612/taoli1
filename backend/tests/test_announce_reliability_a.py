import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from app.services import announcements as module
from app.services.announcements import (
    BitgetWebsiteAnnouncementProvider,
    GateAnnouncementProvider,
    OKXAnnouncementProvider,
)


NOW = datetime(2026, 10, 29, tzinfo=UTC)
LOGGER = "app.services.announcements"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def gate_page() -> str:
    payload = {"props": {"pageProps": {"listData": {"list": [{
        "id": 60001, "title": "Gate Launchpool TEST announcement",
        "url": "/announcements/article/60001", "release_timestamp": "1793232000",
    }]}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


@pytest.mark.asyncio
@pytest.mark.parametrize("step, expected_count", [(16.25, 1), (3.25, 5)])
@pytest.mark.parametrize("headlines", [False, True])
async def test_gate_exhausted_budget_keeps_categories_and_skips_reader(
    caplog, step, expected_count, headlines,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    clock = Clock()
    calls = []

    def transport(request):
        calls.append(request.url)
        if request.url.host == "apim.gateapi.io":
            clock.now += step
            return httpx.Response(200, text=gate_page()) if len(calls) == 1 else httpx.Response(503)
        return httpx.Response(200, text=gate_page())

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = GateAnnouncementProvider(client=client, now_fn=lambda: NOW, monotonic_fn=clock)
        rows = await (provider.fetch_headlines() if headlines else provider.fetch())
    assert [row.announcement_id for row in rows] == ["60001"]
    assert len(calls) == expected_count
    assert all(url.host == "apim.gateapi.io" for url in calls)
    assert any(record.levelno == logging.DEBUG and "reader" in record.getMessage()
               for record in caplog.records)


@pytest.mark.asyncio
async def test_gate_slow_readers_return_categories_at_remaining_deadline():
    clock = Clock()
    started_readers = []
    cancelled_readers = []

    async def transport(request):
        if request.url.host == "apim.gateapi.io":
            if request.url.path.endswith("newspotlistings"):
                return httpx.Response(200, text=gate_page())
            if request.url.path == "/announcements":
                clock.now += 15.9  # 1.1 seconds remain for both readers together.
            return httpx.Response(503)
        started_readers.append(request)
        try:
            await asyncio.Event().wait()  # MockTransport does not enforce httpx timeouts.
        except asyncio.CancelledError:
            cancelled_readers.append(request)
            raise

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = GateAnnouncementProvider(client=client, now_fn=lambda: NOW, monotonic_fn=clock)
        start = asyncio.get_running_loop().time()
        rows = await asyncio.wait_for(provider.fetch_headlines(), timeout=2.5)
        elapsed = asyncio.get_running_loop().time() - start
    assert [row.announcement_id for row in rows] == ["60001"]
    assert 1.0 <= elapsed < 2.0  # Scheduler tolerance; must not wait for the old 15-second timeout.
    assert len(started_readers) == len(cancelled_readers) == 2
    for request in started_readers:
        assert request.extensions["timeout"]["read"] == pytest.approx(0.6)
        assert request.extensions["timeout"]["connect"] <= 0.6 + 1e-6


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["category", "reader"])
async def test_gate_external_cancellation_propagates(phase):
    clock = Clock()
    waiting = asyncio.Event()
    active = 0

    async def transport(request):
        nonlocal active
        if phase == "category" or request.url.host == "r.jina.ai":
            active += 1
            waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = GateAnnouncementProvider(client=client, monotonic_fn=clock)
        task = asyncio.create_task(provider.fetch_headlines())
        try:
            await asyncio.wait_for(waiting.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert active == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [503, "timeout"])
async def test_gate_reader_failure_dedup_signature_and_recovery(caplog, changed):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    outcome = 403

    def transport(request):
        assert request.url.host == "r.jina.ai"
        if outcome == "timeout":
            raise httpx.ReadTimeout("reader timed out", request=request)
        return httpx.Response(outcome, text=gate_page())

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = GateAnnouncementProvider(client=client, now_fn=lambda: NOW, monotonic_fn=Clock())
        # Reader and direct category failure state must be independent.
        provider._category_failures[""] = "HTTPStatusError:403"
        for _ in range(3):
            assert await provider._fetch_reader_page("") == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert warnings[0].exc_info
        repeated = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(repeated) == 2 and all(r.exc_info is None for r in repeated)

        caplog.clear()
        outcome = changed
        assert await provider._fetch_reader_page("") == []
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

        caplog.clear()
        outcome = 200
        for _ in range(2):
            assert len(await provider._fetch_reader_page("")) == 1
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1 and "recovered" in infos[0].getMessage()
        assert provider._category_failures[""] == "HTTPStatusError:403"

        caplog.clear()
        outcome = 403
        await provider._fetch_reader_page("")
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def source_labels(provider):
    if isinstance(provider, OKXAnnouncementProvider):
        return [f"api:{value or 'all'}" for value in provider.ann_types] + list(provider.latest_urls)
    return [f"{section}:{language}" for section in provider.section_ids for language in ("en", "zh")]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", [OKXAnnouncementProvider, BitgetWebsiteAnnouncementProvider])
@pytest.mark.parametrize("failure", ["http", "network", "payload"])
async def test_all_source_failures_have_bounded_reasons_and_summary(provider_class, failure):
    def transport(request):
        if failure == "http":
            return httpx.Response(403)
        if failure == "network":
            raise httpx.ReadTimeout("slow upstream " + "x" * 300, request=request)
        if request.url.path.startswith("/api/"):
            return httpx.Response(200, json={"code": "1"})
        return httpx.Response(200, text="<html>no announcement payload</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = provider_class(client=client, now_fn=lambda: NOW)
        with pytest.raises(ValueError) as caught:
            await provider.fetch_headlines()
    assert set(provider.last_source_errors) == set(source_labels(provider))
    for label, reason in provider.last_source_errors.items():
        assert 0 < len(reason) <= 200
        assert f"{label}={reason}" in str(caught.value)
        if failure == "http":
            assert reason == "HTTP 403" or ("HTTPStatusError" in reason and "403" in reason)
        elif failure == "network":
            assert reason.startswith("ReadTimeout: slow upstream ")
        else:
            assert reason.startswith("ValueError: ")


def bitget_page(section, *, chinese=False, empty=False):
    payload = {"queries": [{"queryKey": ["sections", section], "state": {"data": {
        "sectionArticle": {"items": [] if empty else [{
            "contentId": "12345", "title": "维护公告" if chinese else "Maintenance notice",
            "showTime": int(NOW.timestamp() * 1000),
        }]},
    }}}]}
    return "<script>window.__ZEUS_REACT_QUERY_STATE__=" + json.dumps(payload) + ";</script>"


@pytest.mark.asyncio
async def test_bitget_partial_success_preserves_merge_empty_success_and_clears_errors():
    mode = "partial"
    first = BitgetWebsiteAnnouncementProvider.section_ids[0]

    def transport(request):
        section = request.url.path.rsplit("/", 1)[-1]
        if mode != "recovered" and section != first:
            return httpx.Response(503)
        return httpx.Response(200, text=bitget_page(
            section, chinese=request.url.path.startswith("/zh-CN"), empty=mode == "empty",
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = BitgetWebsiteAnnouncementProvider(client=client, now_fn=lambda: NOW)
        rows = await provider.fetch()
        assert len(rows) == 1
        assert rows[0].announcement_id == "12345"
        assert rows[0].title == "Maintenance notice" and rows[0].title_zh == "维护公告"
        assert len(provider.last_source_errors) == 2
        assert provider.last_source_articles[f"{first}:en"] == ["12345"]
        mode = "empty"
        assert await provider.fetch() == []  # Empty parsed pages still count as success.
        mode = "recovered"
        assert len(await provider.fetch()) == 1
        assert provider.last_source_errors == {}


@pytest.mark.asyncio
async def test_okx_partial_success_preserves_slug_dedup_and_clears_errors():
    mode = "partial"
    article = {"title": "Maintenance notice", "url": "/help/maintenance",
               "pTime": str(int(NOW.timestamp() * 1000))}

    def transport(request):
        if request.url.path.startswith("/api/"):
            if mode == "partial" and request.url.params.get("annType") == "announcements-delistings":
                return httpx.Response(503)
            return httpx.Response(200, json={"code": "0", "data": [{"details": [article]}]})
        if mode == "partial":
            return httpx.Response(200, text="no payload")
        return httpx.Response(200, text='<script id="__NEXT_DATA__">' + json.dumps(article) + '</script>')

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = OKXAnnouncementProvider(client=client, now_fn=lambda: NOW)
        rows = await provider.fetch_headlines()
        assert len(rows) == 1 and rows[0].url == "https://www.okx.com/help/maintenance"
        assert set(provider.last_source_errors) == {"api:announcements-delistings", *provider.latest_urls}
        mode = "recovered"
        assert len(await provider.fetch_headlines()) == 1
        assert provider.last_source_errors == {}


def test_gate_default_fetch_budget():
    assert module.GATE_FETCH_BUDGET_SECONDS == 17.0
