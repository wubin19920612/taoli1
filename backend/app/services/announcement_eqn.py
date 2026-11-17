"""EquationNews exchange notices via its public MCP endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from app.models.announcement import ExchangeAnnouncement
from app.services.announcements import (
    AnnouncementProvider,
    _announcement_source_error,
    announcement_http_client,
    classify_announcement,
    infer_market_type,
    infer_symbols,
    utc_now,
)

logger = logging.getLogger(__name__)
CHANNELS = ("binance", "korea", "alpha", "tier2", "tier3")
SOURCE_EXCHANGES = {
    "binance en": "binance",
    "binance alpha": "binance",
    "bitget announcements": "bitget",
    "bybit announcements": "bybit",
    "okx announcements": "okx",
    "gate.io announcements": "gate",
    "hyperliquid perp listing": "hyperliquid",
    "mexc announcements": "mexc",
    "upbit": "upbit",
    "upbit listing": "upbit",
    "bithumb": "bithumb",
    "bithumb listing": "bithumb",
    "aster perp listing": "aster",
    "aster spot listing": "aster",
}


class EqnRpcError(ValueError):
    """A JSON-RPC error permits one session reinitialization."""


class EqnNewsClient:
    endpoint = "https://api.eqn.news/public/mcp"

    def __init__(
        self, client: httpx.AsyncClient | None = None, *,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now_fn: Callable[[], datetime] = utc_now,
    ) -> None:
        self.client = client or announcement_http_client()
        self._owns_client = client is None
        self._monotonic = monotonic_fn
        self._sleep = sleep_fn
        self._now = now_fn
        self._lock = asyncio.Lock()
        self._session_id: str | None = None
        self._initialized = False
        self._request_id = 0
        self._next_request_at = 0.0
        self._backoff_until = 0.0

    @staticmethod
    def _decode(response: httpx.Response) -> dict:
        if "text/event-stream" not in response.headers.get("content-type", "").lower():
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid eqn JSON-RPC response")
            return payload
        # An SSE event can contain multiple data lines and CRLF delimiters.
        for event in response.text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(
                line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:")
            )
            if not data:
                continue
            payload = json.loads(data)
            if isinstance(payload, dict) and ("result" in payload or "error" in payload):
                return payload
        raise ValueError("Missing eqn JSON-RPC SSE response")

    def _retry_after(self, response: httpx.Response) -> float:
        value = response.headers.get("Retry-After", "1")
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - self._now()).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = 1.0
        return max(1.0, seconds) if math.isfinite(seconds) else 1.0

    async def _rpc(self, method: str, params: dict) -> dict:
        if self._monotonic() < self._backoff_until:
            raise ValueError("eqn Retry-After backoff active")
        delay = self._next_request_at - self._monotonic()
        if delay > 0:
            await self._sleep(delay)
        # Spacing all calls (including initialize/retries) also keeps this client
        # below the shared 120 requests / 10 seconds limit.
        self._next_request_at = self._monotonic() + 0.21
        self._request_id += 1
        headers = {"Accept": "application/json, text/event-stream"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._initialized:
            headers["MCP-Protocol-Version"] = "2025-03-26"
        response = await self.client.post(self.endpoint, headers=headers, json={
            "jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params,
        })
        if response.status_code == 429:
            self._backoff_until = self._monotonic() + self._retry_after(response)
        response.raise_for_status()
        payload = self._decode(response)
        if "error" in payload:
            raise EqnRpcError("eqn JSON-RPC error")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise TypeError("Missing eqn JSON-RPC result object")
        if response.headers.get("Mcp-Session-Id"):
            self._session_id = response.headers["Mcp-Session-Id"]
        return result

    async def _initialize(self) -> None:
        self._session_id = None
        self._initialized = False
        await self._rpc("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "taoli1-radar", "version": "0.1"},
        })
        self._initialized = True

    async def list_news(self, channel: str, *, limit: int = 20, before: int | None = None) -> list[dict]:
        arguments = {"channel": channel, "limit": limit}
        if before is not None:
            arguments["before"] = before
        async with self._lock:
            for attempt in range(2):
                try:
                    if not self._initialized:
                        await self._initialize()
                    result = await self._rpc("tools/call", {
                        "name": "list_news", "arguments": arguments,
                    })
                    break
                except (httpx.HTTPStatusError, EqnRpcError) as exc:
                    invalid_session = isinstance(exc, EqnRpcError) or (
                        400 <= exc.response.status_code < 500 and exc.response.status_code != 429
                    )
                    if invalid_session:
                        self._initialized = False
                        self._session_id = None
                    if not invalid_session or attempt:
                        raise
            if result.get("isError"):
                raise ValueError("eqn list_news returned isError")
            content = result.get("content")
            if not isinstance(content, list) or not content or not isinstance(content[0], dict):
                raise ValueError("Missing eqn list_news content")
            payload = json.loads(content[0].get("text", ""))
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise TypeError("Missing eqn list_news items list")
            return payload["items"]

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class EqnAnnouncementProvider(AnnouncementProvider):
    exchange = "eqn"
    source = "eqn"

    def __init__(
        self, client: EqnNewsClient | None = None, *, enabled: bool = True,
        poll_interval_seconds: float = 10.0, now_fn: Callable[[], datetime] = utc_now,
    ) -> None:
        self.client = client or EqnNewsClient()
        self.enabled = enabled
        self.poll_interval_seconds = max(5.0, poll_interval_seconds)
        self._now = now_fn
        self.last_source_articles: dict[str, dict[str, str]] = {}
        self.last_source_errors: dict[str, str] = {}

    def _announcement(self, item: dict, channel: str, exchange: str) -> ExchangeAnnouncement:
        title_zh = str(item.get("zh") or "").strip()
        title = str(item.get("en") or "").strip() or title_zh
        url = str(item.get("source_url") or "").strip()
        if not url or urlsplit(url).hostname in {"api.eqn.news", "eqn.news"}:
            url = str(item.get("url") or "").strip()
        published_at = datetime.fromisoformat(str(item["time"]))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        return ExchangeAnnouncement(
            exchange=exchange, source=self.source, announcement_id=f"eqn-{item['no']}",
            title=title, title_zh=title_zh or None, url=url, category=channel,
            symbols=item.get("coins") or infer_symbols(title), kind=classify_announcement(title),
            market_type=infer_market_type(title), published_at=published_at.astimezone(UTC),
            fetched_at=self._now(),
        )

    async def fetch(self) -> list[ExchangeAnnouncement]:
        self.last_source_articles = {}
        self.last_source_errors = {}
        rows: dict[tuple[str, str], ExchangeAnnouncement] = {}
        skipped: Counter[str] = Counter()
        for channel in CHANNELS:
            try:
                items = await self.client.list_news(channel, limit=20)
            except Exception as exc:  # noqa: BLE001 - retain other channels' discoveries
                self.last_source_errors[channel] = _announcement_source_error(exc)
                continue
            articles: dict[str, str] = {}
            self.last_source_articles[channel] = articles
            for item in items:
                if not isinstance(item, dict):
                    self.last_source_errors[channel] = "InvalidItem"
                    continue
                source = str(item.get("source") or "").strip()
                exchange = SOURCE_EXCHANGES.get(source.lower())
                if exchange is None:
                    skipped[source] += 1
                    continue
                try:
                    row = self._announcement(item, channel, exchange)
                except (ValueError, TypeError, KeyError):
                    self.last_source_errors[channel] = "InvalidItem"
                    continue
                articles[row.announcement_id] = row.published_at.isoformat()
                rows.setdefault((row.exchange, row.announcement_id), row)
        for source, count in skipped.items():
            logger.debug("eqn skipped source=%s count=%d", source, count)
        if not self.last_source_articles or (not rows and len(self.last_source_errors) == len(CHANNELS)):
            raise ValueError("All eqn announcement channels failed")
        return list(rows.values())

    async def aclose(self) -> None:
        await self.client.aclose()
