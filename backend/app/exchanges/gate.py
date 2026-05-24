from app.exchanges.base import (
    ExchangeAdapter,
    next_aligned_funding_time,
    normalize_usdt_symbol,
    order_book_snapshot,
    parse_datetime_seconds,
    parse_float,
    separated_usdt_symbol,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot


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
        contract_info = await self._fetch_contract_info()
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
            contract = item.get("contract", raw)
            interval_seconds = parse_float(item.get("funding_interval"))
            if interval_seconds is None:
                interval_seconds = parse_float(contract_info.get(contract, {}).get("funding_interval"))
            interval_hours = int(interval_seconds / 3600) if interval_seconds else 8
            next_time = parse_datetime_seconds(item.get("funding_next_apply")) or next_aligned_funding_time(
                now,
                interval_hours,
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
                    funding_interval_hours=interval_hours,
                    funding_next_time=next_time,
                    mark_price=parse_float(item.get("mark_price")),
                    index_price=parse_float(item.get("index_price")),
                    timestamp=now,
                    raw_symbol=contract,
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
        raw = separated_usdt_symbol(symbol, "_", raw_symbol)
        if market_type == MarketType.SPOT:
            url = f"https://api.gateio.ws/api/v4/spot/order_book?currency_pair={raw}&limit={limit}"
        else:
            url = f"https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={raw}&limit={limit}"
        payload = await self.get_json(url)
        return order_book_snapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=raw,
            bids=payload.get("bids", []) if isinstance(payload, dict) else [],
            asks=payload.get("asks", []) if isinstance(payload, dict) else [],
        )

    async def _fetch_contract_info(self) -> dict[str, dict]:
        try:
            data = await self.get_json("https://api.gateio.ws/api/v4/futures/usdt/contracts")
        except Exception:
            return {}
        if not isinstance(data, list):
            return {}
        return {item.get("name", ""): item for item in data if item.get("name")}
