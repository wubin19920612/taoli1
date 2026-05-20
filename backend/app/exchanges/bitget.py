from app.exchanges.base import (
    ExchangeAdapter,
    normalize_usdt_symbol,
    parse_datetime_ms,
    parse_float,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType


class BitgetAdapter(ExchangeAdapter):
    name = "bitget"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        payload = await self.get_json("https://api.bitget.com/api/v2/spot/market/tickers")
        return self._parse(payload.get("data", []), MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        url = "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES"
        payload = await self.get_json(url)
        tickers = self._parse(payload.get("data", []), MarketType.FUTURE)
        funding = await self._fetch_funding_rates()
        enriched: list[MarketSnapshot] = []
        for row in tickers:
            item = funding.get(row.raw_symbol, {})
            next_time = parse_datetime_ms(item.get("nextUpdate"))
            funding_rate = parse_float(item.get("fundingRate"))
            interval_hours = parse_float(item.get("fundingRateInterval"))
            enriched.append(
                row.model_copy(
                    update={
                        "funding_rate_pct": funding_rate * 100 if funding_rate is not None else row.funding_rate_pct,
                        "funding_next_rate_pct": None,
                        "funding_interval_hours": int(interval_hours) if interval_hours is not None else row.funding_interval_hours,
                        "funding_next_time": next_time,
                    }
                )
            )
        return enriched

    async def _fetch_funding_rates(self) -> dict[str, dict]:
        payload = await self.get_json(
            "https://api.bitget.com/api/v2/mix/market/current-fund-rate?productType=USDT-FUTURES"
        )
        rows = payload.get("data", [])
        return {item.get("symbol", ""): item for item in rows if item.get("symbol")}

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
            next_time = parse_datetime_ms(item.get("nextUpdate"))
            interval = parse_float(item.get("fundingRateInterval"))
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
                    funding_interval_hours=int(interval) if interval is not None and market_type == MarketType.FUTURE else None,
                    funding_next_time=next_time,
                    mark_price=parse_float(item.get("markPrice")),
                    index_price=parse_float(item.get("indexPrice")),
                    timestamp=now,
                    raw_symbol=raw,
                )
            )
        return rows
