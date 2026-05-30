from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from html import unescape
from inspect import isawaitable
from urllib.parse import quote_plus

import httpx

from app.db.repositories import AnnouncementRepository, SettingsRepository
from app.models.announcement import AnnouncementKind, AnnouncementSettings, ExchangeAnnouncement

AlertSender = Callable[[str], None | Awaitable[None]]
SettingsLoader = Callable[[], Awaitable[AnnouncementSettings]]
logger = logging.getLogger(__name__)

ANNOUNCEMENT_EXCHANGES = ("binance", "okx", "bybit", "gate", "bitget", "hyperliquid")
ANNOUNCEMENT_EXCHANGE_OPTIONS = [
    {"label": "Binance", "value": "binance"},
    {"label": "OKX", "value": "okx"},
    {"label": "Bybit", "value": "bybit"},
    {"label": "Gate", "value": "gate"},
    {"label": "Bitget", "value": "bitget"},
    {"label": "Hyperliquid", "value": "hyperliquid"},
]

LISTING_KEYWORDS = (
    "list ",
    "lists ",
    "listed ",
    "listing",
    "launch",
    "launches",
    "adds ",
    "add ",
    "will support",
    "support new",
    "new listing",
    "initial listing",
    "will launch",
)
DELISTING_KEYWORDS = (
    "delist",
    "delisting",
    "remove ",
    "removal",
    "suspend trading",
    "cease trading",
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_datetime_ms(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _parse_datetime_seconds(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _contains_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    normalized_text = re.sub(r"\s+", " ", text.strip().lower())
    normalized = f" {normalized_text} "
    return any(keyword in normalized for keyword in keywords)


def classify_announcement(title: str, category: str | None = None) -> AnnouncementKind:
    category_value = (category or "").lower()
    title_value = title.lower()
    if "delist" in category_value or _contains_keyword(title_value, DELISTING_KEYWORDS):
        return AnnouncementKind.DELISTING
    if (
        "listing" in category_value
        or "new_crypto" in category_value
        or "coin_listings" in category_value
        or "newcrytolistings" in category_value
        or "newspotlistings" in category_value
        or "newfutureslistings" in category_value
        or "newconvertlistings" in category_value
        or _contains_keyword(title_value, LISTING_KEYWORDS)
    ):
        return AnnouncementKind.LISTING
    return AnnouncementKind.OTHER


def _article_url(base_url: str, url: str, fallback_id: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{base_url.rstrip('/')}{url}"
    if url:
        return f"{base_url.rstrip('/')}/{url}"
    return f"{base_url.rstrip('/')}/{fallback_id}"


def _display_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return (
        value.astimezone(UTC)
        .replace(tzinfo=None)
        .replace(microsecond=0)
        + timedelta(hours=8)
    ).strftime("%Y-%m-%d %H:%M:%S")


def build_announcement_alert_message(announcement: ExchangeAnnouncement) -> str:
    kind_label = {
        AnnouncementKind.LISTING: "上币",
        AnnouncementKind.DELISTING: "下币",
        AnnouncementKind.OTHER: "公告",
    }[announcement.kind]
    lines = [
        f"[{announcement.exchange.upper()}] {kind_label}公告",
        f"时间: {_display_time(announcement.published_at)} UTC+8",
        f"标题: {announcement.title}",
    ]
    if announcement.category:
        lines.append(f"分类: {announcement.category}")
    lines.append(f"链接: {announcement.url}")
    return "\n".join(lines)


class AnnouncementProvider:
    exchange: str
    source: str

    async def fetch(self) -> list[ExchangeAnnouncement]:
        raise NotImplementedError


class HttpAnnouncementProvider(AnnouncementProvider):
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0, read=8.0),
            follow_redirects=True,
            headers={"User-Agent": "taoli1-radar/0.1"},
        )
        self._owns_client = client is None
        self._now_fn = now_fn or utc_now

    async def _get_json(self, url: str) -> object:
        response = await self.client.get(url)
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _announcement(
        self,
        *,
        announcement_id: str,
        title: str,
        url: str,
        category: str | None,
        published_at: datetime | None,
    ) -> ExchangeAnnouncement | None:
        title = title.strip()
        url = url.strip()
        announcement_id = announcement_id.strip() or url or title
        if not title or not url or not announcement_id:
            return None
        return ExchangeAnnouncement(
            exchange=self.exchange,
            announcement_id=announcement_id,
            kind=classify_announcement(title, category),
            title=title,
            url=url,
            source=self.source,
            category=category,
            published_at=published_at or self._now_fn(),
            fetched_at=self._now_fn(),
        )


class BinanceAnnouncementProvider(HttpAnnouncementProvider):
    exchange = "binance"
    source = "binance-cms-announcements"
    base_url = "https://www.binance.com"
    api_url = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    catalogs = (
        (48, "New Cryptocurrency Listing"),
        (161, "Delisting"),
    )

    async def fetch(self) -> list[ExchangeAnnouncement]:
        announcements: list[ExchangeAnnouncement] = []
        for catalog_id, category in self.catalogs:
            query = f"type=1&catalogId={catalog_id}&pageNo=1&pageSize=20"
            payload = await self._get_json(f"{self.api_url}?{query}")
            announcements.extend(self._parse_payload(payload, str(catalog_id), category))
        return announcements

    def _parse_payload(
        self,
        payload: object,
        catalog_id: str,
        fallback_category: str,
    ) -> list[ExchangeAnnouncement]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        catalogs = data.get("catalogs") if isinstance(data, dict) else None
        if not isinstance(catalogs, list):
            return []
        rows: list[dict] = []
        category = fallback_category
        for catalog in catalogs:
            if not isinstance(catalog, dict):
                continue
            category = _clean_text(catalog.get("catalogName")) or fallback_category
            articles = catalog.get("articles")
            if isinstance(articles, list):
                rows.extend(item for item in articles if isinstance(item, dict))
        announcements: list[ExchangeAnnouncement] = []
        for row in rows:
            title = _clean_text(row.get("title"))
            code = _clean_text(row.get("code"))
            article_id = _clean_text(row.get("id"))
            announcement_id = code or article_id
            url = f"{self.base_url}/en/support/announcement/{code}" if code else ""
            announcement = self._announcement(
                announcement_id=announcement_id,
                title=title,
                url=url,
                category=f"{catalog_id}:{category}",
                published_at=_parse_datetime_ms(row.get("releaseDate") or row.get("publishDate")),
            )
            if announcement is not None:
                announcements.append(announcement)
        return announcements


class OKXAnnouncementProvider(HttpAnnouncementProvider):
    exchange = "okx"
    source = "okx-support-announcements"
    base_url = "https://www.okx.com/api/v5/support/announcements"
    ann_types = ("announcements-new-listings", "announcements-delistings")

    async def fetch(self) -> list[ExchangeAnnouncement]:
        announcements: list[ExchangeAnnouncement] = []
        for ann_type in self.ann_types:
            payload = await self._get_json(f"{self.base_url}?annType={ann_type}&page=1")
            announcements.extend(self._parse_payload(payload, ann_type))
        return announcements

    def _parse_payload(self, payload: object, fallback_category: str) -> list[ExchangeAnnouncement]:
        if not isinstance(payload, dict):
            return []
        rows: list[dict] = []
        data = payload.get("data")
        if isinstance(data, list):
            for group in data:
                if not isinstance(group, dict):
                    continue
                details = group.get("details")
                if isinstance(details, list):
                    rows.extend(item for item in details if isinstance(item, dict))
        announcements: list[ExchangeAnnouncement] = []
        for row in rows:
            title = _clean_text(row.get("title"))
            url = _clean_text(row.get("url"))
            category = _clean_text(row.get("annType")) or fallback_category
            published_at = _parse_datetime_ms(row.get("pTime") or row.get("businessPTime"))
            announcement_id = _clean_text(row.get("annId")) or url.rsplit("/", 1)[-1]
            announcement = self._announcement(
                announcement_id=announcement_id,
                title=title,
                url=url,
                category=category,
                published_at=published_at,
            )
            if announcement is not None:
                announcements.append(announcement)
        return announcements


class BybitAnnouncementProvider(HttpAnnouncementProvider):
    exchange = "bybit"
    source = "bybit-v5-announcements"
    base_url = "https://api.bybit.com/v5/announcements/index"
    announcement_types = ("new_crypto", "delistings")

    async def fetch(self) -> list[ExchangeAnnouncement]:
        announcements: list[ExchangeAnnouncement] = []
        for announcement_type in self.announcement_types:
            query = f"locale=en-US&type={quote_plus(announcement_type)}&limit=20"
            payload = await self._get_json(f"{self.base_url}?{query}")
            announcements.extend(self._parse_payload(payload, announcement_type))
        return announcements

    def _parse_payload(self, payload: object, fallback_category: str) -> list[ExchangeAnnouncement]:
        if not isinstance(payload, dict):
            return []
        result = payload.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            return []
        announcements: list[ExchangeAnnouncement] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = _clean_text(row.get("title"))
            url = _clean_text(row.get("url"))
            category = _clean_text(row.get("type") or row.get("category")) or fallback_category
            published_at = _parse_datetime_ms(row.get("publishTime") or row.get("dateTimestamp"))
            announcement_id = _clean_text(row.get("id")) or url.rstrip("/").rsplit("/", 1)[-1]
            announcement = self._announcement(
                announcement_id=announcement_id,
                title=title,
                url=url,
                category=category,
                published_at=published_at,
            )
            if announcement is not None:
                announcements.append(announcement)
        return announcements


class BitgetAnnouncementProvider(HttpAnnouncementProvider):
    exchange = "bitget"
    source = "bitget-public-annoucements"
    base_url = "https://api.bitget.com/api/v2/public/annoucements"
    ann_types = ("coin_listings", "latest_news")

    async def fetch(self) -> list[ExchangeAnnouncement]:
        announcements: list[ExchangeAnnouncement] = []
        for ann_type in self.ann_types:
            query = f"language=en_US&annType={quote_plus(ann_type)}&limit=20"
            payload = await self._get_json(f"{self.base_url}?{query}")
            parsed = self._parse_payload(payload, ann_type)
            announcements.extend(
                announcement
                for announcement in parsed
                if announcement.kind in {AnnouncementKind.LISTING, AnnouncementKind.DELISTING}
            )
        return announcements

    def _parse_payload(self, payload: object, fallback_category: str) -> list[ExchangeAnnouncement]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        rows = data if isinstance(data, list) else []
        announcements: list[ExchangeAnnouncement] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = _clean_text(row.get("annTitle"))
            url = _clean_text(row.get("annUrl"))
            category = _clean_text(row.get("annType")) or fallback_category
            sub_type = _clean_text(row.get("annSubType"))
            if sub_type:
                category = f"{category}:{sub_type}"
            announcement = self._announcement(
                announcement_id=_clean_text(row.get("annId")),
                title=title,
                url=url,
                category=category,
                published_at=_parse_datetime_ms(row.get("cTime")),
            )
            if announcement is not None:
                announcements.append(announcement)
        return announcements


class GateAnnouncementProvider(HttpAnnouncementProvider):
    exchange = "gate"
    source = "gate-next-announcements"
    base_url = "https://www.gate.com"
    page_base_url = "https://apim.gateapi.io/announcements"
    categories = ("newspotlistings", "newfutureslistings", "newconvertlistings", "delisted")
    listing_title_patterns = (
        "gate to list",
        "initial listing",
        "launches pre-market trading",
        "new listing",
    )

    async def fetch(self) -> list[ExchangeAnnouncement]:
        announcements: list[ExchangeAnnouncement] = []
        for category in self.categories:
            response = await self.client.get(f"{self.page_base_url}/{category}")
            response.raise_for_status()
            announcements.extend(self._parse_page(response.text, category))
        return announcements

    def _parse_page(self, html: str, fallback_category: str) -> list[ExchangeAnnouncement]:
        payload = self._next_data(html)
        if payload is None:
            return []
        page_props = (
            payload.get("props", {}).get("pageProps", {})
            if isinstance(payload.get("props"), dict)
            else {}
        )
        list_data = page_props.get("listData") if isinstance(page_props, dict) else None
        rows = list_data.get("list") if isinstance(list_data, dict) else None
        if not isinstance(rows, list):
            return []
        announcements: list[ExchangeAnnouncement] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = _clean_text(row.get("title"))
            category = fallback_category
            kind = classify_announcement(title, category)
            if fallback_category == "delisted" and kind != AnnouncementKind.DELISTING:
                continue
            if fallback_category != "delisted" and not self._is_listing_title(title):
                continue
            article_id = _clean_text(row.get("id"))
            url = _article_url(self.base_url, _clean_text(row.get("url")), article_id)
            announcement = self._announcement(
                announcement_id=article_id or url,
                title=title,
                url=url,
                category=category,
                published_at=_parse_datetime_seconds(row.get("release_timestamp") or row.get("created_t")),
            )
            if announcement is not None:
                announcement = announcement.model_copy(update={"kind": kind})
                announcements.append(announcement)
        return announcements

    def _is_listing_title(self, title: str) -> bool:
        normalized = title.strip().lower()
        return any(pattern in normalized for pattern in self.listing_title_patterns)

    def _next_data(self, html: str) -> dict[str, object] | None:
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json" crossorigin="anonymous">(.*?)</script>',
            html,
            re.S,
        )
        if match is None:
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        if match is None:
            return None
        try:
            payload = json.loads(unescape(match.group(1)))
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None


class HyperliquidAnnouncementProvider(HttpAnnouncementProvider):
    exchange = "hyperliquid"
    source = "hyperliquid-meta-universe"
    base_url = "https://api.hyperliquid.xyz/info"
    docs_url = "https://hyperliquid.gitbook.io/hyperliquid-docs"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        repository: AnnouncementRepository | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(client=client, now_fn=now_fn)
        self.repository = repository

    async def fetch(self) -> list[ExchangeAnnouncement]:
        response = await self.client.post(self.base_url, json={"type": "meta"})
        response.raise_for_status()
        return await self._parse_payload(response.json())

    async def _parse_payload(self, payload: object) -> list[ExchangeAnnouncement]:
        if not isinstance(payload, dict):
            return []
        universe = payload.get("universe")
        if not isinstance(universe, list):
            return []

        now = self._now_fn()
        current: dict[str, bool] = {}
        for row in universe:
            if not isinstance(row, dict):
                continue
            name = _clean_text(row.get("name")).upper()
            if not name:
                continue
            current[name] = bool(row.get("isDelisted"))

        previous = await self._load_previous_state()
        await self._save_state(current)
        if previous is None:
            return []

        announcements: list[ExchangeAnnouncement] = []
        previous_delisted = {symbol for symbol, is_delisted in previous.items() if is_delisted}
        previous_active = set(previous) - previous_delisted
        current_delisted = {symbol for symbol, is_delisted in current.items() if is_delisted}
        current_active = set(current) - current_delisted

        for symbol in sorted(current_active - set(previous)):
            announcements.append(
                self._synthetic_announcement(
                    symbol=symbol,
                    kind=AnnouncementKind.LISTING,
                    title=f"Hyperliquid listed {symbol} perpetual market",
                    observed_at=now,
                )
            )
        for symbol in sorted((current_delisted - previous_delisted) | (previous_active - set(current))):
            announcements.append(
                self._synthetic_announcement(
                    symbol=symbol,
                    kind=AnnouncementKind.DELISTING,
                    title=f"Hyperliquid delisted {symbol} perpetual market",
                    observed_at=now,
                )
            )
        return announcements

    def _synthetic_announcement(
        self,
        *,
        symbol: str,
        kind: AnnouncementKind,
        title: str,
        observed_at: datetime,
    ) -> ExchangeAnnouncement:
        return ExchangeAnnouncement(
            exchange=self.exchange,
            announcement_id=f"{kind.value}:{symbol}:{observed_at.date().isoformat()}",
            kind=kind,
            title=title,
            url=self.docs_url,
            source=self.source,
            category="meta-universe",
            published_at=observed_at,
            fetched_at=observed_at,
        )

    async def _load_previous_state(self) -> dict[str, bool] | None:
        if self.repository is None:
            return None
        payload = await self.repository.get_provider_state("hyperliquid:meta-universe")
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(symbols, dict):
            return None
        return {str(symbol).upper(): bool(is_delisted) for symbol, is_delisted in symbols.items()}

    async def _save_state(self, current: dict[str, bool]) -> None:
        if self.repository is None:
            return
        await self.repository.set_provider_state("hyperliquid:meta-universe", {"symbols": current})


class MultiAnnouncementProvider:
    def __init__(self, providers: list[AnnouncementProvider]) -> None:
        self.providers = providers

    async def fetch(self, exchanges: set[str]) -> list[ExchangeAnnouncement]:
        announcements: list[ExchangeAnnouncement] = []
        for provider in self.providers:
            if provider.exchange not in exchanges:
                continue
            try:
                announcements.extend(await provider.fetch())
            except Exception:
                logger.exception("announcement provider failed: %s", provider.exchange)
        return announcements

    async def aclose(self) -> None:
        for provider in self.providers:
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()


class AnnouncementMonitor:
    def __init__(
        self,
        repository: AnnouncementRepository,
        *,
        alert_sender: AlertSender | None = None,
    ) -> None:
        self.repository = repository
        self.alert_sender = alert_sender

    async def process(
        self,
        announcements: list[ExchangeAnnouncement],
        settings: AnnouncementSettings,
        *,
        bootstrap: bool = False,
    ) -> list[ExchangeAnnouncement]:
        record_exchanges = set(settings.record_exchanges)
        alert_exchanges = set(settings.alert_exchanges)
        should_alert_on_bootstrap = settings.bootstrap_alerts_enabled
        created: list[ExchangeAnnouncement] = []
        seen_keys: set[tuple[str, str, str]] = set()

        for announcement in sorted(
            announcements,
            key=lambda item: (item.published_at, item.exchange, item.announcement_id),
        ):
            if announcement.exchange not in record_exchanges:
                continue
            key = (announcement.exchange, announcement.source, announcement.announcement_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            alert_status = "pending" if announcement.exchange in alert_exchanges else "muted"
            if bootstrap and not should_alert_on_bootstrap and alert_status == "pending":
                alert_status = "muted"
            candidate = announcement.model_copy(update={"alert_status": alert_status})
            inserted = await self.repository.create_if_new(candidate)
            if inserted is None:
                continue
            if inserted.alert_status == "pending":
                next_status = await self._send_alert(inserted)
                if next_status != inserted.alert_status:
                    inserted = inserted.model_copy(update={"alert_status": next_status})
                    await self.repository.update_alert_status(inserted.id, next_status)
            created.append(inserted)
        return created

    async def _send_alert(self, announcement: ExchangeAnnouncement) -> str:
        if self.alert_sender is None:
            return "skipped"
        try:
            result = self.alert_sender(build_announcement_alert_message(announcement))
            if isawaitable(result):
                await result
        except Exception:
            logger.exception("announcement alert failed")
            return "failed"
        return "sent"


def default_announcement_provider(
    repository: AnnouncementRepository | None = None,
) -> MultiAnnouncementProvider:
    return MultiAnnouncementProvider(
        [
            BinanceAnnouncementProvider(),
            OKXAnnouncementProvider(),
            BybitAnnouncementProvider(),
            GateAnnouncementProvider(),
            BitgetAnnouncementProvider(),
            HyperliquidAnnouncementProvider(repository=repository),
        ]
    )


async def run_announcement_loop(
    provider: MultiAnnouncementProvider,
    monitor: AnnouncementMonitor,
    settings_loader: SettingsLoader,
    stop_event: asyncio.Event,
    *,
    min_interval_seconds: float = 30.0,
) -> None:
    while not stop_event.is_set():
        interval = min_interval_seconds
        try:
            settings = await settings_loader()
            interval = max(min_interval_seconds, float(settings.poll_interval_seconds))
            if settings.enabled and settings.record_exchanges:
                bootstrap = not await monitor.repository.has_any()
                announcements = await provider.fetch(set(settings.record_exchanges))
                await monitor.process(announcements, settings, bootstrap=bootstrap)
        except Exception:
            logger.exception("announcement loop failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


async def load_announcement_settings(settings_repo: SettingsRepository | None) -> AnnouncementSettings:
    if settings_repo is None:
        return AnnouncementSettings()
    get_settings = getattr(settings_repo, "get_announcement_settings", None)
    if get_settings is None:
        return AnnouncementSettings()
    return await get_settings()
