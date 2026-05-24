from app.exchanges.base import (
    ExchangeAdapter,
    normalize_usdt_symbol,
    order_book_snapshot,
    parse_datetime_ms,
    parse_float,
    separated_usdt_symbol,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot


class HTXAdapter(ExchangeAdapter):
    name = "htx"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        payload = await self.get_json("https://api.huobi.pro/market/tickers")
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in payload.get("data", []):
            raw = str(item.get("symbol", "")).upper()
            if not raw.endswith("USDT"):
                continue
            bid = parse_float(item.get("bid"))
            ask = parse_float(item.get("ask"))
            if not bid or not ask:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw)
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=MarketType.SPOT,
                    bid=bid,
                    ask=ask,
                    volume_24h_usdt=parse_float(item.get("vol")),
                    timestamp=now,
                    raw_symbol=raw,
                )
            )
        return rows

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        url = "https://api.hbdm.com/linear-swap-ex/market/detail/batch_merged"
        payload = await self.get_json(url)
        contract_info = await self._fetch_contract_info()
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in payload.get("ticks", []):
            contract_code = item.get("contract_code", "")
            raw = str(contract_code).replace("-", "")
            if not raw.endswith("USDT"):
                continue
            tick = item.get("tick", item)
            bid = parse_float((tick.get("bid") or [None])[0] if isinstance(tick.get("bid"), list) else tick.get("bid"))
            ask = parse_float((tick.get("ask") or [None])[0] if isinstance(tick.get("ask"), list) else tick.get("ask"))
            if not bid or not ask:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw)
            info = contract_info.get(contract_code, {})
            interval = parse_float(info.get("settlement_period"))
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=MarketType.FUTURE,
                    bid=bid,
                    ask=ask,
                    volume_24h_usdt=parse_float(tick.get("amount")),
                    funding_interval_hours=int(interval) if interval is not None and interval > 0 else 8,
                    funding_next_time=parse_datetime_ms(info.get("settlement_date")),
                    timestamp=now,
                    raw_symbol=contract_code,
                )
            )
        return rows

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot | None:
        if market_type == MarketType.SPOT:
            raw = separated_usdt_symbol(symbol, "", raw_symbol).lower()
            url = f"https://api.huobi.pro/market/depth?symbol={raw}&type=step0&depth={limit}"
        else:
            raw = separated_usdt_symbol(symbol, "-", raw_symbol)
            url = (
                "https://api.hbdm.com/linear-swap-ex/market/depth"
                f"?contract_code={raw}&type=step0&depth={limit}"
            )
        payload = await self.get_json(url)
        tick = payload.get("tick", {}) if isinstance(payload, dict) else {}
        timestamp = parse_datetime_ms(tick.get("ts")) if isinstance(tick, dict) else None
        return order_book_snapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=raw,
            bids=tick.get("bids", []) if isinstance(tick, dict) else [],
            asks=tick.get("asks", []) if isinstance(tick, dict) else [],
            timestamp=timestamp,
        )

    async def _fetch_contract_info(self) -> dict[str, dict]:
        try:
            payload = await self.get_json("https://api.hbdm.com/linear-swap-api/v1/swap_contract_info")
        except Exception:
            return {}
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        return {item.get("contract_code", ""): item for item in rows if item.get("contract_code")}
