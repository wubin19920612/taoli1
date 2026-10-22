"""Public-only FS sources. No keys, private requests, or inferred borrow rates."""

import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from app.models.market import MarketType
from app.services.fs_bybit import FsDataError, number

EXCHANGES = ("bybit", "binance", "okx", "bitget", "gate")
HOSTS = {
    "binance": "https://api.binance.com",
    "okx": "https://www.okx.com",
    "bitget": "https://api.bitget.com",
    "gate": "https://api.gateio.ws/api/v4",
}


def route_key(row):
    # Preserve durable Bybit submission markers from the first release.
    return row.symbol if row.exchange == "bybit" else f"{row.exchange}:{row.symbol}"


def raw_symbols(exchange, base):
    if exchange == "okx":
        return f"{base}-USDT", f"{base}-USDT-SWAP"
    if exchange == "gate":
        return (f"{base}_USDT",) * 2
    return (f"{base}USDT",) * 2


def daily_to_hourly(value):
    rate = number(value, nonnegative=True)
    return rate / 24 if rate is not None else None


class PublicFsProvider:
    def __init__(self, exchange, store, client=None):
        self.exchange, self.store = exchange, store
        self._client = client
        self.owns_client = client is None
        self.cache = {}
        self.lock = asyncio.Lock()
        self.last_request = 0.0

    async def aclose(self):
        if self.owns_client and self._client is not None:
            await self._client.aclose()

    @property
    def client(self):
        # Disabled venues and API-only lifecycles need no TLS contexts/sockets.
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=8, follow_redirects=False)
        return self._client

    async def get(self, path, params=None, *, futures=False):
        host = "https://fapi.binance.com" if futures else HOSTS[self.exchange]
        async with self.lock:
            await asyncio.sleep(max(0, 0.13 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
        try:
            r = await self.client.get(host + path, params=params)
            r.raise_for_status()
            data = r.json()
            at = datetime.now(UTC)
            if self.exchange in {"okx", "bitget"}:
                if data.get("code") not in {"0", "00000"}:
                    raise FsDataError("exchange_query_failed")
                data = data["data"]
            return data, at
        except FsDataError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise FsDataError("public_query_failed") from None

    async def borrow_reference(self):
        ex = self.exchange
        if ex == "okx":
            data, at = await self.get("/api/v5/public/interest-rate-loan-quota")
            return {
                x["ccy"]: {"borrowable": True, "hourlyBorrowRate": daily_to_hourly(x.get("rate"))}
                for x in data[0]["basic"]
            }, at
        if ex == "bitget":
            data, at = await self.get("/api/v2/margin/currencies")
            return {
                x["baseCoin"]: {
                    "borrowable": x.get("isCrossBorrowable") is True and x.get("status") == "1"
                }
                for x in data
                if x.get("quoteCoin") == "USDT"
            }, at
        if ex == "gate":
            data, at = await self.get("/margin/uni/currency_pairs")
            return {
                x["currency_pair"][:-5]: {"borrowable": x.get("status") == "enabled"}
                for x in data
                if x.get("currency_pair", "").endswith("_USDT")
            }, at
        # Binance SAPI requires an API key even for the product reference. Never
        # borrow credentials from another exchange or substitute lending yields.
        return {}, datetime.now(UTC)

    async def public_snapshot(self):
        rates, margin_at, borrow_error = {}, datetime.now(UTC), None
        try:
            rates, margin_at = await self.borrow_reference()
        except (FsDataError, KeyError, TypeError, IndexError):
            borrow_error = "public_borrow_query_failed"
        data = {k: {} for k in ("spots", "futures", "spot_tickers", "future_tickers")}
        for m in self.store.get_markets():
            if m.exchange != self.exchange or m.quote != "USDT" or m.dex:
                continue
            spot_raw, future_raw = raw_symbols(self.exchange, m.base)
            expected = spot_raw if m.market_type == MarketType.SPOT else future_raw
            # Discovery is not identity approval. Do not collapse aliases/R tokens.
            if m.raw_symbol != expected or m.symbol != m.base + "USDT":
                continue
            kind = "spot" if m.market_type == MarketType.SPOT else "future"
            data["spots" if kind == "spot" else "futures"][m.symbol] = {
                "symbol": m.raw_symbol,
                "baseCoin": m.base,
                "quoteCoin": "USDT",
            }
            data[kind + "_tickers"][m.symbol] = {
                "symbol": m.symbol,
                "fundingRate": (
                    m.funding_rate_pct / 100 if m.funding_rate_pct is not None else None
                ),
                "fundingIntervalHour": m.funding_interval_hours,
                "nextFundingTime": m.funding_next_time.timestamp() * 1000
                if m.funding_next_time
                else None,
                "turnover24h": m.volume_24h_usdt,
                "markPrice": m.mark_price,
                "source_at": m.upstream_timestamp or m.timestamp,
            }
        data.update(
            rates=rates,
            margin_at=margin_at,
            spot_at=margin_at,
            future_at=margin_at,
            borrow_error=borrow_error,
            exchange=self.exchange,
        )
        return data

    async def current_instruments(self, symbol):
        base = symbol[:-4]
        sr, fr = raw_symbols(self.exchange, base)
        if self.exchange == "okx":
            (s, sa), (f, fa), (margin, _) = await asyncio.gather(
                self.get("/api/v5/public/instruments", {"instType": "SPOT", "instId": sr}),
                self.get("/api/v5/public/instruments", {"instType": "SWAP", "instId": fr}),
                self.get("/api/v5/public/instruments", {"instType": "MARGIN", "instId": sr}),
            )
            s = next(x for x in s if x["instId"] == sr)
            f = next(x for x in f if x["instId"] == fr)
            mult = float(Decimal(f["ctVal"]) * Decimal(f.get("ctMult") or "1"))
            valid = (
                s["baseCcy"] == base
                and s["quoteCcy"] == "USDT"
                and f["ctValCcy"] == base
                and f["settleCcy"] == "USDT"
                and f["ctType"] == "linear"
                and f["instType"] == "SWAP"
            )
            sm = self.meta(sr, base, s["state"] == "live" and valid, s["lotSz"], s["minSz"], 0)
            fm = self.meta(
                fr,
                base,
                f["state"] == "live" and valid,
                str(Decimal(f["lotSz"]) * Decimal(str(mult))),
                str(Decimal(f["minSz"]) * Decimal(str(mult))),
                0,
                multiplier=mult,
            )
            sm["marginBorrowable"] = any(
                x.get("instId") == sr and x.get("state") == "live" for x in margin
            )
            sm["lotSizeFilter"]["maxMarketOrderQty"] = s.get("maxMktSz")
            fm["lotSizeFilter"]["maxMktOrderQty"] = (
                float(f["maxMktSz"]) * mult if f.get("maxMktSz") else None
            )
        elif self.exchange == "bitget":
            (s, sa), (f, fa) = await asyncio.gather(
                self.get("/api/v2/spot/public/symbols", {"symbol": sr}),
                self.get(
                    "/api/v2/mix/market/contracts", {"symbol": fr, "productType": "USDT-FUTURES"}
                ),
            )
            s, f = next(x for x in s if x["symbol"] == sr), next(x for x in f if x["symbol"] == fr)
            valid = all(x["baseCoin"] == base and x["quoteCoin"] == "USDT" for x in (s, f))
            sm = self.meta(
                sr,
                base,
                s["status"] == "online" and valid,
                10 ** -int(s["quantityPrecision"]),
                s.get("minTradeAmount", 0),
                s["minTradeUSDT"],
            )
            fm = self.meta(
                fr,
                base,
                f["symbolStatus"] == "normal" and valid and f.get("symbolType") == "perpetual",
                f["sizeMultiplier"],
                f["minTradeNum"],
                f["minTradeUSDT"],
            )
        elif self.exchange == "gate":
            (s, sa), (f, fa) = await asyncio.gather(
                self.get("/spot/currency_pairs/" + sr),
                self.get("/futures/usdt/contracts/" + fr),
            )
            mult = float(f["quanto_multiplier"])
            valid = s["id"] == sr and s["base"] == base and s["quote"] == "USDT" and f["name"] == fr
            sm = self.meta(
                sr,
                base,
                s["trade_status"] == "tradable" and valid,
                10 ** -int(s["amount_precision"]),
                s["min_base_amount"],
                s["min_quote_amount"],
            )
            fm = self.meta(
                fr,
                base,
                f.get("in_delisting") is False and valid and f.get("type") == "direct",
                mult,
                float(f["order_size_min"]) * mult,
                0,
                multiplier=mult,
            )
        else:
            (s, sa), (f, fa) = await asyncio.gather(
                self.binance_specs(False), self.binance_specs(True)
            )
            s, f = s[sr], f[fr]
            sm, fm = [], []
            for item, dest in ((s, sm), (f, fm)):
                filters = {x["filterType"]: x for x in item["filters"]}
                lot = filters["LOT_SIZE"]
                minimum = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
                active = (
                    item["status"] == "TRADING"
                    and item["baseAsset"] == base
                    and item["quoteAsset"] == "USDT"
                )
                if item is f:
                    active = (
                        active
                        and item.get("contractType") == "PERPETUAL"
                        and item.get("marginAsset") == "USDT"
                    )
                dest.append(
                    self.meta(
                        item["symbol"],
                        base,
                        active,
                        lot["stepSize"],
                        lot["minQty"],
                        minimum.get("minNotional", minimum.get("notional")),
                    )
                )
            sm, fm = sm[0], fm[0]
        # Already normalized to base coin quantities; keep original contract size
        # separately for book conversion and identity comparison.
        self.cache[symbol] = (sm, fm)
        return (sm, sa), (fm, fa)

    @staticmethod
    def meta(raw, base, active, step, minimum, notional, *, multiplier=1):
        return {
            "symbol": raw,
            "baseCoin": base,
            "quoteCoin": "USDT",
            "settleCoin": "USDT",
            "contractType": "LinearPerpetual",
            "status": "Trading" if active else "Closed",
            "baseMultiplier": multiplier,
            "lotSizeFilter": {
                "basePrecision": step,
                "qtyStep": step,
                "minOrderQty": minimum,
                "minOrderAmt": notional,
                "minNotionalValue": notional,
            },
        }

    async def binance_specs(self, futures):
        key = ("binance_specs", futures)
        cached = self.cache.get(key)
        if cached and (datetime.now(UTC) - cached[1]).total_seconds() < 20:
            return cached
        data, at = await self.get(
            "/fapi/v1/exchangeInfo" if futures else "/api/v3/exchangeInfo", futures=futures
        )
        result = ({x["symbol"]: x for x in data["symbols"]}, at)
        self.cache[key] = result
        return result

    async def books(self, symbol):
        sr, fr = raw_symbols(self.exchange, symbol[:-4])
        mult = self.cache[symbol][1]["baseMultiplier"]

        async def one(raw, future):
            if self.exchange == "okx":
                data, at = await self.get("/api/v5/market/books", {"instId": raw, "sz": 50})
                data = data[0]
                stamp = data.get("ts")
            elif self.exchange == "bitget":
                path = "/api/v2/mix/market/orderbook" if future else "/api/v2/spot/market/orderbook"
                params = {"symbol": raw, "limit": "50"}
                params.update({"productType": "USDT-FUTURES"} if future else {"type": "step0"})
                data, at = await self.get(path, params)
                stamp = data.get("ts")
            elif self.exchange == "gate":
                path = "/futures/usdt/order_book" if future else "/spot/order_book"
                params = {
                    "contract" if future else "currency_pair": raw,
                    "limit": 50,
                    "with_id": "true",
                }
                data, at = await self.get(path, params)
                stamp = (number(data.get("update")) or 0) * 1000 if future else data.get("update")
            else:
                data, at = await self.get(
                    "/fapi/v1/depth" if future else "/api/v3/depth",
                    {"symbol": raw, "limit": 50},
                    futures=future,
                )
                stamp = data.get("T") or data.get("E")
            factor = mult if future else 1

            def levels(side):
                return [
                    [x["p"], float(x["s"]) * factor]
                    if isinstance(x, dict)
                    else [x[0], float(x[1]) * factor]
                    for x in data[side]
                ]

            # A response without an exchange book timestamp remains unknown;
            # received time never masquerades as exchange freshness.
            return {"s": raw, "b": levels("bids"), "a": levels("asks"), "ts": stamp}, at

        return await asyncio.gather(one(sr, False), one(fr, True))

    async def refresh_funding(self, ticker):
        """Do not promote collector fallback schedules into an automatic signal."""
        _, raw = raw_symbols(self.exchange, ticker["symbol"][:-4])
        if self.exchange == "okx":
            (data, at), (marks, _) = await asyncio.gather(
                self.get("/api/v5/public/funding-rate", {"instId": raw}),
                self.get("/api/v5/public/mark-price", {"instType": "SWAP", "instId": raw}),
            )
            item = next(x for x in data if x["instId"] == raw)
            mark = next(x for x in marks if x["instId"] == raw)
            start, end = number(item.get("fundingTime")), number(item.get("nextFundingTime"))
            return {
                **ticker,
                "fundingRate": item.get("fundingRate"),
                "fundingIntervalHour": (end - start) / 3600000 if start and end else None,
                "nextFundingTime": start,
                "markPrice": mark.get("markPx"),
                "source_at": at,
            }
        # These venues currently lack an authenticated borrowing-rate source.
        # Their negative funding remains a discovery observation only.
        return ticker
