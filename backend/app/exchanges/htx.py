from app.exchanges.base import ExchangeAdapter, normalize_usdt_symbol, parse_float, utc_now
from app.models.market import MarketSnapshot, MarketType


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
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in payload.get("ticks", []):
            raw = str(item.get("contract_code", "")).replace("-", "")
            if not raw.endswith("USDT"):
                continue
            tick = item.get("tick", item)
            bid = parse_float((tick.get("bid") or [None])[0] if isinstance(tick.get("bid"), list) else tick.get("bid"))
            ask = parse_float((tick.get("ask") or [None])[0] if isinstance(tick.get("ask"), list) else tick.get("ask"))
            if not bid or not ask:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw)
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
                    funding_interval_hours=8,
                    timestamp=now,
                    raw_symbol=item.get("contract_code", raw),
                )
            )
        return rows
