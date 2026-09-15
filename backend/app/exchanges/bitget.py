import asyncio
from time import monotonic

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


RTOKEN_SYMBOL_REFRESH_SECONDS = 300.0

# Fallback for a temporary product-metadata outage. The live product list remains
# authoritative and discovers later Bitget additions without a deployment.
KNOWN_RTOKEN_SPOT_SYMBOLS = frozenset(
    {
        "RAAPLUSDT",
        "RAMDUSDT",
        "RAMZNUSDT",
        "RBABAUSDT",
        "RCOINUSDT",
        "RCRCLUSDT",
        "RGMEUSDT",
        "RGOOGLUSDT",
        "RHOODUSDT",
        "RINTCUSDT",
        "RMCDUSDT",
        "RMETAUSDT",
        "RMSFTUSDT",
        "RMSTRUSDT",
        "RNFLXUSDT",
        "RNKEUSDT",
        "RNVDAUSDT",
        "RORCLUSDT",
        "RPLTRUSDT",
        "RQQQIUSDT",
        "RQQQMUSDT",
        "RQQQUSDT",
        "RSPYUSDT",
        "RSQQQUSDT",
        "RTQQQUSDT",
        "RTSLAUSDT",
        "RXOMUSDT",
    }
)


def rtoken_spot_symbols(products: list[dict]) -> set[str]:
    """Return Bitget stock RToken pairs, excluding ordinary R-prefixed assets."""
    symbols: set[str] = set()
    for product in products:
        raw_symbol = str(product.get("symbol", "")).upper()
        base_coin = str(product.get("baseCoin", "")).strip()
        is_rtoken = (
            str(product.get("areaSymbol", "")).lower() == "yes"
            and raw_symbol.endswith("USDT")
            and base_coin.startswith("r")
            and len(base_coin) > 1
            and raw_symbol == f"{base_coin.upper()}USDT"
        )
        if is_rtoken:
            symbols.add(raw_symbol)
    return symbols


class BitgetAdapter(ExchangeAdapter):
    name = "bitget"

    def __init__(
        self,
        *args,
        rtoken_symbol_refresh_seconds: float = RTOKEN_SYMBOL_REFRESH_SECONDS,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._rtoken_spot_symbols: set[str] = set(KNOWN_RTOKEN_SPOT_SYMBOLS)
        self._rtoken_symbols_refreshed_at: float | None = None
        self._rtoken_symbol_refresh_seconds = max(rtoken_symbol_refresh_seconds, 0.0)

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        payload, rtoken_symbols = await asyncio.gather(
            self.get_json("https://api.bitget.com/api/v2/spot/market/tickers"),
            self._fetch_rtoken_spot_symbols(),
        )
        return self._parse(
            payload.get("data", []),
            MarketType.SPOT,
            rtoken_symbols=rtoken_symbols,
        )

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

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot | None:
        raw = compact_usdt_symbol(symbol, raw_symbol)
        if market_type == MarketType.SPOT:
            url = f"https://api.bitget.com/api/v2/spot/market/orderbook?symbol={raw}&type=step0&limit={limit}"
        else:
            url = (
                "https://api.bitget.com/api/v2/mix/market/orderbook"
                f"?symbol={raw}&productType=USDT-FUTURES&limit={limit}"
            )
        payload = await self.get_json(url)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        return order_book_snapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=raw,
            bids=data.get("bids", []) if isinstance(data, dict) else [],
            asks=data.get("asks", []) if isinstance(data, dict) else [],
        )

    async def _fetch_funding_rates(self) -> dict[str, dict]:
        payload = await self.get_json(
            "https://api.bitget.com/api/v2/mix/market/current-fund-rate?productType=USDT-FUTURES"
        )
        rows = payload.get("data", [])
        return {item.get("symbol", ""): item for item in rows if item.get("symbol")}

    async def _fetch_rtoken_spot_symbols(self) -> set[str]:
        now = monotonic()
        if (
            self._rtoken_symbols_refreshed_at is not None
            and now - self._rtoken_symbols_refreshed_at < self._rtoken_symbol_refresh_seconds
        ):
            return self._rtoken_spot_symbols

        try:
            payload = await self.get_json("https://api.bitget.com/api/v2/spot/public/symbols")
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            discovered = rtoken_spot_symbols(rows if isinstance(rows, list) else [])
        except Exception:
            return self._rtoken_spot_symbols

        self._rtoken_spot_symbols = discovered or set(KNOWN_RTOKEN_SPOT_SYMBOLS)
        self._rtoken_symbols_refreshed_at = now
        return self._rtoken_spot_symbols

    def _parse(
        self,
        data: list[dict],
        market_type: MarketType,
        *,
        rtoken_symbols: set[str] | None = None,
    ) -> list[MarketSnapshot]:
        rows: list[MarketSnapshot] = []
        now = utc_now()
        rtoken_symbols = rtoken_symbols or set()
        for item in data:
            raw = item.get("symbol", "")
            if not raw.endswith("USDT"):
                continue
            bid = parse_float(item.get("bidPr") or item.get("bid"))
            ask = parse_float(item.get("askPr") or item.get("ask"))
            if not bid or not ask:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw)
            is_rtoken_spot = market_type == MarketType.SPOT and raw in rtoken_symbols
            if is_rtoken_spot:
                base = base.removeprefix("R")
                symbol = f"{base}{quote}"
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
                    symbol_alias_original_symbol=raw if is_rtoken_spot else None,
                )
            )
        return rows
