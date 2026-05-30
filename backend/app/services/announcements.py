from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from inspect import isawaitable
from urllib.parse import quote_plus

import httpx

from app.db.repositories import AnnouncementRepository, SettingsRepository
from app.models.announcement import AnnouncementKind, AnnouncementSettings, ExchangeAnnouncement

AlertSender = Callable[[str], None | Awaitable[None]]
SettingsLoader = Callable[[], Awaitable[AnnouncementSettings]]
logger = logging.getLogger(__name__)

ANNOUNCEMENT_EXCHANGES = ("okx", "bybit", "bitget")
ANNOUNCEMENT_EXCHANGE_OPTIONS = [
    {"label": "OKX", "value": "okx"},
    {"label": "Bybit", "value": "bybit"},
    {"label": "Bitget", "value": "bitget"},
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
        or _contains_keyword(title_value, LISTING_KEYWORDS)
    ):
        return AnnouncementKind.LISTING
    return AnnouncementKind.OTHER


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


def default_announcement_provider() -> MultiAnnouncementProvider:
    return MultiAnnouncementProvider(
        [
            OKXAnnouncementProvider(),
            BybitAnnouncementProvider(),
            BitgetAnnouncementProvider(),
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
