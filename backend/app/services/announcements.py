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
    "上线",
    "上線",
    "正式上线",
    "正式上線",
    "开启",
    "開啟",
)
DELISTING_KEYWORDS = (
    "delist",
    "delisting",
    "remove ",
    "removal",
    "suspend trading",
    "cease trading",
    "下线",
    "下線",
    "下架",
    "暂停交易",
    "暫停交易",
)

MARKET_TYPE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("spot", ("spot", "现货", "現貨", "币币", "幣幣")),
    ("futures", ("futures", "perpetual", "usdⓈ-margined", "usdt perpetual", "contract", "永续", "永續", "合约", "合約", "x-perp", "x-合约", "x-合約")),
    ("stock perpetual", ("股票永续", "股票永續", "stock perpetual")),
    ("margin", (" margin", "margin trading", "cross margin", "isolated margin", "杠杆", "槓桿")),
    ("convert", ("convert",)),
    ("pre-market", ("pre-market", "premarket")),
    ("options", ("options",)),
    ("alpha", ("alpha",)),
    ("airdrop", ("airdrop", "hodler")),
)

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

EVENT_TIME_CONTEXT_KEYWORDS = (
    "trading will start",
    "trading starts",
    "start trading",
    "starts trading",
    "open trading",
    "opens trading",
    "trading opens",
    "will list",
    "to list",
    "will launch",
    "launch at",
    "launch on",
    "listed at",
    "listed on",
    "delist",
    "delisting",
    "remove",
    "cease trading",
    "suspend trading",
    "上线",
    "上線",
    "正式上线",
    "正式上線",
    "开启",
    "開啟",
    "下线",
    "下線",
    "下架",
)
NON_EVENT_TIME_CONTEXT_KEYWORDS = (
    "subscription period",
    "airdrop",
    "snapshot",
    "reward",
    "campaign",
    "eligibility",
    "deposit",
    "withdrawal",
    "redemption",
    "promotion",
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


def _parse_datetime_any(value: object) -> datetime | None:
    parsed = _parse_datetime_ms(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            candidate = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return extract_event_time(text)
        if candidate.tzinfo is None:
            return candidate.replace(tzinfo=UTC)
        return candidate.astimezone(UTC)
    return None


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return unescape(str(value)).replace("\xa0", " ").strip()


def _flatten_json_text(value: object) -> str:
    fragments: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str):
                fragments.append(_clean_text(text))
            for child in node.get("child", []) if isinstance(node.get("child"), list) else []:
                walk(child)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return _clean_text(re.sub(r"<[^>]+>", " ", value))
        walk(parsed)
    else:
        walk(value)
    return re.sub(r"\s+", " ", " ".join(item for item in fragments if item)).strip()


def _json_objects_from_text(text: str) -> list[object]:
    objects: list[object] = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except ValueError:
            continue
        objects.append(value)
    return objects


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


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace(" ", "")


def _symbols_from_parentheses(title: str) -> list[str]:
    symbols: list[str] = []
    stop_words = {"UTC"}
    for value in re.findall(r"\(([A-Z0-9]{2,15})\)", title):
        symbol = _normalize_symbol(value)
        if symbol in stop_words:
            continue
        symbols.append(symbol)
    return symbols


def _symbols_from_uppercase_tokens(title: str) -> list[str]:
    stop_words = {
        "A",
        "I",
        "ADD",
        "ALPHA",
        "API",
        "BINANCE",
        "BNB",
        "BITGET",
        "BYBIT",
        "CONTRACT",
        "CONVERT",
        "CROSS",
        "DELIST",
        "DELISTING",
        "ETF",
        "EUR",
        "FDUSD",
        "FUTURES",
        "GATE",
        "HYPERLIQUID",
        "INITIAL",
        "IPO",
        "LAUNCH",
        "LIST",
        "LISTING",
        "M",
        "MARGIN",
        "NEW",
        "NFT",
        "OFFICIAL",
        "OKX",
        "PERPETUAL",
        "REMOVE",
        "SPOT",
        "THE",
        "TO",
        "TRADING",
        "TRY",
        "USD",
        "USDC",
        "USDT",
        "UTC",
        "WILL",
        "X",
    }
    symbols: list[str] = []
    for value in re.findall(r"\b[A-Z][A-Z0-9]{0,14}(?:/[A-Z0-9]{2,12})?\b", title):
        symbol = _normalize_symbol(value)
        if symbol in stop_words:
            continue
        symbols.append(symbol)
    return symbols


def _symbols_from_okx_contract_title(title: str) -> list[str]:
    match = re.search(r"关于\s+(.+?)\s+(?:股票)?(?:X-)?(?:永续)?合[约約]", title, re.I)
    if match is None:
        match = re.search(r"關於\s+(.+?)\s+(?:股票)?(?:X-)?(?:永續)?合[約约]", title, re.I)
    if match is None:
        return []
    symbols: list[str] = []
    for value in re.split(r"[、,，\s]+", match.group(1)):
        symbol = _normalize_symbol(value)
        if 1 <= len(symbol) <= 20 and re.fullmatch(r"[A-Z0-9]+(?:USDT)?", symbol):
            symbols.append(symbol)
    return symbols


def infer_symbols(title: str) -> list[str]:
    normalized: list[str] = []
    for symbol in [
        *_symbols_from_okx_contract_title(title),
        *_symbols_from_parentheses(title),
        *_symbols_from_uppercase_tokens(title),
    ]:
        if symbol.endswith("USDT") and len(symbol) > 4:
            normalized.append(symbol)
        elif "/" in symbol or 1 <= len(symbol) <= 12:
            normalized.append(symbol)
    seen: set[str] = set()
    result: list[str] = []
    for symbol in normalized:
        if symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    pair_bases = {symbol[:-4] for symbol in result if symbol.endswith("USDT") and len(symbol) > 4}
    return [symbol for symbol in result if symbol not in pair_bases]


def infer_market_type(title: str, category: str | None = None) -> str | None:
    def matched_labels(text: str) -> list[str]:
        normalized = text.lower()
        return [
            label
            for label, patterns in MARKET_TYPE_PATTERNS
            if any(pattern in normalized for pattern in patterns)
        ]

    matched = matched_labels(title) or matched_labels(category or "")
    if not matched:
        return None
    primary = []
    for label in ("spot", "futures", "stock perpetual", "margin", "convert", "pre-market", "options", "alpha", "airdrop"):
        if label in matched:
            primary.append(label)
    if "spot" in primary and "margin" in primary:
        primary = ["spot margin", *[label for label in primary if label not in {"spot", "margin"}]]
    return "/".join(primary[:3])


def _datetime_from_parts(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime | None:
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None


def _apply_timezone_hint(value: datetime, context: str) -> datetime:
    if "utc+8" in context.lower() or "北京时间" in context or "香港时间" in context:
        return value - timedelta(hours=8)
    return value


def extract_event_time(text: str) -> datetime | None:
    normalized = _clean_text(re.sub(r"\s+", " ", text))
    patterns = (
        re.compile(
            r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(?:\(UTC\)|UTC)?",
            re.I,
        ),
        re.compile(
            r"\b([A-Z][a-z]+)\s+(\d{1,2}),?\s+(20\d{2})[, ]+(?:at\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(?:\(UTC\)|UTC)?",
            re.I,
        ),
        re.compile(
            r"\b(\d{1,2})\s+([A-Z][a-z]+)\s+(20\d{2})[, ]+(?:at\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(?:\(UTC\)|UTC)?",
            re.I,
        ),
        re.compile(
            r"\b(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(?:上午|下午)?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(?:\(UTC\+?8\)|UTC\+?8|北京时间|香港时间)?",
            re.I,
        ),
    )
    candidates: list[tuple[int, int, datetime, str]] = []
    for pattern in patterns:
        for match in pattern.finditer(normalized):
            values = match.groups(default="0")
            parsed: datetime | None = None
            matched_text = match.group(0)
            if values[0].isdigit() and len(values[0]) == 4:
                year, month, day = int(values[0]), int(values[1]), int(values[2])
                hour, minute, second = int(values[3]), int(values[4]), int(values[5])
                parsed = _datetime_from_parts(year, month, day, hour, minute, second)
            elif values[0].lower() in MONTHS:
                month = MONTHS[values[0].lower()]
                day, year = int(values[1]), int(values[2])
                hour, minute, second = int(values[3]), int(values[4]), int(values[5])
                parsed = _datetime_from_parts(year, month, day, hour, minute, second)
            elif values[1].lower() in MONTHS:
                day = int(values[0])
                month = MONTHS[values[1].lower()]
                year = int(values[2])
                hour, minute, second = int(values[3]), int(values[4]), int(values[5])
                parsed = _datetime_from_parts(year, month, day, hour, minute, second)
            if parsed is not None:
                candidates.append((match.start(), match.end(), _apply_timezone_hint(parsed, matched_text), matched_text))
    if not candidates:
        return None

    scored: list[tuple[int, int, datetime]] = []
    for index, (start, end, parsed, matched_text) in enumerate(candidates):
        context = normalized[max(0, start - 120) : min(len(normalized), end + 120)].lower()
        score = sum(3 for keyword in EVENT_TIME_CONTEXT_KEYWORDS if keyword in context)
        score -= sum(2 for keyword in NON_EVENT_TIME_CONTEXT_KEYWORDS if keyword in context)
        if "utc+8" in matched_text.lower() or "北京时间" in matched_text or "香港时间" in matched_text:
            score += 1
        scored.append((score, -index, parsed))
    best_score, _, best = max(scored, key=lambda item: (item[0], item[1]))
    return best if best_score > 0 else None


def _event_time_from_row(row: dict) -> datetime | None:
    for key in (
        "startTime",
        "start_time",
        "startDate",
        "start_date",
        "startDateTimestamp",
        "endDateTimestamp",
        "tradingStartTime",
        "trading_start_time",
        "onlineTime",
        "online_time",
        "listTime",
        "listingTime",
        "delistTime",
        "delistingTime",
        "deliveryTime",
    ):
        parsed = _parse_datetime_any(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _announcement_summary(
    *,
    kind: AnnouncementKind,
    symbols: list[str],
    market_type: str | None,
    event_time: datetime | None,
) -> str | None:
    pieces: list[str] = []
    if symbols:
        pieces.append(f"symbols={','.join(symbols[:8])}")
    if market_type:
        pieces.append(f"market={market_type}")
    if event_time:
        pieces.append(f"event_time={event_time.isoformat()}")
    if not pieces:
        return None
    return f"{kind.value}: " + "; ".join(pieces)


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
        f"公告时间: {_display_time(announcement.published_at)} UTC+8",
    ]
    if announcement.symbols:
        lines.append(f"币种: {', '.join(announcement.symbols)}")
    if announcement.market_type:
        lines.append(f"市场: {announcement.market_type}")
    if announcement.event_time:
        lines.append(f"事件时间: {_display_time(announcement.event_time)} UTC+8")
    lines.append(f"标题: {announcement.title}")
    if announcement.category:
        lines.append(f"分类: {announcement.category}")
    lines.append(f"链接: {announcement.url}")
    return "\n".join(lines)


def build_announcement_event_reminder_message(
    announcement: ExchangeAnnouncement,
    *,
    minutes_before: int,
    now: datetime,
) -> str:
    kind_label = {
        AnnouncementKind.LISTING: "上币",
        AnnouncementKind.DELISTING: "下币",
        AnnouncementKind.OTHER: "公告",
    }[announcement.kind]
    lines = [
        f"[{announcement.exchange.upper()}] {kind_label}快到时间提醒",
        f"提醒窗口: 提前 {minutes_before} 分钟",
    ]
    if announcement.event_time:
        remaining_seconds = max(0, int((announcement.event_time - now).total_seconds()))
        remaining_minutes = max(0, remaining_seconds // 60)
        lines.append(f"事件时间: {_display_time(announcement.event_time)} UTC+8")
        lines.append(f"剩余: 约 {remaining_minutes} 分钟")
    if announcement.symbols:
        lines.append(f"币种: {', '.join(announcement.symbols)}")
    if announcement.market_type:
        lines.append(f"市场: {announcement.market_type}")
    lines.append(f"标题: {announcement.title}")
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

    async def _get_text(self, url: str) -> str:
        response = await self.client.get(url)
        response.raise_for_status()
        return response.text

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
        content: str | None = None,
        symbols: list[str] | None = None,
        market_type: str | None = None,
        event_time: datetime | None = None,
    ) -> ExchangeAnnouncement | None:
        title = title.strip()
        url = url.strip()
        announcement_id = announcement_id.strip() or url or title
        if not title or not url or not announcement_id:
            return None
        kind = classify_announcement(title, category)
        searchable_text = f"{title} {content or ''}"
        inferred_symbols = symbols if symbols is not None else infer_symbols(title)
        inferred_market_type = market_type or infer_market_type(title, category)
        inferred_event_time = event_time or extract_event_time(searchable_text)
        summary = _announcement_summary(
            kind=kind,
            symbols=inferred_symbols,
            market_type=inferred_market_type,
            event_time=inferred_event_time,
        )
        reminder_status = "pending" if inferred_event_time and inferred_event_time > self._now_fn() else "not_applicable"
        return ExchangeAnnouncement(
            exchange=self.exchange,
            announcement_id=announcement_id,
            kind=kind,
            title=title,
            url=url,
            source=self.source,
            category=category,
            symbols=inferred_symbols,
            market_type=inferred_market_type,
            event_time=inferred_event_time,
            summary=summary,
            published_at=published_at or self._now_fn(),
            fetched_at=self._now_fn(),
            event_reminder_status=reminder_status,
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
            rows = self._rows_from_payload(payload)
            for row in rows:
                code = _clean_text(row.get("code"))
                content = await self._fetch_article_text(code) if code else None
                announcement = self._announcement_from_row(row, str(catalog_id), category, content=content)
                if announcement is not None:
                    announcements.append(announcement)
        return announcements

    async def _fetch_article_text(self, code: str) -> str | None:
        try:
            payload = await self._get_json(f"{self.api_url.replace('/list/', '/detail/')}?articleCode={code}")
        except Exception:
            logger.debug("failed to fetch binance announcement detail: %s", code, exc_info=True)
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        body = data.get("body") if isinstance(data, dict) else None
        return _flatten_json_text(body)

    def _rows_from_payload(self, payload: object) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        catalogs = data.get("catalogs") if isinstance(data, dict) else None
        if not isinstance(catalogs, list):
            return []
        rows: list[dict] = []
        for catalog in catalogs:
            if not isinstance(catalog, dict):
                continue
            articles = catalog.get("articles")
            if isinstance(articles, list):
                rows.extend(item for item in articles if isinstance(item, dict))
        return rows

    def _parse_payload(
        self,
        payload: object,
        catalog_id: str,
        fallback_category: str,
    ) -> list[ExchangeAnnouncement]:
        announcements: list[ExchangeAnnouncement] = []
        for row in self._rows_from_payload(payload):
            announcement = self._announcement_from_row(row, catalog_id, fallback_category)
            if announcement is not None:
                announcements.append(announcement)
        return announcements

    def _announcement_from_row(
        self,
        row: dict,
        catalog_id: str,
        fallback_category: str,
        *,
        content: str | None = None,
    ) -> ExchangeAnnouncement | None:
        title = _clean_text(row.get("title"))
        code = _clean_text(row.get("code"))
        article_id = _clean_text(row.get("id"))
        announcement_id = code or article_id
        url = f"{self.base_url}/en/support/announcement/{code}" if code else ""
        return self._announcement(
            announcement_id=announcement_id,
            title=title,
            url=url,
            category=f"{catalog_id}:{fallback_category}",
            published_at=_parse_datetime_ms(row.get("releaseDate") or row.get("publishDate")),
            content=content,
        )


class OKXAnnouncementProvider(HttpAnnouncementProvider):
    exchange = "okx"
    source = "okx-support-announcements"
    site_base_url = "https://www.okx.com"
    base_url = "https://www.okx.com/api/v5/support/announcements"
    ann_types = ("announcements-new-listings", "announcements-delistings")
    latest_urls = (
        "https://www.okx.com/zh-hans/help/section/announcements-latest-announcements",
        "https://www.okx.com/help/section/announcements-latest-announcements",
    )

    async def fetch(self) -> list[ExchangeAnnouncement]:
        announcements: list[ExchangeAnnouncement] = []
        for ann_type in self.ann_types:
            payload = await self._get_json(f"{self.base_url}?annType={ann_type}&page=1")
            announcements.extend(self._parse_payload(payload, ann_type))
        for url in self.latest_urls:
            try:
                html = await self._get_text(url)
            except Exception:
                logger.debug("failed to fetch okx latest announcements page: %s", url, exc_info=True)
                continue
            announcements.extend(self._parse_latest_page(html, "announcements-latest"))
        return announcements

    def _parse_latest_page(self, html: str, fallback_category: str) -> list[ExchangeAnnouncement]:
        candidates: list[dict[str, object]] = []
        for match in re.finditer(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.S | re.I):
            try:
                payload = json.loads(unescape(match.group(1)))
            except ValueError:
                continue
            candidates.extend(self._find_okx_article_dicts(payload))
        if not candidates:
            candidates.extend(self._find_okx_article_dicts(_json_objects_from_text(html)))
        announcements: list[ExchangeAnnouncement] = []
        seen: set[str] = set()
        for row in candidates:
            title = _clean_text(row.get("title") or row.get("annTitle") or row.get("name"))
            url = _article_url(
                self.site_base_url,
                _clean_text(row.get("url") or row.get("link") or row.get("href")),
                _clean_text(row.get("id") or row.get("annId") or title),
            )
            if not title or "/help/" not in url:
                continue
            dedupe_key = f"{title}|{url}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            category = _clean_text(row.get("annType") or row.get("category") or row.get("type")) or fallback_category
            published_at = (
                _parse_datetime_any(row.get("pTime"))
                or _parse_datetime_any(row.get("businessPTime"))
                or _parse_datetime_any(row.get("publishTime"))
                or _parse_datetime_any(row.get("publishDate"))
                or _parse_datetime_any(row.get("createdAt"))
            )
            announcement = self._announcement(
                announcement_id=_clean_text(row.get("annId") or row.get("id")) or url.rstrip("/").rsplit("/", 1)[-1],
                title=title,
                url=url,
                category=category,
                published_at=published_at,
                content=_clean_text(row.get("desc") or row.get("summary") or row.get("brief")),
            )
            if announcement is not None and announcement.kind in {AnnouncementKind.LISTING, AnnouncementKind.DELISTING}:
                announcements.append(announcement)
        return announcements

    def _find_okx_article_dicts(self, value: object) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []

        def walk(node: object) -> None:
            if isinstance(node, dict):
                title = node.get("title") or node.get("annTitle") or node.get("name")
                url = node.get("url") or node.get("link") or node.get("href")
                if isinstance(title, str) and isinstance(url, str) and ("/help/" in url or url.startswith("/help/")):
                    rows.append(node)
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return rows

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
                event_time=_event_time_from_row(row),
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
                event_time=_event_time_from_row(row),
            )
            if announcement is not None:
                announcements.append(announcement)
        return announcements


class BitgetAnnouncementProvider(HttpAnnouncementProvider):
    exchange = "bitget"
    source = "bitget-public-annoucements"
    base_url = "https://api.bitget.com/api/v2/public/annoucements"
    ann_types = ("coin_listings", "symbol_delisting")

    async def fetch(self) -> list[ExchangeAnnouncement]:
        announcements: list[ExchangeAnnouncement] = []
        for ann_type in self.ann_types:
            query = f"language=en_US&annType={quote_plus(ann_type)}&limit=10"
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
                event_time=_event_time_from_row(row),
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
            brief = _clean_text(row.get("brief"))
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
                content=brief,
                symbols=infer_symbols(f"{title} {_clean_text(row.get('tags'))}"),
                event_time=_event_time_from_row(row),
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

        announcements: list[ExchangeAnnouncement] = []
        previous_delisted = {
            symbol for symbol, is_delisted in (previous or {}).items() if is_delisted
        }
        previous_active = set(previous) - previous_delisted if previous is not None else set()
        current_delisted = {symbol for symbol, is_delisted in current.items() if is_delisted}
        current_active = set(current) - current_delisted

        should_emit_baseline = previous is None or not await self._has_baseline_records()
        new_active = current_active if should_emit_baseline else current_active - set(previous or {})
        for symbol in sorted(new_active):
            announcements.append(
                self._synthetic_announcement(
                    symbol=symbol,
                    kind=AnnouncementKind.LISTING,
                    title=(
                        f"Hyperliquid currently lists {symbol} perpetual market"
                        if should_emit_baseline
                        else f"Hyperliquid listed {symbol} perpetual market"
                    ),
                    observed_at=now,
                    baseline=should_emit_baseline,
                )
            )
        delisted_symbols = set() if previous is None else (current_delisted - previous_delisted) | (previous_active - set(current))
        for symbol in sorted(delisted_symbols):
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
        baseline: bool = False,
    ) -> ExchangeAnnouncement:
        return ExchangeAnnouncement(
            exchange=self.exchange,
            announcement_id=(
                f"baseline:{symbol}"
                if baseline
                else f"{kind.value}:{symbol}:{observed_at.date().isoformat()}"
            ),
            kind=kind,
            title=title,
            url=self.docs_url,
            source=self.source,
            category="meta-universe:baseline" if baseline else "meta-universe",
            symbols=[symbol],
            market_type="futures",
            summary=_announcement_summary(
                kind=kind,
                symbols=[symbol],
                market_type="futures",
                event_time=None if baseline else observed_at,
            ),
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

    async def _has_baseline_records(self) -> bool:
        if self.repository is None:
            return False
        rows = await self.repository.list(exchange=self.exchange, limit=1)
        return any(row.source == self.source for row in rows)


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
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.alert_sender = alert_sender
        self._now_fn = now_fn or utc_now

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

    async def process_due_event_reminders(self, settings: AnnouncementSettings) -> list[ExchangeAnnouncement]:
        if not settings.event_reminders_enabled:
            return []
        alert_exchanges = set(settings.alert_exchanges)
        if not alert_exchanges:
            return []
        now = self._now_fn()
        reminder_due_before = now + timedelta(minutes=settings.event_reminder_minutes_before)
        due_rows = await self.repository.list_due_event_reminders(
            exchanges=alert_exchanges,
            reminder_due_before=reminder_due_before,
            now=now,
        )
        reminded: list[ExchangeAnnouncement] = []
        for announcement in due_rows:
            status = await self._send_event_reminder(
                announcement,
                minutes_before=settings.event_reminder_minutes_before,
                now=now,
            )
            sent_at = now if status == "sent" else None
            await self.repository.update_event_reminder_status(
                announcement.id,
                status,
                sent_at=sent_at,
            )
            reminded.append(
                announcement.model_copy(
                    update={
                        "event_reminder_status": status,
                        "event_reminder_sent_at": sent_at,
                    }
                )
            )
        return reminded

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

    async def _send_event_reminder(
        self,
        announcement: ExchangeAnnouncement,
        *,
        minutes_before: int,
        now: datetime,
    ) -> str:
        if self.alert_sender is None:
            return "skipped"
        try:
            result = self.alert_sender(
                build_announcement_event_reminder_message(
                    announcement,
                    minutes_before=minutes_before,
                    now=now,
                )
            )
            if isawaitable(result):
                await result
        except Exception:
            logger.exception("announcement event reminder failed")
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
                await monitor.process_due_event_reminders(settings)
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
