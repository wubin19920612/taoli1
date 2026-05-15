from app.exchanges.base import ExchangeAdapter, normalize_usdt_symbol, parse_float, utc_now
from app.models.market import MarketSnapshot, MarketType


class AsterAdapter(ExchangeAdapter):
    name = "aster"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        data = (await self.client.get("https://www.asterdex.com/api/v1/ticker/bookTicker")).json()
        return self._parse_book(data if isinstance(data, list) else [], MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        data = (await self.client.get("https://fapi.asterdex.com/fapi/v1/ticker/bookTicker")).json()
        return self._parse_book(data if isinstance(data, list) else [], MarketType.FUTURE)

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
                    funding_interval_hours=1 if market_type == MarketType.FUTURE else None,
                    timestamp=now,
                    raw_symbol=raw,
                )
            )
        return rows
