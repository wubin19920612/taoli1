from app.exchanges.base import ExchangeAdapter, normalize_usdt_symbol, parse_float, utc_now
from app.models.market import MarketSnapshot, MarketType


class OKXAdapter(ExchangeAdapter):
    name = "okx"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        payload = (await self.client.get("https://www.okx.com/api/v5/market/tickers?instType=SPOT")).json()
        return self._parse_tickers(payload.get("data", []), MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        payload = (await self.client.get("https://www.okx.com/api/v5/market/tickers?instType=SWAP")).json()
        tickers = self._parse_tickers(payload.get("data", []), MarketType.FUTURE)
        funding_payload = (await self.client.get("https://www.okx.com/api/v5/public/funding-rate?instType=SWAP")).json()
        funding_by_symbol = {}
        for item in funding_payload.get("data", []):
            try:
                symbol, _, _ = normalize_usdt_symbol(item.get("instId", ""))
            except ValueError:
                continue
            funding_by_symbol[symbol] = item
        enriched = []
        for row in tickers:
            item = funding_by_symbol.get(row.symbol, {})
            funding = parse_float(item.get("fundingRate"))
            enriched.append(
                row.model_copy(
                    update={
                        "funding_rate_pct": funding * 100 if funding is not None else None,
                        "funding_interval_hours": 8,
                    }
                )
            )
        return enriched

    def _parse_tickers(self, data: list[dict], market_type: MarketType) -> list[MarketSnapshot]:
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in data:
            raw = item.get("instId", "")
            if "USDT" not in raw:
                continue
            try:
                symbol, base, quote = normalize_usdt_symbol(raw)
            except ValueError:
                continue
            bid = parse_float(item.get("bidPx"))
            ask = parse_float(item.get("askPx"))
            if not bid or not ask:
                continue
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=market_type,
                    bid=bid,
                    ask=ask,
                    bid_size=parse_float(item.get("bidSz")),
                    ask_size=parse_float(item.get("askSz")),
                    volume_24h_usdt=parse_float(item.get("volCcy24h")),
                    timestamp=now,
                    raw_symbol=raw,
                )
            )
        return rows
