from datetime import datetime

from app.exchanges.base import (
    ExchangeAdapter,
    next_aligned_funding_time,
    normalize_usdt_symbol,
    order_book_snapshot,
    parse_datetime_ms,
    parse_float,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot


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
            funding_time = parse_datetime_ms(item.get("fundingTime"))
            next_time = parse_datetime_ms(item.get("nextFundingTime")) or funding_time
            interval_hours = _funding_interval_hours(funding_time, next_time) or 8
            next_time = next_time or next_aligned_funding_time(utc_now(), interval_hours)
            enriched.append(
                row.model_copy(
                    update={
                        "funding_rate_pct": funding * 100 if funding is not None else None,
                        "funding_next_rate_pct": parse_float(item.get("nextFundingRate")) * 100
                        if parse_float(item.get("nextFundingRate")) is not None
                        else None,
                        "funding_interval_hours": interval_hours,
                        "funding_next_time": next_time,
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
        inst_id = _inst_id(symbol, raw_symbol, market_type)
        payload = await self.get_json(
            f"https://www.okx.com/api/v5/market/books?instId={inst_id}&sz={limit}"
        )
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if not rows:
            return None
        row = rows[0]
        timestamp = parse_datetime_ms(row.get("ts")) if isinstance(row, dict) else None
        return order_book_snapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=inst_id,
            bids=row.get("bids", []) if isinstance(row, dict) else [],
            asks=row.get("asks", []) if isinstance(row, dict) else [],
            timestamp=timestamp,
        )

    async def _fetch_funding_by_symbol(
        self,
        tickers: list[MarketSnapshot],
    ) -> dict[str, dict]:
        if not tickers:
            return {}
        try:
            payload = await self.get_json(
                "https://www.okx.com/api/v5/public/funding-rate?instId=ANY"
            )
        except Exception:
            return {}
        rows = payload.get("data", [])
        return {
            item.get("instId", ""): item
            for item in rows
            if item.get("instId")
        }

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


def _funding_interval_hours(funding_time: datetime | None, next_funding_time: datetime | None) -> int | None:
    if funding_time is None or next_funding_time is None:
        return None
    seconds = (next_funding_time - funding_time).total_seconds()
    if seconds <= 0:
        return None
    hours = seconds / 3600
    rounded = round(hours)
    if rounded <= 0:
        return None
    return rounded


def _inst_id(symbol: str, raw_symbol: str, market_type: MarketType) -> str:
    candidate = raw_symbol.upper()
    if "-" in candidate and "USDT" in candidate:
        return candidate
    _, base, quote = normalize_usdt_symbol(symbol)
    if market_type == MarketType.FUTURE:
        return f"{base}-{quote}-SWAP"
    return f"{base}-{quote}"
