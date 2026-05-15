from app.exchanges.base import ExchangeAdapter, normalize_usdt_symbol, parse_float, utc_now
from app.models.market import MarketSnapshot, MarketType


class BitgetAdapter(ExchangeAdapter):
    name = "bitget"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        payload = (await self.client.get("https://api.bitget.com/api/v2/spot/market/tickers")).json()
        return self._parse(payload.get("data", []), MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        url = "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES"
        payload = (await self.client.get(url)).json()
        return self._parse(payload.get("data", []), MarketType.FUTURE)

    def _parse(self, data: list[dict], market_type: MarketType) -> list[MarketSnapshot]:
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in data:
            raw = item.get("symbol", "")
            if not raw.endswith("USDT"):
                continue
            bid = parse_float(item.get("bidPr") or item.get("bid"))
            ask = parse_float(item.get("askPr") or item.get("ask"))
            if not bid or not ask:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw)
            funding = parse_float(item.get("fundingRate"))
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=market_type,
                    bid=bid,
                    ask=ask,
                    volume_24h_usdt=parse_float(item.get("quoteVolume") or item.get("usdtVolume")),
                    funding_rate_pct=funding * 100 if funding is not None else None,
                    funding_interval_hours=8 if market_type == MarketType.FUTURE else None,
                    mark_price=parse_float(item.get("markPrice")),
                    index_price=parse_float(item.get("indexPrice")),
                    timestamp=now,
                    raw_symbol=raw,
                )
            )
        return rows
