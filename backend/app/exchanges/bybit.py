from app.exchanges.base import (
    ExchangeAdapter,
    compact_usdt_symbol,
    normalize_usdt_symbol,
    order_book_snapshot,
    parse_datetime_ms,
    parse_float,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot


class BybitAdapter(ExchangeAdapter):
    name = "bybit"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        payload = await self.get_json("https://api.bybit.com/v5/market/tickers?category=spot")
        return self._parse(payload.get("result", {}).get("list", []), MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        payload = await self.get_json("https://api.bybit.com/v5/market/tickers?category=linear")
        return self._parse(payload.get("result", {}).get("list", []), MarketType.FUTURE)

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot | None:
        raw = compact_usdt_symbol(symbol, raw_symbol)
        category = "spot" if market_type == MarketType.SPOT else "linear"
        payload = await self.get_json(
            f"https://api.bybit.com/v5/market/orderbook?category={category}&symbol={raw}&limit={limit}"
        )
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        return order_book_snapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=raw,
            bids=result.get("b", []) if isinstance(result, dict) else [],
            asks=result.get("a", []) if isinstance(result, dict) else [],
        )

    def _parse(self, data: list[dict], market_type: MarketType) -> list[MarketSnapshot]:
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in data:
            raw = item.get("symbol", "")
            if not raw.endswith("USDT"):
                continue
            bid = parse_float(item.get("bid1Price"))
            ask = parse_float(item.get("ask1Price"))
            if not bid or not ask:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw)
            funding = parse_float(item.get("fundingRate"))
            interval = parse_float(item.get("fundingIntervalHour"))
            turnover = parse_float(item.get("turnover24h"))
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=market_type,
                    bid=bid,
                    ask=ask,
                    bid_size=parse_float(item.get("bid1Size")),
                    ask_size=parse_float(item.get("ask1Size")),
                    volume_24h_usdt=turnover,
                    funding_rate_pct=funding * 100 if funding is not None else None,
                    funding_interval_hours=int(interval) if interval is not None and market_type == MarketType.FUTURE else None,
                    funding_next_time=parse_datetime_ms(item.get("nextFundingTime")),
                    mark_price=parse_float(item.get("markPrice")),
                    index_price=parse_float(item.get("indexPrice")),
                    timestamp=now,
                    raw_symbol=raw,
                )
            )
        return rows
