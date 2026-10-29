"""Attach publisher-provided Chinese titles without changing announcement identities."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from html import unescape
from urllib.parse import urlsplit

import httpx

from app.models.announcement import ExchangeAnnouncement

logger = logging.getLogger(__name__)

LOCALIZATION_SOURCE_TIMEOUT_SECONDS = 2.5


class AnnouncementLocalizationService:
    """Best-effort official localization, cached separately from the news feed.

    Matching uses the publisher's article ID or full article slug, never ticker
    names. Original titles, publication times and trading metadata remain intact.
    Gate's existing third-party reader is used only to read its Chinese website.
    """

    def __init__(self, exchange: str, client: httpx.AsyncClient) -> None:
        self.exchange = exchange
        self.client = client
        self._cache: dict[str, tuple[str, str]] = {}
        self._missing_until: dict[str, float] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._source_errors: dict[str, type[Exception]] = {}

    def _requests(self) -> list[tuple[str, dict[str, str]]]:
        if self.exchange == "binance":
            return [(
                (
                    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
                    "?type=1&pageNo=1&pageSize=20"
                ),
                {"lang": "zh-CN"},
            )]
        if self.exchange == "bybit":
            return [(
                "https://api.bybit.com/v5/announcements/index?locale=zh-TW&limit=50"
                + (f"&type={kind}" if kind else ""),
                {},
            ) for kind in ("new_crypto", "delistings", "")]
        if self.exchange == "bitget":
            return [(
                "https://api.bitget.com/api/v2/public/annoucements?language=zh_CN&limit=10"
                + (f"&annType={kind}" if kind else ""),
                {},
            ) for kind in (
                "coin_listings", "symbol_delisting", "latest_news",
                "product_updates", "maintenance_system_updates", "",
            )]
        if self.exchange == "okx":
            return [(
                "https://www.okx.com/api/v5/support/announcements?page=1"
                + (f"&annType={kind}" if kind else ""),
                {"Accept-Language": "zh-CN"},
            ) for kind in ("announcements-new-listings", "announcements-delistings", "")] + [(
                "https://www.okx.com/zh-hans/help/section/announcements-latest-announcements",
                {},
            )]
        # GateAnnouncementProvider already falls back to Gate's Chinese reader
        # when the official page is challenged, so do not duplicate that slow
        # reader request here.
        return []

    async def enrich(self, rows: list[ExchangeAnnouncement]) -> list[ExchangeAnnouncement]:
        if not rows or not self._requests():
            return rows
        async with self._lock:
            now = time.monotonic()
            unresolved = {
                row.announcement_id
                for row in rows
                if row.announcement_id not in self._cache
                and self._missing_until.get(row.announcement_id, 0.0) <= now
            }
            if now >= self._expires_at or unresolved:
                await self._refresh()
                now = time.monotonic()
                for announcement_id in unresolved:
                    if announcement_id not in self._cache:
                        # A publisher may add a translation after publication;
                        # retry after the short cache window, not every poll.
                        self._missing_until[announcement_id] = now + 120
        enriched: list[ExchangeAnnouncement] = []
        for row in rows:
            localized = self._cache.get(row.announcement_id)
            # Gate's reader already supplies Chinese titles on its normal feed.
            if localized is None and row.exchange == "gate":
                localized = self._localized_row({
                    "id": row.announcement_id, "title": row.title, "url": row.url,
                })[1]
            if localized is not None:
                row = row.model_copy(update={"title_zh": localized[0], "url_zh": localized[1]})
            enriched.append(row)
        return enriched

    async def _refresh(self) -> None:
        results = await asyncio.gather(*(
            self._fetch(url, headers) for url, headers in self._requests()
        ))
        refreshed = {
            key: value for result in results for key, value in result.items()
        }
        self._cache.update(refreshed)
        # Retry empty/failed feeds sooner; do not poll every news cycle.
        self._expires_at = time.monotonic() + (120 if refreshed else 30)
        for announcement_id in self._cache:
            self._missing_until.pop(announcement_id, None)

    async def _fetch(self, url: str, headers: dict[str, str]) -> dict[str, tuple[str, str]]:
        timeout_seconds = LOCALIZATION_SOURCE_TIMEOUT_SECONDS
        try:
            # Localization cannot hold up a new announcement indefinitely.
            async with asyncio.timeout(timeout_seconds):
                response = await self.client.get(
                    url, headers=headers,
                    timeout=httpx.Timeout(timeout_seconds, connect=min(3, timeout_seconds)),
                )
                response.raise_for_status()
                rows = self._rows(response, url)
                result: dict[str, tuple[str, str]] = {}
                for row in rows:
                    key, value = self._localized_row(row)
                    if key and value is not None:
                        result[key] = value
                if self._source_errors.pop(url, None) is not None:
                    logger.info("Chinese announcement source recovered: %s", url)
                return result
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError,
                AttributeError, IndexError) as exc:
            error_type = type(exc)
            log = logger.debug if self._source_errors.get(url) is error_type else logger.warning
            self._source_errors[url] = error_type
            log("Chinese announcement source unavailable: %s error=%s", url, error_type.__name__)
            return {}

    def _rows(self, response: httpx.Response, url: str) -> list[dict]:
        if "/help/" in url or self.exchange == "gate":
            script_id = "appState" if self.exchange == "okx" else "__NEXT_DATA__"
            match = re.search(
                rf'<script[^>]+id=["\']{script_id}["\'][^>]*>(.*?)</script>',
                response.text, re.DOTALL | re.IGNORECASE,
            )
            if match is None:
                return []
            payload = json.loads(unescape(match[1]))
            if self.exchange == "okx":
                rows = payload["appContext"]["initialProps"]["sectionData"]["articleList"]["list"]
            else:
                rows = payload["props"]["pageProps"]["listData"]["list"]
        else:
            payload = response.json()
            if self.exchange == "binance":
                rows = [
                    row for catalog in payload.get("data", {}).get("catalogs", [])
                    for row in catalog.get("articles", [])
                ]
            elif self.exchange == "bybit":
                rows = (payload.get("result") or {}).get("list", [])
            elif self.exchange == "bitget":
                rows = payload.get("data", [])
            else:
                rows = [row for group in payload.get("data", []) for row in group.get("details", [])]
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _localized_row(self, row: dict) -> tuple[str, tuple[str, str] | None]:
        title = str(row.get("annTitle") or row.get("title") or "").strip()
        # Some publishers return English for unsupported/missing translations.
        if not re.search(r"[\u3400-\u9fff]", title):
            return "", None
        raw_url = str(row.get("annUrl") or row.get("url") or "").strip()
        if self.exchange == "binance":
            key = str(row.get("code") or "").strip()
            url = f"https://www.binance.com/zh-CN/support/announcement/{key}"
            return (key, (title, url)) if re.fullmatch(r"[A-Za-z0-9_-]+", key) else ("", None)
        if self.exchange == "gate":
            key = str(row.get("id") or "").strip()
            url = f"https://www.gate.com/zh/announcements/article/{key}"
            return (key, (title, url)) if re.fullmatch(r"[A-Za-z0-9_-]+", key) else ("", None)
        if self.exchange == "okx":
            slug = str(row.get("slug") or "").strip()
            if slug and re.fullmatch(r"[A-Za-z0-9_-]+", slug):
                raw_url = f"https://www.okx.com/zh-hans/help/{slug}"
            elif raw_url.startswith("/"):
                raw_url = "https://www.okx.com" + raw_url
        parsed = urlsplit(raw_url)
        host, path = parsed.hostname, parsed.path
        official = {
            "bybit": ("announcements.bybit.com", r"/zh-TW/article/"),
            "bitget": ("www.bitget.com", r"/zh[-_]CN/support/articles/"),
            "okx": ("www.okx.com", r"/zh-hans(?:-[a-z]+)?/help/"),
        }.get(self.exchange)
        if not official or parsed.scheme != "https" or host != official[0] or not re.match(official[1], path):
            return "", None
        key = str(row.get("annId") or row.get("id") or "").strip()
        if self.exchange == "okx":
            key = str(row.get("annId") or "").strip() or path.rstrip("/").rsplit("/", 1)[-1]
        elif not key:
            key = path.rstrip("/").rsplit("/", 1)[-1]
        return key, (title, raw_url)
