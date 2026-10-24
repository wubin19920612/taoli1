import json
from datetime import UTC, datetime

import httpx
import pytest

from app.services.announcements import OKXAnnouncementProvider

STAMP = 1790661600000


def page(rows):
    state = {"appContext": {"initialProps": {"sectionData": {"articleList": {"list": rows}}}}}
    return '<script data-id="__app_data_for_ssr__" type="application/json" id="appState">' + json.dumps(state) + '</script>'


def article(slug="maintenance", **extra):
    return {"id": "internal-id", "slug": slug, "title": "Scheduled maintenance", "publishTime": STAMP,
            "sectionSlug": "announcements-others", **extra}


def test_current_ssr_uses_slug_real_time_and_category():
    provider = OKXAnnouncementProvider()
    rows = provider._parse_latest_page(page([article(), article(), article("../unsafe"),
        article("missing-time", publishTime=None)]), "latest")
    assert len(rows) == 1
    row = rows[0]
    assert row.announcement_id == "maintenance"
    assert row.url == "https://www.okx.com/zh-hans/help/maintenance"
    assert row.published_at == datetime.fromtimestamp(STAMP / 1000, UTC)
    assert row.category == "announcements-others"
    assert row.kind.value == "other"


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["announcements-new-listings", "announcements-delistings", "all-api"])
async def test_failed_api_does_not_block_other_sources(failed):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "/help/" in request.url.path:
            return httpx.Response(200, text=page([article("web-only")]))
        category = request.url.params.get("annType", "")
        if failed == "all-api" or category == failed:
            return httpx.Response(503)
        return httpx.Response(200, json={"code": "0", "data": [{"details": [
            {"title": "Maintenance", "url": "/help/api-only", "pTime": STAMP}]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await OKXAnnouncementProvider(client=client).fetch_headlines()
    assert len(calls) == 5
    assert "web-only" in {x.announcement_id for x in rows}
    assert len(rows) == (1 if failed == "all-api" else 2)


@pytest.mark.asyncio
async def test_api_preferred_and_language_variants_deduplicated():
    def handler(request):
        if "/help/" in request.url.path:
            return httpx.Response(200, text=page([article()]))
        return httpx.Response(200, json={"data": [{"details": [{"title": "API title",
            "url": "https://www.okx.com/help/maintenance", "pTime": STAMP + 13056}]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await OKXAnnouncementProvider(client=client).fetch_headlines()
    assert len(rows) == 1
    assert rows[0].title == "API title"
    assert rows[0].published_at == datetime.fromtimestamp((STAMP + 13056) / 1000, UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"code": "500", "data": []}, {"data": []}])
async def test_all_unusable_sources_raise_instead_of_silent_success(body):
    def handler(request):
        if "/help/" in request.url.path:
            return httpx.Response(200, text="<html>Access denied</html>")
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="All OKX"):
            await OKXAnnouncementProvider(client=client).fetch_headlines()
