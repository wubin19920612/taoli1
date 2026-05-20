from app.exchanges.base import (
    ExchangeAdapter,
    next_aligned_funding_time,
    normalize_usdt_symbol,
    parse_datetime_seconds,
    parse_float,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType


class GateAdapter(ExchangeAdapter):
    name = "gate"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        data = await self.get_json("https://api.gateio.ws/api/v4/spot/tickers")
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in data:
            raw = str(item.get("currency_pair", "")).replace("_", "")
            if not raw.endswith("USDT"):
                continue
            bid = parse_float(item.get("highest_bid"))
            ask = parse_float(item.get("lowest_ask"))
            if not bid or not ask:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw)
            vol = parse_float(item.get("quote_volume"))
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=MarketType.SPOT,
                    bid=bid,
                    ask=ask,
                    volume_24h_usdt=vol,
                    timestamp=now,
                    raw_symbol=item.get("currency_pair", raw),
                )
            )
        return rows

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        data = await self.get_json("https://api.gateio.ws/api/v4/futures/usdt/tickers")
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in data:
            raw = str(item.get("contract", "")).replace("_", "")
            if not raw.endswith("USDT"):
                continue
            bid = parse_float(item.get("highest_bid"))
            ask = parse_float(item.get("lowest_ask"))
            if not bid or not ask:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw)
            funding = parse_float(item.get("funding_rate"))
            indicative = parse_float(item.get("funding_rate_indicative"))
            interval_seconds = parse_float(item.get("funding_interval"))
            next_time = parse_datetime_seconds(item.get("funding_next_apply")) or next_aligned_funding_time(
                now,
                int(interval_seconds / 3600) if interval_seconds else 8,
            )
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=MarketType.FUTURE,
                    bid=bid,
                    ask=ask,
                    volume_24h_usdt=parse_float(item.get("volume_24h_quote")),
                    funding_rate_pct=funding * 100 if funding is not None else None,
                    funding_next_rate_pct=indicative * 100 if indicative is not None else None,
                    funding_interval_hours=int(interval_seconds / 3600) if interval_seconds else 8,
                    funding_next_time=next_time,
                    mark_price=parse_float(item.get("mark_price")),
                    index_price=parse_float(item.get("index_price")),
                    timestamp=now,
                    raw_symbol=item.get("contract", raw),
                )
            )
        return rows
