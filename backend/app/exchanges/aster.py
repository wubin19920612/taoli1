import asyncio

import httpx

from app.exchanges.base import (
    ExchangeAdapter,
    ExchangeRequestError,
    compact_usdt_symbol,
    normalize_usdt_symbol,
    order_book_snapshot,
    parse_datetime_ms,
    parse_float,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot


class AsterAdapter(ExchangeAdapter):
    name = "aster"
    spot_base_url = "https://sapi.asterdex.com"
    futures_base_url = "https://fapi.asterdex.com"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        data = await self.get_json(f"{self.spot_base_url}/api/v1/ticker/bookTicker")
        return self._parse_book(data if isinstance(data, list) else [], MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        data = await self.get_json(f"{self.futures_base_url}/fapi/v1/ticker/bookTicker")
        rows = self._parse_book(data if isinstance(data, list) else [], MarketType.FUTURE)
        intervals, premium, volumes = await asyncio.gather(
            self._fetch_funding_intervals(),
            self._fetch_premium_index(),
            self._fetch_future_24h_volumes(),
        )
        enriched: list[MarketSnapshot] = []
        for row in rows:
            item = premium.get(row.raw_symbol, {})
            funding = parse_float(item.get("lastFundingRate"))
            enriched.append(
                row.model_copy(
                    update={
                        "funding_interval_hours": intervals.get(
                            row.raw_symbol, row.funding_interval_hours
                        ),
                        "funding_rate_pct": funding * 100 if funding is not None else None,
                        "funding_next_time": parse_datetime_ms(item.get("nextFundingTime")),
                        "mark_price": parse_float(item.get("markPrice")),
                        "index_price": parse_float(item.get("indexPrice")),
                        "volume_24h_usdt": volumes.get(row.raw_symbol),
                        "data_source": "Aster public bookTicker + premiumIndex + ticker/24hr + fundingInfo",
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
            url = f"{self.spot_base_url}/api/v1/depth?symbol={raw}&limit={limit}"
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
            rows = await self.get_json(f"{self.futures_base_url}/fapi/v1/fundingInfo")
        except (ExchangeRequestError, httpx.RequestError):
            return {}
        intervals: dict[str, int] = {}
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            interval = parse_float(item.get("fundingIntervalHours"))
            if symbol and interval is not None and interval > 0:
                intervals[symbol] = int(interval)
        return intervals

    async def _fetch_premium_index(self) -> dict[str, dict]:
        try:
            rows = await self.get_json(f"{self.futures_base_url}/fapi/v1/premiumIndex")
        except (ExchangeRequestError, httpx.RequestError):
            return {}
        return {
            item["symbol"]: item
            for item in (rows if isinstance(rows, list) else [])
            if isinstance(item, dict) and item.get("symbol")
        }

    async def _fetch_future_24h_volumes(self) -> dict[str, float]:
        try:
            rows = await self.get_json(f"{self.futures_base_url}/fapi/v1/ticker/24hr")
        except (ExchangeRequestError, httpx.RequestError):
            return {}
        volumes: dict[str, float] = {}
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            volume = parse_float(item.get("quoteVolume"))
            if symbol and volume is not None and volume >= 0:
                volumes[symbol] = volume
        return volumes
