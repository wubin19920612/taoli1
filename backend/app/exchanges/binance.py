from app.exchanges.base import (
    ExchangeAdapter,
    compact_usdt_symbol,
    next_aligned_funding_time,
    normalize_usdt_symbol,
    order_book_snapshot,
    parse_datetime_ms,
    parse_float,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot


class BinanceAdapter(ExchangeAdapter):
    name = "binance"
    spot_base_url = "https://data-api.binance.vision"
    futures_base_url = "https://fapi.binance.com"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        data = await self.get_json(f"{self.spot_base_url}/api/v3/ticker/bookTicker")
        return self._parse_book_tickers(data, MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        book = await self.get_json(f"{self.futures_base_url}/fapi/v1/ticker/bookTicker")
        premium = await self.get_json(f"{self.futures_base_url}/fapi/v1/premiumIndex")
        interval_by_symbol = await self._fetch_funding_intervals()
        premium_by_symbol = {item["symbol"]: item for item in premium if item.get("symbol")}
        snapshots = self._parse_book_tickers(book, MarketType.FUTURE)
        now = utc_now()
        enriched = []
        for snapshot in snapshots:
            item = premium_by_symbol.get(snapshot.raw_symbol, {})
            funding = parse_float(item.get("lastFundingRate"))
            interval_hours = interval_by_symbol.get(snapshot.raw_symbol, 8)
            next_time = parse_datetime_ms(item.get("nextFundingTime")) or next_aligned_funding_time(
                now,
                interval_hours,
            )
            enriched.append(
                snapshot.model_copy(
                    update={
                        "funding_rate_pct": funding * 100 if funding is not None else None,
                        "funding_next_rate_pct": None,
                        "funding_interval_hours": interval_hours,
                        "funding_next_time": next_time,
                        "mark_price": parse_float(item.get("markPrice")),
                        "index_price": parse_float(item.get("indexPrice")),
                    }
                )
            )
        return enriched

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot | None:
        raw = compact_usdt_symbol(symbol, raw_symbol)
        if market_type == MarketType.SPOT:
            url = f"{self.spot_base_url}/api/v3/depth?symbol={raw}&limit={limit}"
        else:
            url = f"{self.futures_base_url}/fapi/v1/depth?symbol={raw}&limit={limit}"
        payload = await self.get_json(url)
        return order_book_snapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=raw,
            bids=payload.get("bids", []) if isinstance(payload, dict) else [],
            asks=payload.get("asks", []) if isinstance(payload, dict) else [],
        )

    async def _fetch_funding_intervals(self) -> dict[str, int]:
        try:
            rows = await self.get_json(f"{self.futures_base_url}/fapi/v1/fundingInfo")
        except Exception:
            return {}
        intervals: dict[str, int] = {}
        for item in rows if isinstance(rows, list) else []:
            symbol = item.get("symbol")
            interval = parse_float(item.get("fundingIntervalHours"))
            if symbol and interval is not None and interval > 0:
                intervals[symbol] = int(interval)
        return intervals

    def _parse_book_tickers(self, data: list[dict], market_type: MarketType) -> list[MarketSnapshot]:
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in data:
            raw_symbol = item.get("symbol", "")
            if not raw_symbol.endswith("USDT"):
                continue
            bid = parse_float(item.get("bidPrice"))
            ask = parse_float(item.get("askPrice"))
            bid_size = parse_float(item.get("bidQty"))
            ask_size = parse_float(item.get("askQty"))
            if not bid or not ask:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw_symbol)
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=market_type,
                    bid=bid,
                    ask=ask,
                    bid_size=bid_size,
                    ask_size=ask_size,
                    timestamp=now,
                    raw_symbol=raw_symbol,
                )
            )
        return rows
