from app.exchanges.base import (
    ExchangeAdapter,
    next_aligned_funding_time,
    normalize_usdt_symbol,
    parse_datetime_ms,
    parse_float,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType


class BinanceAdapter(ExchangeAdapter):
    name = "binance"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        data = await self.get_json("https://api.binance.com/api/v3/ticker/bookTicker")
        return self._parse_book_tickers(data, MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        book = await self.get_json("https://fapi.binance.com/fapi/v1/ticker/bookTicker")
        premium = await self.get_json("https://fapi.binance.com/fapi/v1/premiumIndex")
        premium_by_symbol = {item["symbol"]: item for item in premium if item.get("symbol")}
        snapshots = self._parse_book_tickers(book, MarketType.FUTURE)
        now = utc_now()
        enriched = []
        for snapshot in snapshots:
            item = premium_by_symbol.get(snapshot.raw_symbol, {})
            funding = parse_float(item.get("lastFundingRate"))
            next_time = parse_datetime_ms(item.get("nextFundingTime")) or next_aligned_funding_time(
                now,
                8,
            )
            enriched.append(
                snapshot.model_copy(
                    update={
                        "funding_rate_pct": funding * 100 if funding is not None else None,
                        "funding_next_rate_pct": None,
                        "funding_interval_hours": 8,
                        "funding_next_time": next_time,
                        "mark_price": parse_float(item.get("markPrice")),
                        "index_price": parse_float(item.get("indexPrice")),
                    }
                )
            )
        return enriched

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
