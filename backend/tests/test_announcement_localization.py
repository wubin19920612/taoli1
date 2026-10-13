from datetime import UTC, datetime

import httpx
import pytest

from app.models.announcement import AnnouncementKind, ExchangeAnnouncement
from app.services.announcement_localization import AnnouncementLocalizationService


def announcement(exchange: str, announcement_id: str, title: str = "New listing: TEST") -> ExchangeAnnouncement:
    return ExchangeAnnouncement(
        exchange=exchange,
        announcement_id=announcement_id,
        kind=AnnouncementKind.LISTING,
        title=title,
        url=f"https://example.test/{announcement_id}",
        source="test-source",
        symbols=["TEST"],
        published_at=datetime(2026, 10, 13, tzinfo=UTC),
        fetched_at=datetime(2026, 10, 13, tzinfo=UTC),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exchange", "payload", "expected_url"),
    [
        (
            "binance",
            {"data": {"catalogs": [{"articles": [{"code": "bn-1", "title": "币安上线 TEST"}]}]}},
            "https://www.binance.com/zh-CN/support/announcement/bn-1",
        ),
        (
            "bybit",
            {"result": {"list": [{"id": "bb-1", "title": "新币上架 TEST", "url": "https://announcements.bybit.com/zh-TW/article/bb-1/"}]}},
            "https://announcements.bybit.com/zh-TW/article/bb-1/",
        ),
        (
            "bitget",
            {"data": [{"annId": "bg-1", "annTitle": "Bitget 上线 TEST", "annUrl": "https://www.bitget.com/zh-CN/support/articles/bg-1"}]},
            "https://www.bitget.com/zh-CN/support/articles/bg-1",
        ),
        (
            "okx",
            {"data": [{"details": [{"title": "欧易上线 TEST", "url": "https://www.okx.com/zh-hans/help/okx-test", "pTime": "1"}]}]},
            "https://www.okx.com/zh-hans/help/okx-test",
        ),
    ],
)
async def test_localization_uses_official_chinese_title_and_url(exchange, payload, expected_url):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        service = AnnouncementLocalizationService(exchange, client)
        service._requests = lambda: [("https://official.test/feed", {})]
        rows = await service.enrich([announcement(exchange, {"binance": "bn-1", "bybit": "bb-1", "bitget": "bg-1", "okx": "okx-test"}[exchange])])

    assert rows[0].title_zh
    assert rows[0].url_zh == expected_url


@pytest.mark.asyncio
async def test_localization_keeps_original_when_publisher_has_no_chinese_translation():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"result": {"list": [{"title": "New listing: TEST", "url": "https://announcements.bybit.com/en-US/article/test"}]}},
            )
        )
    ) as client:
        service = AnnouncementLocalizationService("bybit", client)
        service._requests = lambda: [("https://official.test/feed", {})]
        row = announcement("bybit", "test")
        localized = await service.enrich([row])

    assert localized[0].title_zh is None
    assert localized[0].url_zh is None


@pytest.mark.asyncio
async def test_new_announcement_refreshes_unexpired_chinese_feed_before_alert():
    """A newly published article must not fall back using a prepublication cache."""
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        ids = ["old"] if calls == 1 else ["old", "12560603897191"]
        return httpx.Response(200, json={"data": [
            {"annId": key, "annTitle": "Bitget 关于暂停 FET 网络提现服务的公告",
             "annUrl": f"https://www.bitget.com/zh-CN/support/articles/{key}"}
            for key in ids
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("bitget", client)
        service._requests = lambda: [("https://official.test/feed", {})]
        old = announcement("bitget", "old")
        await service.enrich([old])
        new = announcement("bitget", "12560603897191")
        rows = await service.enrich([new, old])
        await service.enrich([new, old])

    assert rows[0].title_zh == "Bitget 关于暂停 FET 网络提现服务的公告"
    assert rows[0].url_zh == "https://www.bitget.com/zh-CN/support/articles/12560603897191"
    assert calls == 2


@pytest.mark.asyncio
async def test_missing_translation_does_not_refresh_on_every_poll():
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": {"catalogs": [{"articles": [
            {"code": "old", "title": "币安公告"},
        ]}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("binance", client)
        service._requests = lambda: [("https://official.test/feed", {})]
        await service.enrich([announcement("binance", "old")])
        missing = announcement("binance", "english-only")
        for _ in range(3):
            rows = await service.enrich([missing])

    assert calls == 2
    assert rows[0].title_zh is None
    assert rows[0].title == missing.title


@pytest.mark.asyncio
async def test_new_article_refresh_failure_keeps_original():
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise httpx.ReadTimeout("Chinese feed unavailable", request=request)
        return httpx.Response(200, json={"data": {"catalogs": [{"articles": [
            {"code": "old", "title": "币安公告"},
        ]}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = AnnouncementLocalizationService("binance", client)
        service._requests = lambda: [("https://official.test/feed", {})]
        await service.enrich([announcement("binance", "old")])
        new = announcement("binance", "new")
        rows = await service.enrich([new])

    assert calls == 2
    assert rows[0].title_zh is None
    assert rows[0].url == new.url
