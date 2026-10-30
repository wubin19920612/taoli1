"""Offline equivalence, retained-size, TTL and ownership checks for M2-3a."""

import asyncio
import gc
import json
import sys
import weakref
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.services import fs_public
from app.services.fs_public import PublicFsProvider, daily_to_hourly, raw_symbols


class LegacyPublicFsProvider(PublicFsProvider):
    # Verbatim methods from 1bfc8f43a40e1c6dadb9e5dab65025ffd3445d92.
    # Freeze the readers too, so a changed reader cannot make both sides agree.
    async def borrow_reference(self):
        ex = self.exchange
        if ex == "okx":
            data, at = await self.get("/api/v5/public/interest-rate-loan-quota")
            payload = data[0]
            rates = {
                x["ccy"]: {
                    "borrowable": True,
                    "hourlyBorrowRate": daily_to_hourly(x.get("rate")),
                    "maxBorrowingAmount": x.get("quota"),
                }
                for x in data[0]["basic"]
            }
            # Customized currencies must NOT use basic quota or VIP multipliers.
            # Display the public Lv1/general reference without inferring the user's tier.
            for coin in payload.get("configCcyList", []):
                ccy = coin["ccy"] if isinstance(coin, dict) else coin
                matches = [
                    x
                    for x in payload.get("config", [])
                    if x.get("ccy") == ccy and x.get("level") == "Lv1" and x.get("stgyType") == "0"
                ]
                rates[ccy] = {
                    "borrowable": True,
                    "hourlyBorrowRate": (
                        daily_to_hourly(coin.get("rate")) if isinstance(coin, dict) else None
                    ),
                    "maxBorrowingAmount": matches[0].get("quota") if len(matches) == 1 else None,
                    "publicScope": "okx_lv1",
                }
            return rates, at
        if ex == "bitget":
            data, at = await self.get("/api/v2/margin/currencies")
            return {
                x["baseCoin"]: {
                    "borrowable": x.get("isCrossBorrowable") is True and x.get("status") == "1",
                    "minimumBorrowAmount": x.get("userMinBorrow"),
                }
                for x in data
                if x.get("quoteCoin") == "USDT"
            }, at
        if ex == "gate":
            data, at = await self.get("/margin/uni/currency_pairs")
            return {
                x["currency_pair"][:-5]: {
                    "borrowable": x.get("status") == "enabled",
                    "minimumBorrowAmount": x.get("base_min_borrow_amount"),
                }
                for x in data
                if x.get("currency_pair", "").endswith("_USDT")
            }, at
        # Margin market support is public; it does NOT prove base-coin borrowability.
        specs, at = await self.binance_specs(False)
        return {
            x["baseAsset"]: {"marginTradingAllowed": x.get("isMarginTradingAllowed")}
            for x in specs.values()
            if x.get("quoteAsset") == "USDT" and x.get("symbol") == x.get("baseAsset", "") + "USDT"
        }, at


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


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 10, 26, 12, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            assert tz == UTC
            return cls.current

    monkeypatch.setattr(fs_public, "datetime", Clock)
    monkeypatch.setitem(globals(), "datetime", Clock)
    return Clock


def exchange_info(count=1, *, futures=False):
    """Representative JSON fields, including permission sets and unused filters."""
    symbols = []
    for index in range(count):
        base = "ABC" if index == 0 else f"COIN{index}"
        symbols.append({
            "symbol": base + "USDT", "status": "TRADING", "baseAsset": base,
            "quoteAsset": "USDT", "isMarginTradingAllowed": True,
            "contractType": "PERPETUAL", "marginAsset": "USDT",
            "baseAssetPrecision": 8, "quotePrecision": 8, "quoteAssetPrecision": 8,
            "baseCommissionPrecision": 8, "quoteCommissionPrecision": 8,
            "orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS",
                           "STOP_LOSS_LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"],
            "icebergAllowed": True, "ocoAllowed": True, "otoAllowed": True,
            "quoteOrderQtyMarketAllowed": True, "allowTrailingStop": True,
            "cancelReplaceAllowed": True, "isSpotTradingAllowed": not futures,
            "permissions": ["SPOT", "MARGIN"],
            "permissionSets": [["SPOT", "MARGIN"] + [f"TRD_GRP_{i:03}" for i in range(1, 26)]],
            "defaultSelfTradePreventionMode": "EXPIRE_MAKER",
            "allowedSelfTradePreventionModes": ["EXPIRE_TAKER", "EXPIRE_MAKER", "EXPIRE_BOTH"],
            "filters": [
                {"filterType": "PRICE_FILTER", "minPrice": "0.00000100",
                 "maxPrice": "100000.00000000", "tickSize": "0.00000100"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00100000",
                 "minQty": "0.01000000", "maxQty": "999999.00000000"},
                {"filterType": "MIN_NOTIONAL" if futures else "NOTIONAL",
                 "notional" if futures else "minNotional": "5.00000000",
                 "maxNotional": "9000000.00000000", "applyMinToMarket": True,
                 "applyMaxToMarket": False, "avgPriceMins": 5},
                {"filterType": "PERCENT_PRICE_BY_SIDE", "bidMultiplierUp": "5",
                 "bidMultiplierDown": "0.2", "askMultiplierUp": "5",
                 "askMultiplierDown": "0.2", "avgPriceMins": 5},
                {"filterType": "MARKET_LOT_SIZE", "minQty": "0.00000000",
                 "maxQty": "999999.00000000", "stepSize": "0.00000000"},
                {"filterType": "ICEBERG_PARTS", "limit": 10},
                {"filterType": "TRAILING_DELTA", "minTrailingAboveDelta": 10,
                 "maxTrailingAboveDelta": 2000, "minTrailingBelowDelta": 10,
                 "maxTrailingBelowDelta": 2000},
                {"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200},
                {"filterType": "MAX_NUM_ALGO_ORDERS", "maxNumAlgoOrders": 5},
            ],
        })
    return {"timezone": "UTC", "serverTime": 1793016000000, "rateLimits": [],
            "exchangeFilters": [], "symbols": symbols}


def variant(case, futures):
    body = exchange_info(futures=futures)
    item = body["symbols"][0]
    if case.startswith("missing_") and case != "missing_base_keyerror":
        del item[case.removeprefix("missing_")]
    elif case == "min_notional_only":
        item["filters"][2] = {"filterType": "MIN_NOTIONAL", "notional": "7.50000000"}
    elif case == "no_notional":
        del item["filters"][2]
    elif case == "both_notional":
        item["filters"][2] = {"filterType": "NOTIONAL", "minNotional": None, "notional": "9"}
        item["filters"].append({"filterType": "MIN_NOTIONAL", "minNotional": "99"})
    elif case == "empty_notional":
        item["filters"][2] = {"filterType": "NOTIONAL"}
        item["filters"].append({"filterType": "MIN_NOTIONAL", "minNotional": "99"})
    elif case == "duplicate_lot":
        item["filters"].append({"filterType": "LOT_SIZE", "stepSize": "2", "minQty": "3"})
    elif case == "duplicate_symbol":
        body["symbols"].append(deepcopy(item))
        body["symbols"][-1]["isMarginTradingAllowed"] = False
    elif case == "closed":
        item["status"] = "BREAK"
    elif case == "dated":
        item["contractType"] = "CURRENT_QUARTER"
    elif case == "wrong_margin":
        item["marginAsset"] = "USDC"
    elif case == "wrong_base":
        item["baseAsset"] = "XYZ"
    elif case == "wrong_quote":
        item["quoteAsset"] = "USDC"
    elif case == "absent_symbol":
        body["symbols"] = []
    elif case == "null_margin_flag":
        item["isMarginTradingAllowed"] = None
    elif case == "string_margin_flag":
        item["isMarginTradingAllowed"] = "true"
    elif case == "false_margin_flag":
        item["isMarginTradingAllowed"] = False
    elif case == "missing_base_keyerror":
        item.pop("baseAsset")
        item["symbol"] = "USDT"
    elif case == "null_base":
        item["baseAsset"] = None
    elif case == "null_filters":
        item["filters"] = None
    elif case == "empty_filters":
        item["filters"] = []
    elif case == "no_lot":
        del item["filters"][1]
    elif case == "no_step":
        del item["filters"][1]["stepSize"]
    elif case == "no_min_qty":
        del item["filters"][1]["minQty"]
    elif case == "no_filter_type":
        del item["filters"][0]["filterType"]
    elif case == "null_filter":
        item["filters"].append(None)
    elif case == "unhashable_filter_type":
        item["filters"][0]["filterType"] = []
    return body


async def outcome(call):
    try:
        value = await call()
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        return "error", type(exc), exc.args
    # Unsorted JSON also checks order and the exact spelling of numeric strings.
    encoded = json.dumps(value, default=lambda at: at.isoformat(), ensure_ascii=False).encode()
    return "result", value, encoded


@pytest.mark.parametrize("futures", [False, True], ids=["spot", "future"])
@pytest.mark.parametrize("case", [
    "normal", "min_notional_only", "no_notional", "both_notional", "empty_notional",
    "duplicate_lot", "duplicate_symbol", "closed", "dated", "wrong_margin",
    "wrong_base", "wrong_quote", "absent_symbol", "null_margin_flag", "string_margin_flag",
    "false_margin_flag", "missing_symbol", "missing_status", "missing_baseAsset",
    "missing_quoteAsset", "missing_contractType", "missing_marginAsset",
    "missing_isMarginTradingAllowed", "missing_filters", "missing_base_keyerror",
    "null_base", "null_filters", "empty_filters", "no_lot", "no_step", "no_min_qty",
    "no_filter_type", "null_filter", "unhashable_filter_type",
])
async def test_readers_match_legacy_byte_for_byte(clock, case, futures):
    payloads = {side: variant(case if side == futures else "normal", side)
                for side in (False, True)}

    def handler(request):
        return httpx.Response(200, json=payloads[request.url.host == "fapi.binance.com"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        new = PublicFsProvider("binance", None, client)
        old = LegacyPublicFsProvider("binance", None, client)
        assert await outcome(new.borrow_reference) == await outcome(old.borrow_reference)
        assert await outcome(lambda: new.current_instruments("ABCUSDT")) == await outcome(
            lambda: old.current_instruments("ABCUSDT")
        )
        # Repeat through cached data, including unchanged per-symbol meta entries.
        assert await outcome(new.borrow_reference) == await outcome(old.borrow_reference)
        assert await outcome(lambda: new.current_instruments("ABCUSDT")) == await outcome(
            lambda: old.current_instruments("ABCUSDT")
        )
        assert new.cache.get("ABCUSDT") == old.cache.get("ABCUSDT")


def exact_deep_size(root):
    """Exact traversal of this JSON cache, deduplicating identities, no sampling."""
    seen, pending, total = set(), [root], 0
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        total += sys.getsizeof(value)
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, (tuple, list)):
            pending.extend(value)
    return total


async def test_cache_retains_at_least_80_percent_fewer_bytes(clock):
    def handler(request):
        future = request.url.host == "fapi.binance.com"
        return httpx.Response(200, json=exchange_info(600 if future else 3000, futures=future))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        old = LegacyPublicFsProvider("binance", None, client)
        new = PublicFsProvider("binance", None, client)
        for provider in (old, new):
            await provider.binance_specs(False)
            await provider.binance_specs(True)
        old_bytes, new_bytes = exact_deep_size(old.cache), exact_deep_size(new.cache)
        print(f"\nCache bytes: legacy={old_bytes}, slim={new_bytes}, "
              f"reduction={1 - new_bytes / old_bytes:.4%}")
        assert new_bytes <= old_bytes * 0.2
        assert new_bytes < 5_000_000


@pytest.mark.parametrize("futures", [False, True])
async def test_twenty_second_ttl_and_response_time(clock, futures):
    requests = []

    def handler(request):
        requests.append(request)
        clock.current += timedelta(milliseconds=250)
        return httpx.Response(200, json=exchange_info(futures=futures))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = PublicFsProvider("binance", None, client)
        first = await provider.binance_specs(futures)
        assert first[1] == clock.current  # HTTP completion, not request start.
        clock.current = first[1] + timedelta(seconds=19, milliseconds=999)
        assert await provider.binance_specs(futures) is first
        assert len(requests) == 1
        clock.current = first[1] + timedelta(seconds=20)
        second = await provider.binance_specs(futures)
        assert len(requests) == 2 and second[1] == clock.current > first[1]
        assert second is provider.cache[("binance_specs", futures)]
        clock.current = second[1] + timedelta(seconds=21)
        third = await provider.binance_specs(futures)
        assert len(requests) == 3 and third[1] == clock.current > second[1]


class WeakDict(dict):
    """Weak-referenceable stand-in for dictionaries produced by JSON decoding."""


@pytest.mark.parametrize("futures", [False, True])
@pytest.mark.parametrize("missing_symbol", [False, True], ids=["success", "traceback"])
async def test_decoded_payload_and_raw_symbol_dicts_are_released(clock, futures, missing_symbol):
    references = []

    class TrackingResponse(httpx.Response):
        def json(self, **kwargs):
            payload = WeakDict(super().json(**kwargs))
            payload["symbols"] = [WeakDict(item) for item in payload["symbols"]]
            references.extend(weakref.ref(value) for value in [payload, *payload["symbols"]])
            if missing_symbol:
                del payload["symbols"][-1]["symbol"]
            return payload

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: TrackingResponse(200, json=exchange_info(3, futures=futures))
    )) as client:
        provider = PublicFsProvider("binance", None, client)
        if missing_symbol:
            with pytest.raises(KeyError, match="symbol") as caught:
                await provider.binance_specs(futures)
            assert caught.value.__traceback__ is not None
            assert ("binance_specs", futures) not in provider.cache
        else:
            result = await provider.binance_specs(futures)
            assert result is provider.cache[("binance_specs", futures)]
        gc.collect()
        assert len(references) == 4
        assert all(ref() is None for ref in references)
