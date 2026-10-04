from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.db.database import connect_database
from app.db.repositories import AnnouncementRepository
from app.db.schema import initialize_schema
from app.models.announcement import AnnouncementKind, AnnouncementSettings, ExchangeAnnouncement
from app.services.announcements import (
    AnnouncementMonitor,
    GateAnnouncementProvider,
    HyperliquidAnnouncementProvider,
    HyperliquidOfficialAnnouncementProvider,
)

NOW = datetime(2026, 9, 29, 8, tzinfo=UTC)


def telegram_post(post_id: str, text: str, date: str = "2026-09-29T07:59:00+00:00") -> str:
    return (
        f'<div class="tgme_widget_message" data-post="hyperliquid_announcements/{post_id}">'
        f'<div class="tgme_widget_message_text js-message_text">{text}</div>'
        f'<a class="tgme_widget_message_date"><time datetime="{date}">time</time></a></div>'
    )


def gate_page() -> str:
    return '''<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{
      "listData":{"list":[{"id":102003,"title":"Gate 关于 SAMSUNG 永续合约分红派息结算的公告",
      "release_timestamp":"1790650416","url":"/announcements/article/102003"}]}}}}</script>'''


def test_official_channel_preserves_post_identity_time_and_multiline_body() -> None:
    provider = HyperliquidOfficialAnnouncementProvider(now_fn=lambda: NOW)
    html = telegram_post("593", '<b>Weekly Update</b><br>HIP-3 market <a href="https://example.com">xyz:ABC</a> &amp; maintenance')
    rows = provider._parse_page(html)
    assert len(rows) == 1
    row = rows[0]
    assert row.announcement_id == "593"
    assert row.url == "https://t.me/hyperliquid_announcements/593"
    assert row.title == "Weekly Update"
    assert row.published_at == NOW - timedelta(minutes=1)
    assert row.kind == AnnouncementKind.OTHER
    assert row.symbols == []  # News must not silently resolve HIP-3 names to native markets.
    assert "xyz:ABC & maintenance" in row.summary


def test_official_channel_rejects_challenges_and_invalid_posts() -> None:
    provider = HyperliquidOfficialAnnouncementProvider(now_fn=lambda: NOW)
    for html in ("Access Denied", telegram_post("593", "hello", "invalid"),
                 telegram_post("593", "hello").replace("hyperliquid_announcements/", "untrusted/")):
        with pytest.raises(ValueError):
            provider._parse_page(html)


def test_official_media_post_keeps_date_and_source_link() -> None:
    provider = HyperliquidOfficialAnnouncementProvider(now_fn=lambda: NOW)
    html = telegram_post("594", "").replace(
        '<a class="tgme_widget_message_date">',
        '<a class="tgme_widget_message_photo_wrap"></a><a class="tgme_widget_message_date">',
    )
    row = provider._parse_page(html)[0]
    assert row.announcement_id == "594"
    assert "媒体公告" in row.title
    assert row.published_at == NOW - timedelta(minutes=1)


@pytest.mark.asyncio
async def test_official_channel_initial_history_muted_then_new_posts_alert_once() -> None:
    db = await connect_database(":memory:")
    provider = HyperliquidOfficialAnnouncementProvider(now_fn=lambda: NOW)
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        sent: list[str] = []
        monitor = AnnouncementMonitor(repo, alert_sender=sent.append, now_fn=lambda: NOW)
        settings = AnnouncementSettings(other_alerts_enabled=True)
        first = provider._parse_page(telegram_post("592", "Maintenance notice", "2026-09-28T07:00:00+00:00"))
        assert (await monitor.process(first, settings))[0].alert_status == "muted"
        fresh = provider._parse_page(telegram_post("593", "Weekly Update<br>New system features"))
        assert (await monitor.process(fresh, settings))[0].alert_status == "sent"
        assert await AnnouncementMonitor(repo, alert_sender=sent.append, now_fn=lambda: NOW).process(fresh, settings) == []
        assert len(sent) == 1
    finally:
        await provider.aclose()
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(567, "blocked"), (200, "<script>challenge()</script>")])
async def test_gate_recovers_dividend_from_reader_without_changing_identity(status: int, body: str) -> None:
    reader_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "r.jina.ai":
            reader_requests.append(str(request.url))
            assert request.headers["X-Return-Format"] == "html"
            if str(request.url).endswith("dividenddistribution"):
                return httpx.Response(200, text=gate_page())
            return httpx.Response(429)
        return httpx.Response(status, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await GateAnnouncementProvider(client=client, now_fn=lambda: NOW).fetch_headlines()
    assert len(reader_requests) == 2
    assert len(rows) == 1
    assert rows[0].announcement_id == "102003"
    assert rows[0].source == "gate-next-announcements"
    assert rows[0].kind == AnnouncementKind.OTHER
    assert rows[0].published_at == datetime(2026, 9, 29, 2, 53, 36, tzinfo=UTC)


@pytest.mark.asyncio
async def test_gate_does_not_use_reader_when_general_page_works() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host != "r.jina.ai"
        if request.url.path == "/announcements":
            return httpx.Response(200, text=gate_page())
        return httpx.Response(567)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert len(await GateAnnouncementProvider(client=client).fetch_headlines()) == 1


@pytest.mark.asyncio
async def test_hyper_meta_baseline_is_source_scoped_and_relisting_is_detected() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        await repo.set_provider_state("hyperliquid:meta-universe", {"symbols": {"BTC": False, "OLD": True}})
        for source, stamp in (("hyperliquid-meta-universe", NOW - timedelta(days=1)),
                              ("hyperliquid-official-announcements", NOW)):
            await repo.create_if_new(ExchangeAnnouncement(
                exchange="hyperliquid", announcement_id="baseline", source=source,
                title="Baseline", url="https://hyperliquid.xyz", kind=AnnouncementKind.OTHER,
                published_at=stamp, fetched_at=stamp,
            ))
        provider = HyperliquidAnnouncementProvider(repository=repo, now_fn=lambda: NOW)
        try:
            rows = await provider._parse_payload({"universe": [{"name": "BTC"}, {"name": "OLD"}]})
            assert [(x.kind, x.symbols) for x in rows] == [(AnnouncementKind.LISTING, ["OLD"])]
            assert rows[0].category == "meta-universe"
        finally:
            await provider.aclose()
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("universe", [[], [{"name": "BTC"}, {}], [{"name": "BTC", "isDelisted": "false"}]])
async def test_hyper_invalid_snapshot_does_not_delist_markets_or_replace_state(universe: list) -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        original = {"symbols": {"BTC": False, "ETH": False}}
        await repo.set_provider_state("hyperliquid:meta-universe", original)
        provider = HyperliquidAnnouncementProvider(repository=repo)
        try:
            with pytest.raises((TypeError, ValueError)):
                await provider._parse_payload({"universe": universe})
            assert await repo.get_provider_state("hyperliquid:meta-universe") == original
        finally:
            await provider.aclose()
    finally:
        await db.close()
