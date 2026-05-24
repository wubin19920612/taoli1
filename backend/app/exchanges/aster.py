from app.exchanges.base import (
    ExchangeAdapter,
    compact_usdt_symbol,
    normalize_usdt_symbol,
    order_book_snapshot,
    parse_float,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot


class AsterAdapter(ExchangeAdapter):
    name = "aster"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        data = await self.get_json("https://www.asterdex.com/api/v1/ticker/bookTicker")
        return self._parse_book(data if isinstance(data, list) else [], MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        data = await self.get_json("https://fapi.asterdex.com/fapi/v1/ticker/bookTicker")
        intervals = await self._fetch_funding_intervals()
        rows = self._parse_book(data if isinstance(data, list) else [], MarketType.FUTURE)
        return [
            row.model_copy(
                update={
                    "funding_interval_hours": intervals.get(row.raw_symbol, row.funding_interval_hours),
                }
            )
            for row in rows
        ]

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot | None:
        raw = compact_usdt_symbol(symbol, raw_symbol)
        if market_type == MarketType.SPOT:
            url = f"https://www.asterdex.com/api/v1/depth?symbol={raw}&limit={limit}"
        else:
            url = f"https://fapi.asterdex.com/fapi/v1/depth?symbol={raw}&limit={limit}"
        payload = await self.get_json(url)
        return order_book_snapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=raw,
            bids=payload.get("bids", []) if isinstance(payload, dict) else [],
            asks=payload.get("asks", []) if isinstance(payload, dict) else [],
        )

    def _parse_book(self, data: list[dict], market_type: MarketType) -> list[MarketSnapshot]:
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in data:
            raw = item.get("symbol", "")
            if not raw.endswith("USDT"):
                continue
            bid = parse_float(item.get("bidPrice"))
            ask = parse_float(item.get("askPrice"))
            if not bid or not ask:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw)
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=market_type,
                    bid=bid,
                    ask=ask,
                    bid_size=parse_float(item.get("bidQty")),
                    ask_size=parse_float(item.get("askQty")),
                    timestamp=now,
                    raw_symbol=raw,
                )
            )
        return rows

    async def _fetch_funding_intervals(self) -> dict[str, int]:
        try:
            rows = await self.get_json("https://fapi.asterdex.com/fapi/v1/fundingInfo")
        except Exception:
            return {}
        intervals: dict[str, int] = {}
        for item in rows if isinstance(rows, list) else []:
            symbol = item.get("symbol")
            interval = parse_float(item.get("fundingIntervalHours"))
            if symbol and interval is not None and interval > 0:
                intervals[symbol] = int(interval)
        return intervals
