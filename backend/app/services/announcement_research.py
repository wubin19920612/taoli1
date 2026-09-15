from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

from app.exchanges.bitget import KNOWN_RTOKEN_SPOT_SYMBOLS, rtoken_spot_symbols
from app.models.announcement import (
    AnnouncementAssetResearch,
    AnnouncementKind,
    AnnouncementResearchSource,
    ExchangeAnnouncement,
)

logger = logging.getLogger(__name__)

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
BITGET_SPOT_SYMBOLS_URL = "https://api.bitget.com/api/v2/spot/public/symbols"

MAX_RESEARCH_SUMMARY_LENGTH = 520
MAX_RESEARCH_BUSINESS_LENGTH = 700
MAX_RESEARCH_SOURCES = 3
MAX_SEARCH_RESULTS = 4
DEFAULT_ASSET_TIMEOUT_SECONDS = 18.0


def utc_now() -> datetime:
    return datetime.now(UTC)


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _truncate(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    text = _clean_text(value)
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _symbol_base(symbol: str) -> str:
    value = symbol.strip().upper().replace("-", "").replace("_", "")
    if "/" in value:
        value = value.split("/", 1)[0]
    for quote_asset in ("USDⓈ", "FDUSD", "USDT", "USDC", "USD1", "USD", "BTC", "ETH", "BNB"):
        if value.endswith(quote_asset) and len(value) > len(quote_asset):
            return value[: -len(quote_asset)]
    return value


def _research_context(announcement: ExchangeAnnouncement) -> str:
    return " ".join(
        item
        for item in (
            announcement.title,
            announcement.category or "",
            announcement.market_type or "",
        )
        if item
    ).lower()


def _normalized_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace("-", "").replace("_", "").replace("/", "")


def _is_bitget_rtoken_shape(announcement: ExchangeAnnouncement, symbol: str) -> bool:
    normalized = _normalized_symbol(symbol)
    return (
        announcement.exchange == "bitget"
        and "spot" in (announcement.market_type or "").lower()
        and len(normalized) > len("RUSDT")
        and normalized.startswith("R")
        and normalized.endswith("USDT")
    )


def _is_stock_context(announcement: ExchangeAnnouncement, symbol: str) -> bool:
    context = _research_context(announcement)
    if any(
        token in context
        for token in (
            "stock",
            "stocks",
            "equity",
            "equities",
            "bstock",
            "tradfi",
            "cfd",
            "share",
            "股票",
            "指数",
            "index",
            "etf",
        )
    ):
        return True
    return _normalized_symbol(symbol) in KNOWN_RTOKEN_SPOT_SYMBOLS and _is_bitget_rtoken_shape(
        announcement,
        symbol,
    )


def _is_index_context(announcement: ExchangeAnnouncement, symbol: str) -> bool:
    context = _research_context(announcement)
    base = _symbol_base(symbol)
    return "index" in context or "指数" in context or base in {
        "DJI",
        "JP225",
        "N225",
        "NAS100",
        "NDX",
        "SP500",
        "SPX",
        "US30",
        "US500",
    }


def _is_binance_bstock_context(announcement: ExchangeAnnouncement) -> bool:
    context = _research_context(announcement)
    return announcement.exchange == "binance" and any(
        token in context for token in ("bstock", "tokenized security", "tokenized securities")
    )


def _binance_bstock_canonical_symbol(announcement: ExchangeAnnouncement, symbol: str) -> str | None:
    if not _is_binance_bstock_context(announcement):
        return None
    value = _symbol_base(symbol)
    if len(value) > 2 and value.endswith("B"):
        return value[:-1]
    return value


def _stock_canonical_symbol(announcement: ExchangeAnnouncement, symbol: str) -> str:
    return (
        _bitget_rtoken_canonical_symbol(announcement, symbol)
        or _binance_bstock_canonical_symbol(announcement, symbol)
        or _symbol_base(symbol)
    )


def _bitget_rtoken_canonical_symbol(announcement: ExchangeAnnouncement, symbol: str) -> str | None:
    if not _is_bitget_rtoken_shape(announcement, symbol):
        return None
    value = _symbol_base(symbol)
    if value.startswith("R") and len(value) > 1:
        return value[1:]
    return None


def _title_asset_hints(title: str) -> dict[str, str]:
    """Extract company/project names written as `Name (TICKER)` in an announcement."""
    hints: dict[str, str] = {}
    for match in re.finditer(
        r"(?P<name>[A-Za-z][A-Za-z0-9&.'’/\- ]{1,90}?)\s*\(\s*(?P<symbol>[A-Z][A-Z0-9]{1,14})\s*\)",
        title,
    ):
        name = re.sub(
            r"^.*\b(?:adds?|lists?|launch(?:es)?|will\s+(?:add|list|launch)|to\s+list|and)\s+",
            "",
            match.group("name"),
            flags=re.I,
        )
        name = re.sub(r"\s+", " ", name).strip(" ,;:-")
        symbol = match.group("symbol").upper()
        if name and symbol:
            hints[symbol] = name
    return hints


def _decode_search_url(value: str) -> str:
    href = unescape(value)
    parsed = urlparse(href)
    if parsed.path.endswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [])
        if target:
            return unquote(target[0])
    return href


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.titles: list[tuple[str, str]] = []
        self.snippets: list[str] = []
        self._capture: str | None = None
        self._capture_tag: str | None = None
        self._capture_href = ""
        self._capture_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if "result__a" in classes or "result__snippet" in classes:
            self._capture = "title" if "result__a" in classes else "snippet"
            self._capture_tag = tag
            self._capture_href = _decode_search_url(attributes.get("href") or "")
            self._capture_parts = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture is None or tag != self._capture_tag:
            return
        text = _clean_text("".join(self._capture_parts))
        if text:
            if self._capture == "title":
                self.titles.append((text, self._capture_href))
            else:
                self.snippets.append(text)
        self._capture = None
        self._capture_tag = None
        self._capture_href = ""
        self._capture_parts = []


class AnnouncementResearchService:
    """Best-effort public research for new listing announcements.

    The service deliberately uses public, unauthenticated endpoints. A failed lookup
    produces a not-found result instead of preventing the announcement alert.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        now_fn: Callable[[], datetime] | None = None,
        max_concurrency: int = 3,
        asset_timeout_seconds: float = DEFAULT_ASSET_TIMEOUT_SECONDS,
    ) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0, read=7.0),
            follow_redirects=True,
            headers={"User-Agent": "taoli1-radar/0.1"},
        )
        self._owns_client = client is None
        self._now_fn = now_fn or utc_now
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._asset_timeout_seconds = max(1.0, asset_timeout_seconds)
        self._cache: dict[str, AnnouncementAssetResearch] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def research(self, announcement: ExchangeAnnouncement) -> list[AnnouncementAssetResearch]:
        if announcement.kind != AnnouncementKind.LISTING or not announcement.symbols:
            return []

        symbols: list[str] = []
        seen: set[str] = set()
        for symbol in announcement.symbols:
            normalized = symbol.strip().upper()
            if normalized and normalized not in seen:
                symbols.append(normalized)
                seen.add(normalized)

        title_hints = _title_asset_hints(announcement.title)
        results = await asyncio.gather(
            *(
                self._research_one_with_timeout(
                    announcement,
                    symbol,
                    title_hint=title_hints.get(_symbol_base(symbol)),
                )
                for symbol in symbols
            ),
            return_exceptions=True,
        )
        researched: list[AnnouncementAssetResearch] = []
        for symbol, result in zip(symbols, results, strict=True):
            if isinstance(result, AnnouncementAssetResearch):
                researched.append(result)
                continue
            logger.warning("asset research failed for %s %s: %s", announcement.exchange, symbol, result)
            researched.append(self._not_found(symbol))
        return researched

    async def _research_one_with_timeout(
        self,
        announcement: ExchangeAnnouncement,
        symbol: str,
        *,
        title_hint: str | None,
    ) -> AnnouncementAssetResearch:
        try:
            return await asyncio.wait_for(
                self._research_one(announcement, symbol, title_hint=title_hint),
                timeout=self._asset_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("asset research timed out for %s", symbol)
            canonical_symbol = (
                _stock_canonical_symbol(announcement, symbol)
                if _is_stock_context(announcement, symbol)
                else _symbol_base(symbol)
            )
            return self._not_found(symbol, canonical_symbol)

    async def _research_one(
        self,
        announcement: ExchangeAnnouncement,
        symbol: str,
        *,
        title_hint: str | None = None,
    ) -> AnnouncementAssetResearch:
        canonical_symbol = _symbol_base(symbol)
        async with self._semaphore:
            try:
                stock_context = _is_stock_context(announcement, symbol)
                if not stock_context and _is_bitget_rtoken_shape(announcement, symbol):
                    verified_rtoken = await self._is_verified_bitget_rtoken(announcement, symbol)
                    if verified_rtoken is None:
                        return self._not_found(symbol, canonical_symbol)
                    stock_context = verified_rtoken
                canonical_symbol = (
                    _stock_canonical_symbol(announcement, symbol)
                    if stock_context
                    else _symbol_base(symbol)
                )
                cache_key = self._cache_key(announcement, symbol, canonical_symbol)
                cached = self._cache.get(cache_key)
                if cached is not None:
                    return cached.model_copy(deep=True)
                if stock_context:
                    result = await self._research_stock(
                        symbol,
                        canonical_symbol,
                        is_index=_is_index_context(announcement, symbol),
                        name_hint=title_hint,
                    )
                    if result.status == "found":
                        self._cache[cache_key] = result
                        return result.model_copy(deep=True)
                    if result.status == "partial":
                        return result.model_copy(deep=True)
                    result = self._not_found(symbol, canonical_symbol)
                    return result.model_copy(deep=True)

                try:
                    result = await self._research_crypto(symbol, canonical_symbol)
                except Exception:
                    logger.info("CoinGecko lookup failed for %s", canonical_symbol, exc_info=True)
                    result = self._not_found(symbol, canonical_symbol)
                if result.status == "found":
                    self._cache[cache_key] = result
                    return result.model_copy(deep=True)
                if result.status == "partial":
                    return result.model_copy(deep=True)

                if not stock_context:
                    result = await self._research_web_fallback(
                        symbol,
                        canonical_symbol,
                        asset_type="crypto",
                        name_hint=title_hint,
                    )
            except Exception:
                logger.info("public asset research request failed for %s", symbol, exc_info=True)
                result = self._not_found(symbol, canonical_symbol)

        if result.status == "found":
            self._cache[cache_key] = result
        return result.model_copy(deep=True)

    async def _is_verified_bitget_rtoken(
        self,
        announcement: ExchangeAnnouncement,
        symbol: str,
    ) -> bool | None:
        if not _is_bitget_rtoken_shape(announcement, symbol):
            return False
        normalized = _normalized_symbol(symbol)
        if normalized in KNOWN_RTOKEN_SPOT_SYMBOLS:
            return True
        try:
            payload = await self._get_json(BITGET_SPOT_SYMBOLS_URL)
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            return normalized in rtoken_spot_symbols(rows if isinstance(rows, list) else [])
        except Exception:
            logger.info("Bitget RToken metadata lookup failed for %s", symbol, exc_info=True)
            return None

    def _cache_key(self, announcement: ExchangeAnnouncement, symbol: str, canonical: str) -> str:
        return ":".join(
            (
                announcement.exchange,
                (announcement.market_type or "").lower(),
                symbol,
                canonical,
            )
        )

    async def _get_json(self, url: str, *, params: dict[str, str] | None = None) -> object:
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def _get_text(self, url: str, *, params: dict[str, str] | None = None) -> str:
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.text

    async def _research_crypto(
        self,
        display_symbol: str,
        canonical_symbol: str,
    ) -> AnnouncementAssetResearch:
        searched_at = self._now_fn()
        payload = await self._get_json(
            f"{COINGECKO_BASE_URL}/search",
            params={"query": canonical_symbol},
        )
        coins = payload.get("coins", []) if isinstance(payload, dict) else []
        rows = [row for row in coins if isinstance(row, dict)]
        exact = [
            row
            for row in rows
            if str(row.get("symbol", "")).strip().upper() == canonical_symbol.upper()
        ]
        candidates = exact or [
            row
            for row in rows
            if str(row.get("name", "")).strip().upper() == canonical_symbol.upper()
        ]
        if not candidates:
            return self._not_found(display_symbol, canonical_symbol, searched_at=searched_at)

        candidates.sort(
            key=lambda row: (
                row.get("market_cap_rank") is None,
                row.get("market_cap_rank") or 10**9,
            )
        )
        coin = candidates[0]
        coin_id = _clean_text(coin.get("id"))
        name = _clean_text(coin.get("name")) or canonical_symbol
        sources = [
            AnnouncementResearchSource(
                title="CoinGecko",
                url=f"https://www.coingecko.com/en/coins/{quote(coin_id, safe='')}",
            )
        ]
        summary: str | None = None
        business: str | None = None
        try:
            detail = await self._get_json(
                f"{COINGECKO_BASE_URL}/coins/{quote(coin_id, safe='')}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "false",
                    "community_data": "false",
                    "developer_data": "false",
                },
            )
            if isinstance(detail, dict):
                description = detail.get("description")
                if isinstance(description, dict):
                    business = _truncate(description.get("en"), MAX_RESEARCH_BUSINESS_LENGTH)
                categories = detail.get("categories")
                if isinstance(categories, list):
                    category_text = ", ".join(
                        _clean_text(item) for item in categories if _clean_text(item)
                    )
                    if category_text:
                        summary = f"分类：{_truncate(category_text, MAX_RESEARCH_SUMMARY_LENGTH)}"
                links = detail.get("links")
                homepages = links.get("homepage") if isinstance(links, dict) else None
                if isinstance(homepages, list):
                    homepage = next((_clean_text(item) for item in homepages if _clean_text(item)), None)
                    if homepage and homepage.startswith(("http://", "https://")):
                        sources.append(
                            AnnouncementResearchSource(title="项目官网", url=homepage)
                        )
        except Exception:
            logger.info("CoinGecko detail lookup failed for %s", canonical_symbol, exc_info=True)

        if business is None and summary is None:
            summary = f"{name}，CoinGecko 将其识别为加密资产。"
        elif summary is None:
            summary = f"{name} 的公开项目介绍。"
        return AnnouncementAssetResearch(
            symbol=display_symbol,
            canonical_symbol=canonical_symbol,
            asset_type="crypto",
            name=name,
            summary=summary,
            business=business,
            sources=sources[:MAX_RESEARCH_SOURCES],
            status="found" if business else "partial",
            searched_at=searched_at,
        )

    async def _research_web_fallback(
        self,
        display_symbol: str,
        canonical_symbol: str,
        *,
        asset_type: str,
        name_hint: str | None = None,
    ) -> AnnouncementAssetResearch:
        searched_at = self._now_fn()
        query_name = name_hint or canonical_symbol
        query_suffix = "crypto project official business" if asset_type == "crypto" else "company business"
        results = await self._search_web(f'"{query_name}" {canonical_symbol} {query_suffix}')
        relevant_results = [
            item
            for item in results
            if self._search_result_mentions(item, canonical_symbol, name_hint)
        ]
        if not relevant_results:
            return self._not_found(display_symbol, canonical_symbol, searched_at=searched_at)

        name = _clean_text(name_hint) or self._name_from_search_results(
            relevant_results,
            canonical_symbol,
        )
        business = self._best_business_snippet(relevant_results)
        sources = [
            AnnouncementResearchSource(title=title, url=url)
            for title, url, _ in relevant_results[:MAX_RESEARCH_SOURCES]
        ]
        kind_label = "加密货币项目" if asset_type == "crypto" else "公开标的"
        summary = f"{name}，公开搜索资料显示其为{kind_label}。"
        return AnnouncementAssetResearch(
            symbol=display_symbol,
            canonical_symbol=canonical_symbol,
            asset_type=asset_type,
            name=name or None,
            summary=summary,
            business=business,
            sources=sources,
            status="partial",
            searched_at=searched_at,
        )

    async def _research_stock(
        self,
        display_symbol: str,
        canonical_symbol: str,
        *,
        is_index: bool,
        name_hint: str | None = None,
    ) -> AnnouncementAssetResearch:
        searched_at = self._now_fn()
        chart_candidates = self._yahoo_chart_candidates(canonical_symbol, is_index=is_index)
        meta: dict[str, object] | None = None
        chart_url: str | None = None
        for candidate in chart_candidates:
            url = f"{YAHOO_CHART_BASE_URL}/{quote(candidate, safe='')}"
            try:
                payload = await self._get_json(
                    url,
                    params={"range": "1d", "interval": "1d"},
                )
            except Exception:
                continue
            result = payload.get("chart", {}).get("result") if isinstance(payload, dict) else None
            first = result[0] if isinstance(result, list) and result else None
            candidate_meta = first.get("meta") if isinstance(first, dict) else None
            if isinstance(candidate_meta, dict):
                meta = candidate_meta
                chart_url = url
                break

        instrument_type = _clean_text(meta.get("instrumentType")) if meta else ""
        asset_type = "index" if is_index or instrument_type.upper() == "INDEX" else "stock"
        name = _clean_text((meta or {}).get("longName") or (meta or {}).get("shortName")) or _clean_text(
            name_hint
        )
        query_name = name or canonical_symbol
        search_results = await self._search_web(
            f'"{query_name}" {canonical_symbol} '
            f"{'index' if asset_type == 'index' else 'company business'}"
        )
        relevant_results = [
            item
            for item in search_results
            if self._search_result_mentions(item, canonical_symbol, name)
        ]
        business = self._best_business_snippet(relevant_results)
        sources: list[AnnouncementResearchSource] = []
        if chart_url:
            chart_symbol = chart_url.rsplit("/", 1)[-1]
            sources.append(
                AnnouncementResearchSource(
                    title="Yahoo Finance",
                    url=f"https://finance.yahoo.com/quote/{quote(unquote(chart_symbol), safe='')}",
                )
            )
        for title, url, _ in relevant_results[: MAX_RESEARCH_SOURCES - len(sources)]:
            if url and url not in {source.url for source in sources}:
                sources.append(AnnouncementResearchSource(title=title, url=url))

        if not name and relevant_results:
            name = self._name_from_search_results(relevant_results, canonical_symbol)
        summary = None
        if name:
            kind_label = "指数" if asset_type == "index" else "股票"
            summary = f"{name}，公开资料识别为{kind_label}标的。"
        has_evidence = bool(meta or relevant_results)
        status = (
            "found"
            if meta and name and business
            else "partial"
            if has_evidence and name
            else "not_found"
        )
        return AnnouncementAssetResearch(
            symbol=display_symbol,
            canonical_symbol=canonical_symbol,
            asset_type=asset_type,
            name=name or None,
            summary=summary,
            business=business,
            sources=sources[:MAX_RESEARCH_SOURCES],
            status=status,
            searched_at=searched_at,
        )

    @staticmethod
    def _yahoo_chart_candidates(symbol: str, *, is_index: bool) -> list[str]:
        if not is_index:
            return [symbol]
        aliases = {
            "JP225": "^N225",
            "N225": "^N225",
            "SP500": "^GSPC",
            "SPX": "^GSPC",
            "NDX": "^NDX",
            "DJI": "^DJI",
            "US30": "^DJI",
            "NAS100": "^NDX",
            "US500": "^GSPC",
        }
        return [aliases.get(symbol.upper(), symbol), symbol]

    async def _search_web(self, query: str) -> list[tuple[str, str, str]]:
        try:
            html = await self._get_text(
                DUCKDUCKGO_HTML_URL,
                params={"q": query},
            )
        except Exception:
            return []
        parser = _DuckDuckGoParser()
        parser.feed(html)
        results: list[tuple[str, str, str]] = []
        for index, (title, url) in enumerate(parser.titles[:MAX_SEARCH_RESULTS]):
            snippet = parser.snippets[index] if index < len(parser.snippets) else ""
            if not url.startswith(("http://", "https://")):
                continue
            results.append((title, url, snippet))
        return results

    @staticmethod
    def _best_business_snippet(results: list[tuple[str, str, str]]) -> str | None:
        for _, _, snippet in results:
            text = _truncate(snippet, MAX_RESEARCH_BUSINESS_LENGTH)
            if text and len(text) >= 35:
                return text
        return _truncate(results[0][2], MAX_RESEARCH_BUSINESS_LENGTH) if results else None

    @staticmethod
    def _search_result_mentions(
        result: tuple[str, str, str],
        canonical_symbol: str,
        name_hint: str | None,
    ) -> bool:
        haystack = " ".join(result).upper()
        symbol = canonical_symbol.upper()
        if len(symbol) >= 3 and symbol in haystack:
            return True
        if name_hint:
            words = [
                word.upper()
                for word in re.findall(r"[A-Za-z][A-Za-z0-9&.'’-]{2,}", name_hint)
            ]
            return bool(words) and sum(word in haystack for word in words) >= max(1, len(words) // 2)
        return False

    @staticmethod
    def _name_from_search_results(
        results: list[tuple[str, str, str]],
        fallback: str,
    ) -> str:
        if not results:
            return fallback
        title = _clean_text(results[0][0])
        title = re.split(r"\s+[|:-]\s+", title, maxsplit=1)[0].strip()
        return title or fallback

    def _not_found(
        self,
        symbol: str,
        canonical_symbol: str | None = None,
        *,
        searched_at: datetime | None = None,
    ) -> AnnouncementAssetResearch:
        return AnnouncementAssetResearch(
            symbol=symbol,
            canonical_symbol=canonical_symbol or _symbol_base(symbol),
            asset_type="unknown",
            status="not_found",
            searched_at=searched_at or self._now_fn(),
        )
