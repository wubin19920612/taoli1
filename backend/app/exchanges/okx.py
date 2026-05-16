import asyncio

from app.exchanges.base import ExchangeAdapter, normalize_usdt_symbol, parse_float, utc_now
from app.models.market import MarketSnapshot, MarketType


class OKXAdapter(ExchangeAdapter):
    name = "okx"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        payload = await self.get_json("https://www.okx.com/api/v5/market/tickers?instType=SPOT")
        return self._parse_tickers(payload.get("data", []), MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        payload = await self.get_json("https://www.okx.com/api/v5/market/tickers?instType=SWAP")
        tickers = self._parse_tickers(payload.get("data", []), MarketType.FUTURE)
        funding_by_symbol = await self._fetch_funding_by_symbol(tickers)
        enriched: list[MarketSnapshot] = []
        for row in tickers:
            item = funding_by_symbol.get(row.raw_symbol, {})
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

    async def _fetch_funding_by_symbol(
        self,
        tickers: list[MarketSnapshot],
        limit: int = 120,
    ) -> dict[str, dict]:
        semaphore = asyncio.Semaphore(8)

        async def fetch_one(raw_symbol: str) -> tuple[str, dict | None]:
            async with semaphore:
                try:
                    payload = await self.get_json(
                        f"https://www.okx.com/api/v5/public/funding-rate?instId={raw_symbol}"
                    )
                except Exception:
                    return raw_symbol, None
                rows = payload.get("data", [])
                return raw_symbol, rows[0] if rows else None

        results = await asyncio.gather(
            *(fetch_one(row.raw_symbol) for row in tickers[:limit]),
            return_exceptions=False,
        )
        return {symbol: item for symbol, item in results if item is not None}

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
