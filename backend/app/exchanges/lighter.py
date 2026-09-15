from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from app.exchanges.base import ExchangeAdapter, next_aligned_funding_time, order_book_snapshot, parse_float, utc_now
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot

LIGHTER_URL = "https://mainnet.zklighter.elliot.ai/api/v1"


def lighter_symbol(raw: str, market_type: MarketType) -> tuple[str, str] | None:
    name = raw.strip().upper()
    if market_type == MarketType.SPOT:
        if not name.endswith("/USDC"):
            return None
        name = name.removesuffix("/USDC")
    if not name or "/" in name or not name.isalnum():
        return None
    return f"{name}USDT", name


def lighter_book_levels(rows: Any) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        price = parse_float(row.get("price"))
        size = parse_float(row.get("remaining_base_amount"))
        if price is not None and size is not None and price > 0 and size > 0:
            levels.append((price, size))
    return levels


def lighter_best_prices(payload: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        return None
    bids = lighter_book_levels(payload.get("bids"))
    asks = lighter_book_levels(payload.get("asks"))
    if not bids or not asks:
        return None
    bid = max(price for price, _ in bids)
    ask = min(price for price, _ in asks)
    if bid >= ask:
        return None
    return bid, ask, sum(size for price, size in bids if price == bid), sum(
        size for price, size in asks if price == ask
    )


class LighterAdapter(ExchangeAdapter):
    name = "lighter"
    max_concurrent_books = 8
    book_refresh_seconds = 12
    max_scanner_perp_markets = 48

    def __init__(self, client=None):
        super().__init__(client)
        self._markets: dict[tuple[MarketType, str], int] = {}
        self._cached: dict[MarketType, tuple[datetime, list[MarketSnapshot]]] = {}

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        return await self._fetch_tickers(MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        return await self._fetch_tickers(MarketType.FUTURE)

    async def _fetch_tickers(self, market_type: MarketType) -> list[MarketSnapshot]:
        now = utc_now()
        cached = self._cached.get(market_type)
        if cached and now - cached[0] < timedelta(seconds=self.book_refresh_seconds):
            return cached[1]

        details = await self.get_json(f"{LIGHTER_URL}/orderBookDetails")
        if not isinstance(details, dict) or details.get("code") != 200:
            raise RuntimeError("invalid Lighter orderBookDetails response")
        key = "spot_order_book_details" if market_type == MarketType.SPOT else "order_book_details"
        rows = details.get(key)
        if not isinstance(rows, list):
            raise RuntimeError("missing Lighter market details")
        funding: dict[int, float] = {}
        if market_type == MarketType.FUTURE:
            try:
                rates = await self.get_json(f"{LIGHTER_URL}/funding-rates")
                if isinstance(rates, dict) and rates.get("code") == 200:
                    funding = {
                        item["market_id"]: rate
                        for item in rates.get("funding_rates", [])
                        if isinstance(item, dict)
                        and item.get("exchange") == "lighter"
                        and isinstance(item.get("market_id"), int)
                        if (rate := parse_float(item.get("rate"))) is not None
                    }
            except Exception:
                pass

        markets: list[tuple[dict, str, str, int]] = []
        for item in rows:
            if not isinstance(item, dict) or item.get("status") != "active":
                continue
            resolved = lighter_symbol(str(item.get("symbol", "")), market_type)
            market_id = item.get("market_id")
            if resolved is None or not isinstance(market_id, int):
                continue
            symbol, base = resolved
            markets.append((item, symbol, base, market_id))
        if market_type == MarketType.FUTURE:
            # The REST book is per market; bound automatic polling to liquid markets.
            markets.sort(key=lambda row: parse_float(row[0].get("daily_quote_token_volume")) or 0, reverse=True)
            markets = markets[: self.max_scanner_perp_markets]
        semaphore = asyncio.Semaphore(self.max_concurrent_books)

        async def fetch(row: tuple[dict, str, str, int]) -> MarketSnapshot | None:
            item, symbol, base, market_id = row
            async with semaphore:
                try:
                    book = await self.get_json(f"{LIGHTER_URL}/orderBookOrders?market_id={market_id}&limit=20")
                except Exception:
                    return None
            prices = lighter_best_prices(book)
            if prices is None:
                return None
            bid, ask, bid_size, ask_size = prices
            rate = funding.get(market_id)
            return MarketSnapshot(
                symbol=symbol,
                base=base,
                quote="USDT",
                exchange=self.name,
                market_type=market_type,
                bid=bid,
                ask=ask,
                bid_size=bid_size,
                ask_size=ask_size,
                volume_24h_usdt=parse_float(item.get("daily_quote_token_volume")),
                funding_rate_pct=rate * 100 if rate is not None else None,
                funding_interval_hours=1 if market_type == MarketType.FUTURE else None,
                funding_next_time=next_aligned_funding_time(now, 1) if market_type == MarketType.FUTURE else None,
                mark_price=parse_float(item.get("mark_price")),
                index_price=parse_float(item.get("index_price")),
                timestamp=utc_now(),
                raw_symbol=str(item["symbol"]),
            )

        snapshots = [market for market in await asyncio.gather(*(fetch(row) for row in markets)) if market]
        self._markets.update({(market_type, row[0]["symbol"]): row[3] for row in markets})
        self._cached[market_type] = (utc_now(), snapshots)
        return snapshots

    async def fetch_order_book(
        self, symbol: str, market_type: MarketType, raw_symbol: str, limit: int = 20
    ) -> OrderBookSnapshot | None:
        market_id = self._markets.get((market_type, raw_symbol))
        if market_id is None:
            details = await self.get_json(f"{LIGHTER_URL}/orderBookDetails")
            key = "spot_order_book_details" if market_type == MarketType.SPOT else "order_book_details"
            for row in details.get(key, []) if isinstance(details, dict) else []:
                if isinstance(row, dict) and row.get("symbol") == raw_symbol and row.get("status") == "active":
                    market_id = row.get("market_id")
                    break
        if not isinstance(market_id, int):
            return None
        payload = await self.get_json(f"{LIGHTER_URL}/orderBookOrders?market_id={market_id}&limit={max(1, min(limit, 100))}")
        if not isinstance(payload, dict) or payload.get("code") != 200:
            return None
        return order_book_snapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=raw_symbol,
            bids=lighter_book_levels(payload.get("bids")),
            asks=lighter_book_levels(payload.get("asks")),
        )
