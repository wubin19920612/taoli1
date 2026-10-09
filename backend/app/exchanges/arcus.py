from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any
from urllib.parse import quote

from websockets.asyncio.client import connect as websocket_connect

from app.exchanges.base import ExchangeAdapter, order_book_snapshot, parse_float, utc_now
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot

ARCUS_URL = "https://api.arcus.xyz/v1"
ARCUS_WS_URL = "wss://api.arcus.xyz/v1/ws"
ARCUS_DATA_SOURCE = "Arcus public markets + WebSocket bbo (USD)"


def arcus_float(value: Any) -> float | None:
    parsed = parse_float(value)
    return parsed if parsed is not None and isfinite(parsed) else None


def arcus_timestamp(value: Any, *, microseconds: bool = True) -> datetime | None:
    parsed = arcus_float(value)
    if parsed is None or parsed <= 0:
        return None
    try:
        return datetime.fromtimestamp(parsed / 1_000_000 if microseconds else parsed, UTC)
    except (OSError, OverflowError, ValueError):
        return None


def arcus_market_symbol(row: Any) -> tuple[str, str] | None:
    if not isinstance(row, dict) or row.get("type") != "PERPETUAL":
        return None
    base = str(row.get("baseAsset", "")).strip().upper()
    raw_symbol = str(row.get("marketDisplayName", "")).strip().upper()
    if not base or not base.isascii() or not base.isalnum():
        return None
    if row.get("quoteAsset") != "USD" or raw_symbol != f"{base}-USD":
        return None
    return f"{base}USDT", base


def arcus_market_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
        raise RuntimeError("invalid Arcus markets response")
    return [
        row for row in payload["markets"]
        if arcus_market_symbol(row) is not None and row.get("status") == "ONLINE"
    ]


def arcus_bbo_prices(payload: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(payload, dict):
        return None
    levels = [payload.get("bestBid"), payload.get("bestAsk")]
    if not all(isinstance(level, dict) for level in levels):
        return None
    bid, ask = [arcus_float(level.get("price")) for level in levels]
    bid_size, ask_size = [arcus_float(level.get("size")) for level in levels]
    if any(value is None or value <= 0 for value in (bid, ask, bid_size, ask_size)):
        return None
    if bid >= ask:
        return None
    return bid, ask, bid_size, ask_size


async def arcus_bbos(
    raw_symbols: list[str], *, timeout_seconds: float = 12,
) -> dict[str, dict[str, Any]]:
    pending = set(raw_symbols)
    if not pending:
        return {}
    snapshots: dict[str, dict[str, Any]] = {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    async with websocket_connect(
        ARCUS_WS_URL, open_timeout=min(timeout_seconds, 10), close_timeout=3,
    ) as websocket:
        for raw_symbol in dict.fromkeys(raw_symbols):
            await websocket.send(json.dumps({
                "type": "subscribe", "channel": "bbo", "id": raw_symbol,
            }))
        while pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                message = json.loads(await asyncio.wait_for(websocket.recv(), remaining))
            except TimeoutError:
                break
            if not isinstance(message, dict):
                continue
            if message.get("type") == "error":
                raise RuntimeError(f"Arcus WebSocket subscription error: {message}")
            if message.get("type") not in {"subscribed", "channel_data"}:
                continue
            raw_symbol = message.get("id")
            contents = message.get("contents")
            if (
                message.get("channel") == "bbo" and isinstance(raw_symbol, str)
                and raw_symbol in pending
                and isinstance(contents, dict)
            ):
                snapshots[raw_symbol] = contents
                pending.remove(raw_symbol)
    if pending:
        raise RuntimeError(f"missing Arcus BBO snapshots: {', '.join(sorted(pending))}")
    return snapshots


class ArcusAdapter(ExchangeAdapter):
    name = "arcus"
    book_refresh_seconds = 12
    details_refresh_seconds = 60

    def __init__(self, client=None):
        super().__init__(client)
        self._details: tuple[datetime, list[dict[str, Any]]] | None = None
        self._cached: tuple[datetime, list[MarketSnapshot]] | None = None

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        return []

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        now = utc_now()
        if self._cached and now - self._cached[0] < timedelta(seconds=self.book_refresh_seconds):
            return self._cached[1]
        if not self._details or now - self._details[0] >= timedelta(
            seconds=self.details_refresh_seconds,
        ):
            rows = arcus_market_rows(await self.get_json(f"{ARCUS_URL}/markets"))
            self._details = now, rows
        rows = self._details[1]
        books = await arcus_bbos([row["marketDisplayName"] for row in rows])
        snapshots: list[MarketSnapshot] = []
        for row in rows:
            raw_symbol = row["marketDisplayName"]
            book = books.get(raw_symbol)
            prices = arcus_bbo_prices(book)
            if prices is None:
                continue
            symbol, base = arcus_market_symbol(row)
            bid, ask, bid_size, ask_size = prices
            funding = arcus_float(row.get("fundingRate"))
            next_funding = arcus_float(row.get("nextFundingRate"))
            snapshots.append(MarketSnapshot(
                exchange=self.name, market_type=MarketType.FUTURE,
                symbol=symbol, base=base, quote="USDT", raw_symbol=raw_symbol,
                bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size,
                volume_24h_usdt=arcus_float(row.get("volume24hNotional")),
                funding_rate_pct=funding * 100 if funding is not None else None,
                funding_next_rate_pct=next_funding * 100 if next_funding is not None else None,
                funding_interval_hours=1,
                funding_next_time=arcus_timestamp(row.get("nextFundingAt"), microseconds=False),
                mark_price=arcus_float(row.get("markPrice")),
                index_price=arcus_float(row.get("oraclePrice")),
                contract_size_multiplier=1, data_source=ARCUS_DATA_SOURCE,
                timestamp=now, upstream_timestamp=arcus_timestamp(book.get("timestamp")),
            ))
        self._cached = now, snapshots
        return snapshots

    async def fetch_order_book(
        self, symbol: str, market_type: MarketType, raw_symbol: str, limit: int = 20,
    ) -> OrderBookSnapshot | None:
        if market_type != MarketType.FUTURE:
            return None
        payload = await self.get_json(
            f"{ARCUS_URL}/l2OrderBook/{quote(raw_symbol, safe='')}"
            f"?nLevels={max(1, min(limit, 100))}"
        )
        if not isinstance(payload, dict):
            raise RuntimeError("invalid Arcus order book response")
        return order_book_snapshot(
            exchange=self.name, market_type=market_type, symbol=symbol, raw_symbol=raw_symbol,
            bids=payload.get("bids"), asks=payload.get("asks"),
            timestamp=arcus_timestamp(payload.get("timestamp")),
        )
