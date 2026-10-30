import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from app.models.announcement import ExchangeAnnouncement
from app.services.announcements import GateAnnouncementProvider


NOW = datetime(2026, 10, 28, 8, tzinfo=UTC)
CATEGORIES = ("newspotlistings", "newfutureslistings", "newconvertlistings", "delisted", "")
CATEGORY_FAILURE = "failed to fetch gate announcement category: %s"
LOGGER_NAME = "app.services.announcements"


def gate_page(article_id: int, title: str) -> str:
    payload = {"props": {"pageProps": {"listData": {"list": [{
        "id": article_id,
        "title": title,
        "url": f"/announcements/article/{article_id}",
        "release_timestamp": "1793174400",
    }]}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class GateFeed:
    def __init__(self) -> None:
        self.outcomes: dict[str, int | type[httpx.RequestError]] = dict.fromkeys(CATEGORIES, 567)
        self.direct_requests: list[str] = []
        self.reader_requests: list[str] = []
        self.pages = {
            "newspotlistings": gate_page(60001, "Gate to List SPOT (SPOT) for Spot Trading"),
            "newfutureslistings": gate_page(60002, "Gate to List FUT (FUT) for Futures Trading"),
            "newconvertlistings": gate_page(60003, "Gate to List CONV (CONV) for Convert Trading"),
            "delisted": gate_page(60004, "Gate Will Delist OLD (OLD) from Spot Trading"),
            "": gate_page(60005, "Gate maintenance notice"),
        }
        self.reader_pages = {
            "": gate_page(61001, "Gate general announcement"),
            "dividenddistribution": gate_page(61002, "Gate dividend distribution notice"),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        category = request.url.path.rsplit("/announcements", 1)[1].strip("/")
        if request.url.host == "r.jina.ai":
            self.reader_requests.append(category)
            assert request.headers["X-Return-Format"] == "html"
            assert request.headers["X-No-Cache"] == "true"
            return httpx.Response(200, text=self.reader_pages[category])
        assert request.url.host == "apim.gateapi.io"
        self.direct_requests.append(category)
        outcome = self.outcomes[category]
        if not isinstance(outcome, int):
            raise outcome("Gate connection timed out", request=request)
        return httpx.Response(outcome, text=self.pages[category] if outcome == 200 else "blocked")


def category_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record for record in caplog.records
        if record.name == LOGGER_NAME and record.msg == CATEGORY_FAILURE
    ]


def row_payloads(rows: list[ExchangeAnnouncement]) -> list[dict[str, object]]:
    # Storage IDs are generated on each parse; compare every business field.
    return [row.model_dump(exclude={"id"}) for row in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("headlines", [False, True])
async def test_all_categories_fail_then_back_off_and_keep_reader_results(
    caplog: pytest.LogCaptureFixture, headlines: bool,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    feed, clock = GateFeed(), Clock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(feed)) as client:
        provider = GateAnnouncementProvider(client=client, now_fn=lambda: NOW, monotonic_fn=clock)
        fetch = provider.fetch_headlines if headlines else provider.fetch
        expected = [
            row for category, page in feed.reader_pages.items()
            for row in provider._parse_page(page, category)
        ]
        assert [row.announcement_id for row in expected] == ["61001", "61002"]
        assert row_payloads(await fetch()) == row_payloads(expected)
        assert feed.direct_requests == list(CATEGORIES)
        assert feed.reader_requests == ["", "dividenddistribution"]
        first_logs = category_logs(caplog)
        assert [record.args for record in first_logs] == [(category,) for category in CATEGORIES]
        assert all(record.levelno == logging.WARNING and record.exc_info for record in first_logs)
        assert all(record.exc_info[1].response.status_code == 567 for record in first_logs)

        # The next two polls skip all direct requests, including just before expiry.
        for now in (1030.0, 1299.999):
            caplog.clear()
            feed.direct_requests.clear()
            feed.reader_requests.clear()
            clock.now = now
            assert row_payloads(await fetch()) == row_payloads(expected)
            assert feed.direct_requests == []
            assert feed.reader_requests == ["", "dividenddistribution"]
            records = [record for record in caplog.records if record.name == LOGGER_NAME]
            assert len(records) == 1
            assert records[0].levelno == logging.DEBUG
            assert records[0].exc_info is None

        # At exactly 300 seconds, retry all five and renew the backoff quietly.
        caplog.clear()
        feed.direct_requests.clear()
        feed.reader_requests.clear()
        clock.now = 1300.0
        assert row_payloads(await fetch()) == row_payloads(expected)
        assert feed.direct_requests == list(CATEGORIES)
        assert feed.reader_requests == ["", "dividenddistribution"]
        repeated = category_logs(caplog)
        assert len(repeated) == 5
        assert all(record.levelno == logging.DEBUG and record.exc_info is None for record in repeated)
        feed.direct_requests.clear()
        clock.now = 1330.0
        assert row_payloads(await fetch()) == row_payloads(expected)
        assert feed.direct_requests == []


@pytest.mark.asyncio
async def test_one_category_recovers_at_expiry_and_clears_backoff_and_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    feed, clock = GateFeed(), Clock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(feed)) as client:
        provider = GateAnnouncementProvider(client=client, now_fn=lambda: NOW, monotonic_fn=clock)
        await provider.fetch_headlines()
        caplog.clear()
        feed.direct_requests.clear()
        clock.now = 1300.0
        feed.outcomes["newspotlistings"] = 200
        rows = await provider.fetch_headlines()
        assert feed.direct_requests == list(CATEGORIES)
        assert [row.announcement_id for row in rows] == ["60001", "61001", "61002"]
        recovered = [record for record in caplog.records if "recovered" in record.getMessage()]
        assert len(recovered) == 1
        assert recovered[0].levelno == logging.INFO
        assert recovered[0].getMessage() == "gate announcement category recovered: newspotlistings"
        assert provider._direct_retry_at is None
        assert "newspotlistings" not in provider._category_failures
        assert not any(record.levelno >= logging.WARNING for record in category_logs(caplog))

        caplog.clear()
        feed.direct_requests.clear()
        clock.now = 1330.0
        assert row_payloads(await provider.fetch_headlines()) == row_payloads(rows)
        assert feed.direct_requests == list(CATEGORIES)
        assert not any("recovered" in record.getMessage() for record in caplog.records)

        # A new failure after recovery must warn even with the original signature.
        caplog.clear()
        feed.outcomes["newspotlistings"] = 567
        await provider.fetch_headlines()
        warnings = [record for record in category_logs(caplog) if record.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert warnings[0].args == ("newspotlistings",)
        assert warnings[0].exc_info


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_failure", [httpx.ConnectTimeout, 503])
async def test_failure_signature_changes_warn_again(
    caplog: pytest.LogCaptureFixture, changed_failure: int | type[httpx.RequestError],
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    feed, clock = GateFeed(), Clock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(feed)) as client:
        provider = GateAnnouncementProvider(client=client, now_fn=lambda: NOW, monotonic_fn=clock)
        await provider.fetch_headlines()
        caplog.clear()
        clock.now = 1300.0
        feed.outcomes["delisted"] = changed_failure
        await provider.fetch_headlines()
        warnings = [record for record in category_logs(caplog) if record.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert warnings[0].args == ("delisted",)
        assert warnings[0].exc_info
        if changed_failure == 503:
            assert warnings[0].exc_info[1].response.status_code == 503
        else:
            assert isinstance(warnings[0].exc_info[1], httpx.ConnectTimeout)

        caplog.clear()
        clock.now = 1600.0
        await provider.fetch_headlines()
        assert len(category_logs(caplog)) == 5
        assert all(
            record.levelno == logging.DEBUG and record.exc_info is None
            for record in category_logs(caplog)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_category", ["delisted", ""])
async def test_partial_failure_preserves_results_and_request_order_without_backoff(
    caplog: pytest.LogCaptureFixture, failed_category: str,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    feed = GateFeed()
    feed.outcomes = dict.fromkeys(CATEGORIES, 200)
    feed.outcomes[failed_category] = 567
    async with httpx.AsyncClient(transport=httpx.MockTransport(feed)) as client:
        provider = GateAnnouncementProvider(client=client, now_fn=lambda: NOW)
        expected = [
            row for category in CATEGORIES if category != failed_category
            for row in provider._parse_page(feed.pages[category], category)
        ]
        if not failed_category:
            expected.extend(
                row for category, page in feed.reader_pages.items()
                for row in provider._parse_page(page, category)
            )
        assert len(expected) == (6 if not failed_category else 4)
        for poll in range(3):
            caplog.clear()
            feed.direct_requests.clear()
            feed.reader_requests.clear()
            assert row_payloads(await provider.fetch_headlines()) == row_payloads(expected)
            assert feed.direct_requests == list(CATEGORIES)
            assert feed.reader_requests == (["", "dividenddistribution"] if not failed_category else [])
            records = category_logs(caplog)
            assert len(records) == 1
            assert records[0].args == (failed_category,)
            assert records[0].levelno == (logging.WARNING if poll == 0 else logging.DEBUG)
            assert bool(records[0].exc_info) == (poll == 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("success_at", [1000.0, 1001.0])
async def test_concurrent_failure_does_not_overwrite_direct_recovery(success_at: float) -> None:
    feed, clock = GateFeed(), Clock()
    a_category_waiting = asyncio.Event()
    release_a = asyncio.Event()
    b_detail_waiting = asyncio.Event()
    release_b_detail = asyncio.Event()
    first_category_requests = 0

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal first_category_requests
        if request.url.host == "www.gate.com":
            assert request.url.path == "/announcements/article/60001"
            b_detail_waiting.set()
            await release_b_detail.wait()
            return httpx.Response(200, text="<article>Gate listing details</article>")
        response = feed(request)
        if request.url.host == "apim.gateapi.io" and request.url.path.endswith("/newspotlistings"):
            first_category_requests += 1
            if first_category_requests == 1:
                a_category_waiting.set()
                await release_a.wait()
            elif first_category_requests == 2:
                return httpx.Response(200, text=feed.pages["newspotlistings"])
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = GateAnnouncementProvider(client=client, now_fn=lambda: NOW, monotonic_fn=clock)
        tasks = [asyncio.create_task(provider.fetch_headlines())]
        try:
            await asyncio.wait_for(a_category_waiting.wait(), timeout=5)
            # Also cover success at the same clock tick as A's start.
            clock.now = success_at
            tasks.append(asyncio.create_task(provider.fetch()))
            await asyncio.wait_for(b_detail_waiting.wait(), timeout=5)
            clock.now = 1002.0
            release_a.set()
            rows_a = await asyncio.wait_for(tasks[0], timeout=5)
            assert [row.announcement_id for row in rows_a] == ["61001", "61002"]

            clock.now = 1003.0
            release_b_detail.set()
            rows_b = await asyncio.wait_for(tasks[1], timeout=5)
            assert [row.announcement_id for row in rows_b] == ["60001", "61001", "61002"]
            assert feed.direct_requests == [CATEGORIES[0], *CATEGORIES, *CATEGORIES[1:]]

            feed.direct_requests.clear()
            clock.now = 1004.0
            await provider.fetch_headlines()
            assert feed.direct_requests == list(CATEGORIES)

            # A later all-failed poll must still back off despite the older success.
            feed.direct_requests.clear()
            clock.now = 1005.0
            await provider.fetch_headlines()
            assert feed.direct_requests == []
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_all_failed_fetches_still_back_off() -> None:
    feed, clock = GateFeed(), Clock()
    a_category_waiting = asyncio.Event()
    b_category_waiting = asyncio.Event()
    release_a = asyncio.Event()
    release_b = asyncio.Event()
    first_category_requests = 0

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal first_category_requests
        response = feed(request)
        if request.url.host == "apim.gateapi.io" and request.url.path.endswith("/newspotlistings"):
            first_category_requests += 1
            if first_category_requests == 1:
                a_category_waiting.set()
                await release_a.wait()
            elif first_category_requests == 2:
                b_category_waiting.set()
                await release_b.wait()
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = GateAnnouncementProvider(client=client, now_fn=lambda: NOW, monotonic_fn=clock)
        tasks = [asyncio.create_task(provider.fetch_headlines())]
        try:
            await asyncio.wait_for(a_category_waiting.wait(), timeout=5)
            clock.now = 1001.0
            tasks.append(asyncio.create_task(provider.fetch()))
            await asyncio.wait_for(b_category_waiting.wait(), timeout=5)
            clock.now = 1002.0
            release_a.set()
            await asyncio.wait_for(tasks[0], timeout=5)
            clock.now = 1003.0
            release_b.set()
            await asyncio.wait_for(tasks[1], timeout=5)
            assert feed.direct_requests == [CATEGORIES[0], *CATEGORIES, *CATEGORIES[1:]]

            feed.direct_requests.clear()
            feed.reader_requests.clear()
            clock.now = 1030.0
            rows = await provider.fetch_headlines()
            assert feed.direct_requests == []
            assert feed.reader_requests == ["", "dividenddistribution"]
            assert [row.announcement_id for row in rows] == ["61001", "61002"]
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
