from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from inspect import isawaitable
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx

from app.models.oil_news import (
    SEVERITY_RANK,
    OilMarketSnapshot,
    OilNewsDirection,
    OilNewsItem,
    OilNewsRefreshResult,
    OilNewsSettings,
    OilNewsSeverity,
)

logger = logging.getLogger(__name__)
AlertSender = Callable[[str], None | Awaitable[None]]
SettingsLoader = Callable[[], Awaitable[OilNewsSettings]]

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
EIA_TODAY_IN_ENERGY_RSS = "https://www.eia.gov/rss/todayinenergy.xml"
BINANCE_CL_KLINES = "https://fapi.binance.com/fapi/v1/klines"
MYMEMORY_TRANSLATE_URL = "https://api.mymemory.translated.net/get"
GOOGLE_TRANSLATE_URL = "https://clients5.google.com/translate_a/t"
TRANSLATION_MAX_CHARS = 450
CST = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class OilNewsFeed:
    key: str
    url: str


DEFAULT_OIL_NEWS_FEEDS = (
    OilNewsFeed(
        key="google-oil-markets",
        url=(
            f"{GOOGLE_NEWS_RSS}?q={quote_plus('(crude oil OR WTI OR Brent OR OPEC) when:1d')}"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
    ),
    OilNewsFeed(
        key="google-oil-geopolitics",
        url=(
            f"{GOOGLE_NEWS_RSS}?q="
            f"{quote_plus('(Hormuz OR oil tanker OR oil sanctions OR oil pipeline) when:1d')}"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
    ),
    OilNewsFeed(key="eia-today-in-energy", url=EIA_TODAY_IN_ENERGY_RSS),
)

TOPIC_TERMS = (
    "crude oil",
    "oil price",
    "oil market",
    "oil supply",
    "oil demand",
    "oil output",
    "oil production",
    "oil export",
    "oil import",
    "oil inventory",
    "petroleum",
    "brent",
    "wti",
    "opec",
    "hormuz",
    "oil tanker",
    "refinery",
    "pipeline",
    "eia",
)

CATEGORY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("霍尔木兹/航运", ("hormuz", "strait", "shipping", "tanker", "red sea", "bab el-mandeb", "suez")),
    ("地缘冲突", ("war", "attack", "strike", "missile", "drone", "iran", "israel", "houthi")),
    ("OPEC+", ("opec", "output quota", "production quota")),
    ("库存/EIA", ("inventory", "inventories", "stockpile", "eia", "api data")),
    ("供应中断", ("outage", "pipeline", "refinery", "disruption", "force majeure")),
    ("制裁", ("sanction", "embargo", "price cap")),
    ("需求/宏观", ("demand", "recession", "growth forecast", "china economy", "dollar")),
)

# Phrase rules deliberately favor explicit events over generic market commentary.
BULLISH_RULES: tuple[tuple[str, int, str], ...] = (
    ("strait closed", 34, "关键海峡关闭，供应链中断风险上升"),
    ("close the strait", 34, "关键海峡关闭威胁推高供应风险"),
    ("shipping halted", 32, "航运中止会压缩可交付供应"),
    ("blockade", 30, "封锁风险推高原油风险溢价"),
    ("tanker attacked", 28, "油轮遇袭增加运输中断风险"),
    ("tanker hit", 28, "油轮受损增加运输中断风险"),
    ("pipeline attack", 28, "管道遇袭可能直接减少供应"),
    ("pipeline ablaze", 28, "管道起火可能直接减少供应"),
    ("pipeline explosion", 28, "管道爆炸可能直接减少供应"),
    ("pipeline fire", 26, "管道火灾可能造成供应中断"),
    ("pipeline damaged", 26, "管道受损可能造成供应中断"),
    ("production cut", 25, "产量削减收紧供给"),
    ("output cut", 25, "产量削减收紧供给"),
    ("exports suspended", 25, "出口暂停减少国际市场供应"),
    ("exports fall", 20, "出口下降收紧可交易供应"),
    ("supply disruption", 24, "供应中断推高原油价格风险"),
    ("force majeure", 24, "不可抗力通常意味着供应无法按约交付"),
    ("sanctions tightened", 22, "制裁收紧可能压低受制裁国产量或出口"),
    ("new sanctions", 20, "新增能源制裁可能压缩供应"),
    ("inventory draw", 18, "库存下降显示短期供需偏紧"),
    ("inventories fell", 18, "库存下降显示短期供需偏紧"),
    ("stockpiles fell", 18, "库存下降显示短期供需偏紧"),
    ("ceasefire collapses", 22, "停火破裂重新抬升地缘风险"),
    ("talks collapse", 20, "谈判破裂抬升供应中断风险"),
    ("tensions escalate", 18, "局势升级增加供应中断概率"),
)

BEARISH_RULES: tuple[tuple[str, int, str], ...] = (
    ("strait reopens", 34, "关键海峡复航降低运输中断风险"),
    ("shipping resumes", 32, "航运恢复释放此前计入的风险溢价"),
    ("ceasefire agreed", 28, "停火协议降低供应中断风险"),
    ("ceasefire deal", 28, "停火协议降低供应中断风险"),
    ("peace deal", 26, "和平协议降低地缘风险溢价"),
    ("hormuz deal", 25, "霍尔木兹协议降低封锁与运输中断风险"),
    ("push for hormuz deal", 25, "霍尔木兹谈判降低封锁风险预期"),
    ("peace talks", 18, "和平谈判降低冲突继续升级的概率"),
    ("talks resume", 16, "谈判恢复降低地缘风险预期"),
    ("sanctions eased", 24, "制裁放松可能增加国际市场供应"),
    ("sanctions relief", 22, "制裁豁免可能释放额外供应"),
    ("production increase", 25, "增产扩大供给"),
    ("output increase", 25, "增产扩大供给"),
    ("raise output", 24, "增产扩大供给"),
    ("exports resume", 24, "出口恢复增加国际市场供应"),
    ("inventory build", 18, "库存增加显示短期供应更宽松"),
    ("inventories rose", 18, "库存增加显示短期供应更宽松"),
    ("stockpiles rose", 18, "库存增加显示短期供应更宽松"),
    ("demand forecast cut", 20, "需求预期下调压低均衡油价"),
    ("demand slows", 18, "需求放缓压低原油价格预期"),
    ("recession fears", 16, "衰退预期削弱原油需求"),
)

IMPACT_TERMS: tuple[tuple[str, int], ...] = (
    ("hormuz", 24),
    ("blockade", 20),
    ("strait closed", 24),
    ("shipping halted", 22),
    ("opec", 15),
    ("sanction", 14),
    ("tanker", 13),
    ("pipeline", 12),
    ("force majeure", 18),
    ("inventory", 12),
    ("inventories", 12),
    ("crude oil stocks", 12),
)

TRUSTED_SOURCES = {
    "reuters": 12,
    "financial times": 12,
    "bloomberg": 12,
    "associated press": 10,
    "the wall street journal": 10,
    "cnbc": 8,
    "eia": 15,
    "u.s. energy information administration": 15,
    "opec": 15,
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _clean_html(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse_published_at(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        return _as_utc(parsedate_to_datetime(value))
    except (TypeError, ValueError):
        try:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return fallback


def _child_text(node: ElementTree.Element, *names: str) -> str:
    for child in node:
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        if local_name in names and child.text:
            return child.text.strip()
    return ""


def _canonical_title(title: str, source: str) -> str:
    text = _clean_html(title)
    suffix = f" - {source}" if source else ""
    if suffix and text.lower().endswith(suffix.lower()):
        text = text[: -len(suffix)].rstrip()
    return text


def _fingerprint(title: str, published_at: datetime) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    dated_title = f"{published_at:%Y-%m-%d}|{normalized}"
    return hashlib.sha256(dated_title.encode("utf-8")).hexdigest()


def _external_id(url: str, title: str) -> str:
    return hashlib.sha256(f"{url}|{title}".encode("utf-8")).hexdigest()


def _source_bonus(source: str) -> int:
    lowered = source.lower()
    return max((points for key, points in TRUSTED_SOURCES.items() if key in lowered), default=0)


def _categories(text: str) -> list[str]:
    return [label for label, terms in CATEGORY_TERMS if any(term in text for term in terms)]


def _horizon(categories: list[str]) -> str:
    if "霍尔木兹/航运" in categories or "地缘冲突" in categories:
        return "6小时-3天"
    if "库存/EIA" in categories:
        return "1小时-1天"
    if "OPEC+" in categories or "制裁" in categories:
        return "1天-2周"
    if "供应中断" in categories:
        return "6小时-1周"
    return "1天-4周"


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _terms_near(
    text: str,
    subjects: tuple[str, ...],
    signals: tuple[str, ...],
    *,
    max_gap_words: int = 5,
) -> bool:
    gap = rf"(?:\W+\w+){{0,{max_gap_words}}}\W+"
    return any(
        re.search(rf"\b{subject}\b{gap}\b{signal}\b", text)
        or re.search(rf"\b{signal}\b{gap}\b{subject}\b", text)
        for subject in subjects
        for signal in signals
    )


def _derived_direction_signals(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    bullish: list[tuple[int, str]] = []
    bearish: list[tuple[int, str]] = []
    production_subjects = ("output", "production", "export", "exports")
    inventory_subjects = (
        "inventory",
        "inventories",
        "stockpile",
        "stockpiles",
        "crude oil stocks",
        "oil stocks",
    )
    falling_signals = (
        "slump(?:ed|s|ing)?",
        "fell",
        "falls",
        "falling",
        "declin(?:e|ed|es|ing)",
        "drop(?:ped|s|ping)?",
        "lowest",
    )
    rising_signals = (
        "increas(?:e|ed|es|ing)",
        "rose",
        "rise(?:s|n|ing)?",
        "rais(?:e|ed|es|ing)",
        "surg(?:e|ed|es|ing)",
        "highest",
    )

    if "demand" in text and "forecast" in text and _has_any(
        text, ("lower", "cut", "downgrade", "reduce")
    ):
        bearish.append((22, "原油需求预测下调，压低均衡价格预期"))
    if _terms_near(text, production_subjects, falling_signals):
        bullish.append((22, "产量或出口下降，收紧国际市场供应"))
    if _terms_near(text, production_subjects, rising_signals):
        bearish.append((22, "产量或出口增加，扩大国际市场供应"))
    if _terms_near(text, inventory_subjects, (*falling_signals, "draw")):
        bullish.append((18, "库存下降显示短期供需偏紧"))
    if _terms_near(text, inventory_subjects, (*rising_signals, "build", "jump(?:ed|s|ing)?")):
        bearish.append((18, "库存增加显示短期供应更宽松"))
    if "hormuz" in text and _has_any(text, ("choked", "blocked", "closure", "tightens grip")):
        bullish.append((24, "霍尔木兹通航受限，供应链中断风险上升"))
    if "supply risk" in text and _has_any(text, ("raise", "rise", "mount", "grow", "escalat")):
        bullish.append((22, "供应中断风险上升，推高原油风险溢价"))
    if "supply risk" in text and _has_any(text, ("ease", "fall", "recede", "declin")):
        bearish.append((22, "供应中断风险下降，释放原油风险溢价"))
    return bullish, bearish


def classify_oil_news(
    *,
    title: str,
    summary: str | None,
    source: str,
) -> tuple[OilNewsSeverity, int, OilNewsDirection, float, list[str], list[str], str, str] | None:
    text = f"{title} {summary or ''}".lower()
    if not any(term in text for term in TOPIC_TERMS):
        return None

    categories = _categories(text)
    bullish_score = 0
    bearish_score = 0
    bullish_reasons: list[str] = []
    bearish_reasons: list[str] = []
    for phrase, points, reason in BULLISH_RULES:
        if phrase in text:
            bullish_score += points
            bullish_reasons.append(reason)
    for phrase, points, reason in BEARISH_RULES:
        if phrase in text:
            bearish_score += points
            bearish_reasons.append(reason)
    derived_bullish, derived_bearish = _derived_direction_signals(text)
    for points, reason in derived_bullish:
        if reason not in bullish_reasons:
            bullish_score += points
            bullish_reasons.append(reason)
    for points, reason in derived_bearish:
        if reason not in bearish_reasons:
            bearish_score += points
            bearish_reasons.append(reason)

    directional_total = bullish_score + bearish_score
    difference = bullish_score - bearish_score
    if difference >= 12:
        direction = OilNewsDirection.LONG
        rationale = bullish_reasons[:3]
    elif difference <= -12:
        direction = OilNewsDirection.SHORT
        rationale = bearish_reasons[:3]
    else:
        direction = OilNewsDirection.WATCH
        rationale = [*(bullish_reasons[:2]), *(bearish_reasons[:2])]

    impact_bonus = sum(points for phrase, points in IMPACT_TERMS if phrase in text)
    score = min(100, 18 + max(bullish_score, bearish_score) + impact_bonus + _source_bonus(source))
    if score >= 85:
        severity = OilNewsSeverity.CRITICAL
    elif score >= 60:
        severity = OilNewsSeverity.HIGH
    elif score >= 40:
        severity = OilNewsSeverity.MEDIUM
    else:
        severity = OilNewsSeverity.LOW

    source_points = _source_bonus(source)
    unverified = _has_any(text, ("unconfirmed", "reportedly", "claims", "rumor"))
    if directional_total == 0:
        confidence = 0.35
        rationale = rationale or ["标题涉及原油核心变量，但尚未给出明确的供应或需求变化"]
    else:
        dominance = abs(difference) / directional_total
        confidence = min(0.95, 0.55 + 0.2 * dominance + min(0.2, source_points / 60))
    if unverified:
        confidence = max(0.35, confidence - 0.2)
        rationale.append("消息包含未确认或消息源引述，需等待官方或第二可靠来源确认")
    if direction == OilNewsDirection.WATCH:
        confidence = min(confidence, 0.55)

    if bullish_score and bearish_score:
        rationale.append("标题同时包含相反信号，需等待正式条款或市场确认")
    risk_note = {
        OilNewsDirection.LONG: "若供应未实际中断、谈判迅速降温或航运恢复，多头逻辑可能失效。",
        OilNewsDirection.SHORT: "若协议未获官方确认、谈判破裂或出现新的袭击，空头逻辑可能快速反转。",
        OilNewsDirection.WATCH: "信息方向尚不充分，等待官方确认和 CL/Brent 价格共同验证。",
    }[direction]
    return (
        severity,
        score,
        direction,
        confidence,
        categories,
        rationale,
        _horizon(categories),
        risk_note,
    )


class OilNewsProvider:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        feeds: tuple[OilNewsFeed, ...] = DEFAULT_OIL_NEWS_FEEDS,
        now_fn: Callable[[], datetime] = utc_now,
    ):
        self._client = client
        self._owns_client = client is None
        self.feeds = feeds
        self.now_fn = now_fn

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers={"User-Agent": "ArbitrageRadar/1.0 oil-news-monitor"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()

    async def fetch(self) -> tuple[list[OilNewsItem], list[str]]:
        results = await asyncio.gather(
            *(self._fetch_feed(feed) for feed in self.feeds),
            return_exceptions=True,
        )
        rows: list[OilNewsItem] = []
        errors: list[str] = []
        seen: set[str] = set()
        for feed, result in zip(self.feeds, results, strict=True):
            if isinstance(result, BaseException):
                errors.append(f"{feed.key}: {result}")
                continue
            for row in result:
                if row.fingerprint in seen:
                    continue
                seen.add(row.fingerprint)
                rows.append(row)
        return rows, errors

    async def fetch_market_snapshot(self) -> OilMarketSnapshot | None:
        try:
            response = await self.client.get(
                BINANCE_CL_KLINES,
                params={"symbol": "CLUSDT", "interval": "1m", "limit": 61},
            )
            response.raise_for_status()
            payload = response.json()
            closes = [float(row[4]) for row in payload if isinstance(row, list) and len(row) > 4]
            if not closes:
                return None
            change = ((closes[-1] / closes[0]) - 1) * 100 if closes[0] else None
            return OilMarketSnapshot(
                price=closes[-1],
                change_1h_pct=change,
                observed_at=self.now_fn(),
            )
        except (httpx.HTTPError, TypeError, ValueError, KeyError):
            logger.exception("failed to fetch CLUSDT market snapshot")
            return None

    async def _fetch_feed(self, feed: OilNewsFeed) -> list[OilNewsItem]:
        response = await self.client.get(feed.url)
        response.raise_for_status()
        return self._parse_feed(response.text, feed)

    def _parse_feed(self, content: str, feed: OilNewsFeed) -> list[OilNewsItem]:
        root = ElementTree.fromstring(content)
        fetched_at = self.now_fn()
        rows: list[OilNewsItem] = []
        nodes = [
            node
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
        ]
        for node in nodes:
            raw_title = _child_text(node, "title")
            url = _child_text(node, "link")
            if not url:
                link_node = next(
                    (child for child in node if child.tag.rsplit("}", 1)[-1].lower() == "link"),
                    None,
                )
                url = link_node.attrib.get("href", "") if link_node is not None else ""
            summary = _clean_html(_child_text(node, "description", "summary", "content")) or None
            source = _child_text(node, "source")
            if not source and feed.key.startswith("eia"):
                source = "U.S. Energy Information Administration"
            title = _canonical_title(raw_title, source)
            if not title or not url:
                continue
            classification = classify_oil_news(title=title, summary=summary, source=source)
            if classification is None:
                continue
            (
                severity,
                score,
                direction,
                confidence,
                categories,
                rationale,
                horizon,
                risk_note,
            ) = classification
            published_at = _parse_published_at(
                _child_text(node, "pubdate", "published", "updated"),
                fetched_at,
            )
            fingerprint = _fingerprint(title, published_at)
            rows.append(
                OilNewsItem(
                    fingerprint=fingerprint,
                    external_id=_external_id(url, title),
                    source=source or feed.key,
                    source_feed=feed.key,
                    title=title,
                    url=url,
                    summary=summary,
                    published_at=published_at,
                    fetched_at=fetched_at,
                    categories=categories,
                    severity=severity,
                    impact_score=score,
                    direction=direction,
                    confidence=confidence,
                    horizon=horizon,
                    rationale=rationale,
                    risk_note=risk_note,
                )
            )
        return rows


class OilNewsTranslator:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=10,
                follow_redirects=True,
                headers={"User-Agent": "ArbitrageRadar/1.0 oil-news-translator"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()

    async def translate_item(self, item: OilNewsItem) -> tuple[str, str | None]:
        title_zh = item.title_zh or await self.translate_text(item.title)
        summary_zh = item.summary_zh
        summary = self._translatable_summary(item)
        if summary_zh is None and summary is not None:
            try:
                summary_zh = await self.translate_text(summary)
            except Exception:  # noqa: BLE001 - the translated title is enough to alert promptly.
                logger.warning(
                    "failed to translate oil news summary title=%r",
                    item.title,
                    exc_info=True,
                )
        return title_zh, summary_zh

    async def translate_text(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            raise ValueError("translation text must not be empty")
        if re.search(r"[\u3400-\u9fff]", normalized):
            return normalized
        if len(normalized) > TRANSLATION_MAX_CHARS:
            normalized = f"{normalized[:TRANSLATION_MAX_CHARS].rstrip()}..."

        failures: list[str] = []
        translators = (
            ("MyMemory", self._translate_with_mymemory),
            ("Google", self._translate_with_google),
        )
        for provider, translate in translators:
            try:
                translated = unescape((await translate(normalized)).strip())
                if not translated:
                    raise RuntimeError("translation response is empty")
                if not re.search(r"[\u3400-\u9fff]", translated):
                    raise RuntimeError("translation response does not contain Chinese text")
                return translated
            except Exception as exc:  # noqa: BLE001 - try the next translation provider.
                failures.append(f"{provider}: {exc}")
                logger.warning("%s oil news translation failed", provider, exc_info=True)
        raise RuntimeError(f"all translation providers failed: {'; '.join(failures)}")

    async def _translate_with_mymemory(self, text: str) -> str:
        response = await self.client.get(
            MYMEMORY_TRANSLATE_URL,
            params={"q": text, "langpair": "en|zh-CN"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("translation response must be a JSON object")
        status = payload.get("responseStatus")
        if status not in (None, 200, "200"):
            detail = payload.get("responseDetails") or "unknown translation error"
            raise RuntimeError(f"translation failed: {detail}")
        response_data = payload.get("responseData")
        translated = (
            response_data.get("translatedText")
            if isinstance(response_data, dict)
            else None
        )
        if not isinstance(translated, str) or not translated.strip():
            raise RuntimeError("translation response is missing translatedText")
        return translated

    async def _translate_with_google(self, text: str) -> str:
        response = await self.client.get(
            GOOGLE_TRANSLATE_URL,
            params={
                "client": "dict-chrome-ex",
                "sl": "en",
                "tl": "zh-CN",
                "q": text,
            },
        )
        response.raise_for_status()
        payload = response.json()
        translated = payload[0] if isinstance(payload, list) and payload else None
        if not isinstance(translated, str) or not translated.strip():
            raise RuntimeError("translation response is missing translated text")
        return translated

    @staticmethod
    def _translatable_summary(item: OilNewsItem) -> str | None:
        summary = re.sub(r"\s+", " ", item.summary or "").strip()
        if not summary:
            return None
        duplicate_candidates = {
            item.title.casefold(),
            f"{item.title} {item.source}".casefold(),
        }
        return None if summary.casefold() in duplicate_candidates else summary


def build_oil_news_alert(item: OilNewsItem) -> str:
    direction_label = {
        OilNewsDirection.LONG: "做多倾向",
        OilNewsDirection.SHORT: "做空倾向",
        OilNewsDirection.WATCH: "观察",
    }[item.direction]
    severity_label = {
        OilNewsSeverity.CRITICAL: "极重大",
        OilNewsSeverity.HIGH: "严重",
        OilNewsSeverity.MEDIUM: "中等",
        OilNewsSeverity.LOW: "一般",
    }[item.severity]
    lines = [f"【原油重大新闻｜{direction_label}｜{severity_label}】"]
    if item.title_zh:
        lines.append(f"中文标题：{item.title_zh}")
        if item.summary_zh:
            lines.append(f"中文摘要：{item.summary_zh}")
        if item.title_zh.casefold() != item.title.casefold():
            lines.append(f"英文标题：{item.title}")
    else:
        lines.append(f"英文标题：{item.title}")
    lines.extend([
        f"时间：{item.published_at.astimezone(CST):%Y-%m-%d %H:%M:%S} UTC+8",
        f"来源：{item.source}",
        f"判断：{direction_label}（置信度 {item.confidence:.0%}，影响周期 {item.horizon}）",
        f"影响分：{item.impact_score}/100",
    ])
    if item.rationale:
        lines.append("依据：")
        lines.extend(f"- {reason}" for reason in item.rationale)
    if item.market and item.market.price is not None:
        change = (
            f"，近1小时 {item.market.change_1h_pct:+.2f}%"
            if item.market.change_1h_pct is not None
            else ""
        )
        confirmation = "待确认"
        if item.market.change_1h_pct is not None and abs(item.market.change_1h_pct) >= 0.3:
            agrees = (
                item.direction == OilNewsDirection.LONG and item.market.change_1h_pct > 0
            ) or (
                item.direction == OilNewsDirection.SHORT and item.market.change_1h_pct < 0
            )
            if item.direction != OilNewsDirection.WATCH:
                confirmation = "行情确认" if agrees else "行情背离"
        lines.append(f"行情：CLUSDT {item.market.price:.2f}{change}（{confirmation}）")
    lines.extend((f"反转条件：{item.risk_note}", f"原文：{item.url}"))
    return "\n".join(lines)


class OilNewsMonitor:
    def __init__(
        self,
        repository,
        provider: OilNewsProvider,
        alert_sender: AlertSender | None = None,
        translator: OilNewsTranslator | None = None,
        now_fn: Callable[[], datetime] = utc_now,
    ):
        self.repository = repository
        self.provider = provider
        self.alert_sender = alert_sender
        self.translator = translator
        self.now_fn = now_fn
        self._poll_lock = asyncio.Lock()

    async def aclose(self) -> None:
        try:
            await self.provider.aclose()
        finally:
            if self.translator is not None:
                await self.translator.aclose()

    async def poll(self, settings: OilNewsSettings) -> OilNewsRefreshResult:
        async with self._poll_lock:
            return await self._poll_once(settings)

    async def _poll_once(self, settings: OilNewsSettings) -> OilNewsRefreshResult:
        had_rows = await self.repository.has_any()
        items, errors = await self.provider.fetch()
        market = await self.provider.fetch_market_snapshot()
        inserted_count = 0
        alerted_count = 0
        now = self.now_fn()
        for item in sorted(items, key=lambda row: row.published_at):
            item.market = market
            inserted = await self.repository.create_if_new(item)
            retrying = inserted is None
            if retrying:
                existing = await self.repository.get_by_fingerprint(item.fingerprint)
                if existing is None or existing.alert_status not in {"pending", "failed"}:
                    continue
                item = existing
            else:
                inserted_count += 1
            should_alert = (
                settings.feishu_notifications_enabled
                and self.alert_sender is not None
                and (had_rows or settings.bootstrap_alerts_enabled)
                and SEVERITY_RANK[item.severity] >= SEVERITY_RANK[settings.alert_min_severity]
                and item.published_at <= now + timedelta(minutes=5)
                and now - item.published_at <= timedelta(minutes=settings.alert_max_age_minutes)
            )
            if not should_alert:
                if not retrying:
                    status = "skipped_bootstrap" if not had_rows else "filtered"
                    await self.repository.update_alert_status(item.id, status)
                continue
            if self.translator is not None:
                try:
                    title_zh, summary_zh = await self.translator.translate_item(item)
                    if title_zh != item.title_zh or summary_zh != item.summary_zh:
                        item.title_zh = title_zh
                        item.summary_zh = summary_zh
                        await self.repository.update_translation(
                            item.id,
                            title_zh=title_zh,
                            summary_zh=summary_zh,
                        )
                except Exception:  # noqa: BLE001 - breaking news must go out immediately.
                    logger.warning(
                        "failed to translate oil news alert id=%s; sending English original",
                        item.id,
                        exc_info=True,
                    )
            try:
                result = self.alert_sender(build_oil_news_alert(item))
                if isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 - one notifier failure must not stop collection.
                logger.exception("failed to send oil news alert id=%s", item.id)
                await self.repository.update_alert_status(item.id, "failed")
                continue
            await self.repository.update_alert_status(item.id, "sent", alerted_at=now)
            alerted_count += 1
        return OilNewsRefreshResult(
            fetched_count=len(items),
            relevant_count=len(items),
            inserted_count=inserted_count,
            alerted_count=alerted_count,
            market=market,
            errors=errors,
        )


async def run_oil_news_loop(
    monitor: OilNewsMonitor,
    settings_loader: SettingsLoader,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        interval = 300
        try:
            settings = await settings_loader()
            interval = settings.poll_interval_seconds
            if settings.enabled:
                await monitor.poll(settings)
        except Exception:  # noqa: BLE001 - retry on the next configured interval.
            logger.exception("oil news polling failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
