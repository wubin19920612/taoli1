from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .route_engine import BookLevel, LegSnapshot, Route, RouteLeg, lsk_research_route

D = Decimal


def _decimal(value: Any) -> Decimal:
    try:
        result = D(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid numeric market field: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("non-finite numeric market field")
    return result


def _milliseconds(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, UTC)


def _levels(rows: Any) -> tuple[BookLevel, ...]:
    if not isinstance(rows, list):
        raise TypeError("order book levels missing")
    return tuple(BookLevel(_decimal(row[0]), _decimal(row[1])) for row in rows)


class PublicRouteProvider:
    """The only S2 source is public REST; evaluations remain research_only."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0),
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=4),
        )
        self._owns_client = client is None
        self._route: Route | None = None
        self._metadata_at: datetime | None = None
        self._binance_interval_hours: int | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _get(self, url: str) -> Any:
        for attempt in range(3):
            response = await self.client.get(url)
            if response.status_code in (418, 429) and attempt < 2:
                retry_after = response.headers.get("Retry-After", "")
                delay = min(30, int(retry_after)) if retry_after.isdigit() else 2 ** attempt
                await asyncio.sleep(max(1, delay))
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("public route request retry limit reached")

    async def verified_route(self, now: datetime) -> Route:
        if self._route and self._metadata_at and now - self._metadata_at < timedelta(minutes=10):
            return self._route
        binance, bybit, funding_rows = await asyncio.gather(
            self._get("https://fapi.binance.com/fapi/v1/exchangeInfo"),
            self._get("https://api.bybit.com/v5/market/instruments-info?category=linear&symbol=LSKUSDT"),
            self._get("https://fapi.binance.com/fapi/v1/fundingInfo"),
        )
        binance_rows = [row for row in binance.get("symbols", []) if row.get("symbol") == "LSKUSDT"]
        bybit_rows = bybit.get("result", {}).get("list", [])
        if len(binance_rows) != 1 or len(bybit_rows) != 1:
            raise ValueError("route market metadata missing or ambiguous")
        b, y = binance_rows[0], bybit_rows[0]
        if (b.get("status") != "TRADING" or b.get("contractType") != "PERPETUAL"
                or b.get("baseAsset") != "LSK" or b.get("quoteAsset") != "USDT"
                or b.get("marginAsset") != "USDT"):
            raise ValueError("Binance LSK identity or linear contract changed")
        if b.get("contractSize") is not None and _decimal(b["contractSize"]) != 1:
            raise ValueError("Binance LSK contract multiplier changed")
        if (y.get("symbol") != "LSKUSDT" or y.get("status") != "Trading"
                or y.get("contractType") != "LinearPerpetual" or y.get("fullName") != "Lisk"
                or y.get("baseCoin") != "LSK" or y.get("quoteCoin") != "USDT"
                or y.get("settleCoin") != "USDT"):
            raise ValueError("Bybit Lisk identity or linear contract changed")
        if y.get("contractSize") is not None and _decimal(y["contractSize"]) != 1:
            raise ValueError("Bybit LSK contract multiplier changed")
        filters = {row.get("filterType"): row for row in b.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        min_notional = filters.get("MIN_NOTIONAL", {})
        y_lot = y.get("lotSizeFilter", {})
        cheap = self._with_lot(
            lsk_research_route().cheap, lot.get("stepSize"), lot.get("minQty"),
            min_notional.get("notional"),
        )
        expensive = self._with_lot(
            lsk_research_route().expensive, y_lot.get("qtyStep"),
            y_lot.get("minOrderQty"), y_lot.get("minNotionalValue"),
        )
        route = lsk_research_route()
        self._route = replace(route, expensive=expensive, cheap=cheap)
        interval_rows = [
            row for row in funding_rows if row.get("symbol") == "LSKUSDT"
        ]
        self._binance_interval_hours = (
            int(interval_rows[0]["fundingIntervalHours"]) if interval_rows else 8
        )
        if self._binance_interval_hours <= 0:
            raise ValueError("Binance funding interval invalid")
        self._metadata_at = now
        return self._route

    @staticmethod
    def _with_lot(leg: RouteLeg, step: Any, minimum: Any, min_notional: Any) -> RouteLeg:
        parsed = (_decimal(step), _decimal(minimum), _decimal(min_notional))
        if any(value <= 0 for value in parsed):
            raise ValueError("market quantity rules invalid")
        return replace(
            leg, quantity_step=parsed[0], minimum_quantity=parsed[1],
            minimum_notional=parsed[2],
        )

    async def snapshot(self, leg: RouteLeg, metadata_at: datetime) -> LegSnapshot:
        if leg.exchange == "binance":
            root = "https://fapi.binance.com"
            book_url = f"{root}/fapi/v1/depth?symbol={leg.raw_symbol}&limit=100"
            trade_url = f"{root}/fapi/v1/trades?symbol={leg.raw_symbol}&limit=1"
            funding_url = f"{root}/fapi/v1/premiumIndex?symbol={leg.raw_symbol}"
            funding, turnover = await asyncio.gather(
                self._get(funding_url),
                self._get(f"{root}/fapi/v1/ticker/24hr?symbol={leg.raw_symbol}"),
            )
            if funding.get("symbol") != leg.raw_symbol or turnover.get("symbol") != leg.raw_symbol:
                raise ValueError("Binance route source identity mismatch")
            book, trades = await asyncio.gather(
                self._get(book_url), self._get(trade_url),
            )
            received_at = datetime.now(UTC)
            if not isinstance(trades, list) or len(trades) != 1:
                raise ValueError("Binance recent trade unavailable")
            trade = trades[0]
            return LegSnapshot(
                leg.key, _levels(book.get("bids")), _levels(book.get("asks")),
                _milliseconds(book["E"]) if book.get("E") else None, received_at,
                int(book["lastUpdateId"]) if book.get("lastUpdateId") else None,
                _milliseconds(trade["time"]),
                _decimal(trade["price"]) * _decimal(trade["qty"]),
                _decimal(funding["lastFundingRate"]) if funding.get("lastFundingRate") is not None else None,
                self._binance_interval_hours,
                _milliseconds(funding["nextFundingTime"]) if funding.get("nextFundingTime") else None,
                _milliseconds(funding["time"]) if funding.get("time") else None,
                metadata_at,
                _decimal(turnover["quoteVolume"]) if turnover.get("quoteVolume") is not None else None,
                "binance_last_public_rate_proxy",
            )
        if leg.exchange == "bybit":
            root = "https://api.bybit.com/v5/market"
            funding = await self._get(
                f"{root}/tickers?category=linear&symbol={leg.raw_symbol}"
            )
            book, trades = await asyncio.gather(
                self._get(f"{root}/orderbook?category=linear&symbol={leg.raw_symbol}&limit=100"),
                self._get(f"{root}/recent-trade?category=linear&symbol={leg.raw_symbol}&limit=1"),
            )
            received_at = datetime.now(UTC)
            result = book.get("result", {})
            trade_rows = trades.get("result", {}).get("list", [])
            tickers = funding.get("result", {}).get("list", [])
            if result.get("s") != leg.raw_symbol or len(trade_rows) != 1 or len(tickers) != 1:
                raise ValueError("Bybit route source identity mismatch")
            trade, ticker = trade_rows[0], tickers[0]
            if trade.get("symbol") != leg.raw_symbol or ticker.get("symbol") != leg.raw_symbol:
                raise ValueError("Bybit trade or funding identity mismatch")
            interval = int(ticker.get("fundingIntervalHour") or 0)
            return LegSnapshot(
                leg.key, _levels(result.get("b")), _levels(result.get("a")),
                _milliseconds(result["ts"]) if result.get("ts") else None, received_at,
                int(result["seq"]) if result.get("seq") else None,
                _milliseconds(trade["time"]),
                _decimal(trade["price"]) * _decimal(trade["size"]),
                _decimal(ticker["fundingRate"]) if ticker.get("fundingRate") is not None else None,
                interval or None,
                _milliseconds(ticker["nextFundingTime"]) if ticker.get("nextFundingTime") else None,
                _milliseconds(funding["time"]) if funding.get("time") else None,
                metadata_at,
                _decimal(ticker["turnover24h"]) if ticker.get("turnover24h") is not None else None,
                "bybit_current_public_estimate",
            )
        raise ValueError(f"unsupported route exchange: {leg.exchange}")

    async def fetch_route(self, now: datetime) -> tuple[Route, LegSnapshot, LegSnapshot]:
        route = await self.verified_route(now)
        assert self._metadata_at is not None
        expensive, cheap = await asyncio.gather(
            self.snapshot(route.expensive, self._metadata_at),
            self.snapshot(route.cheap, self._metadata_at),
        )
        return route, expensive, cheap
