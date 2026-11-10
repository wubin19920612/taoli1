import math
from datetime import UTC, datetime

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

    def __init__(self, client=None, *, bbo_runtime=None, batch_quotes_executable=False):
        super().__init__(client)
        self.bbo_runtime = bbo_runtime
        self.batch_quotes_executable = batch_quotes_executable

    def _merge_bbo(self, row: MarketSnapshot) -> MarketSnapshot:
        # Compatibility for callers with an existing snapshot. Collection uses
        # _bbo_fields before construction, avoiding a second snapshot per ticker.
        if self.batch_quotes_executable:
            return row
        scale = row.contract_size_multiplier if row.market_type == MarketType.FUTURE else 1
        fields = self._bbo_fields(row.market_type, row.raw_symbol, scale)
        if fields.get("is_estimated"):
            fields["estimated_fields"] = list(dict.fromkeys([*row.estimated_fields, "bid", "ask"]))
        return row.model_copy(update=fields)

    def _bbo_fields(self, kind, raw_symbol, scale):
        if self.batch_quotes_executable:
            return {}
        stage = "get_fresh"
        try:
            bbo = self.bbo_runtime.get_fresh(kind, raw_symbol) if self.bbo_runtime else None
            # Futures B/A are contracts; snapshots store base quantity. Reject
            # missing metadata and overflow/underflow rather than invent depth.
            if bbo is not None and (
                scale is None or not math.isfinite(scale) or scale <= 0
                or any(not math.isfinite(size * scale) or size * scale <= 0
                       for size in (bbo[1], bbo[3]))
            ):
                bbo = None
            fields = None
            if bbo is not None:
                bid, bid_size, ask, ask_size, stamp, _, _ = bbo
                fields = {
                    "bid": bid, "ask": ask, "bid_size": bid_size * scale, "ask_size": ask_size * scale,
                    "upstream_timestamp": datetime.fromtimestamp(stamp / 1000, tz=UTC),
                    "quote_time_source": "ws",
                }
            stage = "record_merge"
            if self.bbo_runtime is not None:
                self.bbo_runtime.record_merge(bbo is not None)
            if fields is not None:
                return fields
        except Exception as exc:  # noqa: BLE001 - isolate optional BBO failure per ticker.
            try:
                self.bbo_runtime.record_merge_error(stage, exc)
            except Exception:  # noqa: BLE001, S110 - do not recursively log a diagnostics failure.
                pass
        return {"is_estimated": True, "estimated_fields": ["bid", "ask"],
                "quote_time_source": "received_batch"}

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
            fields = {
                "symbol": symbol,
                "base": base,
                "quote": quote,
                "exchange": self.name,
                "market_type": MarketType.SPOT,
                "bid": bid,
                "ask": ask,
                "volume_24h_usdt": vol,
                "timestamp": now,
                "raw_symbol": item.get("currency_pair", raw),
                "data_source": "Gate public spot tickers",
                "upstream_timestamp": parse_datetime_seconds(item.get("time")),
            }
            fields.update(self._bbo_fields(MarketType.SPOT, fields["raw_symbol"], 1))
            rows.append(MarketSnapshot(**fields))
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
            contract_metadata = contract_info.get(contract, {})
            interval_seconds = parse_float(item.get("funding_interval"))
            if interval_seconds is None:
                interval_seconds = parse_float(contract_metadata.get("funding_interval"))
            interval_hours = int(interval_seconds / 3600) if interval_seconds else 8
            next_time = parse_datetime_seconds(item.get("funding_next_apply")) or next_aligned_funding_time(
                now,
                interval_hours,
            )
            fields = {
                "symbol": symbol,
                "base": base,
                "quote": quote,
                "exchange": self.name,
                "market_type": MarketType.FUTURE,
                "bid": bid,
                "ask": ask,
                "volume_24h_usdt": parse_float(item.get("volume_24h_quote")),
                "funding_rate_pct": funding * 100 if funding is not None else None,
                "funding_next_rate_pct": indicative * 100 if indicative is not None else None,
                "funding_interval_hours": interval_hours,
                "funding_next_time": next_time,
                "mark_price": parse_float(item.get("mark_price")),
                "index_price": parse_float(item.get("index_price")),
                "timestamp": now,
                "raw_symbol": contract,
                "contract_size_multiplier": parse_float(
                    contract_metadata.get("quanto_multiplier")
                ),
                "data_source": "Gate public futures tickers + contracts",
                "upstream_timestamp": parse_datetime_seconds(
                    item.get("time") or item.get("update_time")
                ),
            }
            fields.update(self._bbo_fields(
                MarketType.FUTURE, contract, fields["contract_size_multiplier"],
            ))
            rows.append(MarketSnapshot(**fields))
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
        except Exception:  # noqa: BLE001 - metadata failure must not hide a usable book.
            return {}
        if not isinstance(data, list):
            return {}
        return {item.get("name", ""): item for item in data if item.get("name")}
