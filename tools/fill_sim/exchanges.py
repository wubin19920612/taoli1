"""Exact-symbol, public GET endpoints only. No ticker lists or credentials."""
from urllib.parse import urlencode
from .core import Book, Spec, dec, common_step


def endpoint(m, kind):
    ex, spot, raw = m.exchange, m.market_type == "spot", m.raw_symbol
    if ex == "gate":
        root = "https://api.gateio.ws/api/v4/"
        if kind == "spec":
            return root + ("spot/currency_pairs/" if spot else "futures/usdt/contracts/") + raw
        path = "spot/order_book" if spot else "futures/usdt/order_book"
        return root + path + "?" + urlencode({"currency_pair" if spot else "contract": raw, "limit": 50})
    if ex in ("binance", "aster"):
        root = ({True: "https://data-api.binance.vision/api/v3/", False: "https://fapi.binance.com/fapi/v1/"}
                if ex == "binance" else
                {True: "https://sapi.asterdex.com/api/v1/", False: "https://fapi.asterdex.com/fapi/v1/"})[spot]
        return root + (("exchangeInfo" + ("?" + urlencode({"symbol": raw}) if spot else "")) if kind == "spec" else "depth?" + urlencode({"symbol": raw, "limit": 50}))
    if ex == "bybit":
        return "https://api.bybit.com/v5/market/" + ("instruments-info" if kind == "spec" else "orderbook") + "?" + urlencode(
            {"category": "spot" if spot else "linear", "symbol": raw, **({} if kind == "spec" else {"limit": 50})})
    if ex == "okx":
        if kind == "spec":
            return "https://www.okx.com/api/v5/public/instruments?" + urlencode({"instType": "SPOT" if spot else "SWAP", "instId": raw})
        return "https://www.okx.com/api/v5/market/books?" + urlencode({"instId": raw, "sz": 50})
    if ex == "bitget":
        if spot:
            path = "spot/public/symbols" if kind == "spec" else "spot/market/orderbook"
            params = {"symbol": raw, **({} if kind == "spec" else {"type": "step0", "limit": 50})}
        else:
            path = "mix/market/contracts" if kind == "spec" else "mix/market/merge-depth"
            params = {"symbol": raw, "productType": "USDT-FUTURES", **({} if kind == "spec" else {"precision": "scale0", "limit": 50})}
        return "https://api.bitget.com/api/v2/" + path + "?" + urlencode(params)
    raise ValueError("unsupported_market")


def check_response(ex, data):
    if isinstance(data, list) and ex in ("binance", "aster"):
        for row in data:
            check_response(ex, row)
        return
    if not isinstance(data, dict):
        raise ValueError("invalid_response")
    code = str(data.get("retCode" if ex == "bybit" else "code", "0"))
    if code in ("429", "418", "10006", "10429", "50011", "50040", "50061", "-1003", "-1015", "429000"):
        raise ValueError("rate_limited")
    if code not in ("0", "00000", "200") or data.get("label"):
        if "LIMIT" in str(data.get("label", "")) or data.get("label") == "TOO_MANY_REQUESTS":
            raise ValueError("rate_limited")
        raise ValueError("exchange_error")


def exact(rows, field, raw):
    matches = [r for r in rows if r.get(field) == raw]
    if len(matches) != 1:
        raise ValueError("missing_or_ambiguous_spec")
    return matches[0]


def parse_spec(m, data):
    check_response(m.exchange, data)
    ex, spot = m.exchange, m.market_type == "spot"
    cs, step, minimum, notional, maximum = dec(1), None, None, dec(0), dec("1e30")
    if ex == "gate":
        r = data
        if r["id" if spot else "name"] != m.raw_symbol:
            raise ValueError("spec_mismatch")
        if spot:
            if r.get("trade_status") != "tradable":
                raise ValueError("inactive_market")
            base, quote = r["base"], r["quote"]
            step = dec(10) ** -int(r["amount_precision"])
            minimum, notional = dec(r["min_base_amount"]), dec(r["min_quote_amount"])
            maximum = dec(r.get("max_base_amount") or "1e30")
        else:
            if r.get("in_delisting") or r.get("type") not in (None, "direct"):
                raise ValueError("unsupported_spec")
            base, quote = m.raw_symbol.rsplit("_", 1)
            cs = dec(r["quanto_multiplier"])
            # Gate's minimum size is also the allowed lot quantum (decimal lots supported).
            step = cs * dec(r["order_size_min"])
            minimum, maximum = step, cs * dec(r["order_size_max"])
    elif ex in ("binance", "aster"):
        r = exact(data["symbols"], "symbol", m.raw_symbol)
        if r["status"] != "TRADING" or (not spot and r.get("contractType") != "PERPETUAL"):
            raise ValueError("inactive_or_nonperpetual")
        base, quote = r["baseAsset"], r["quoteAsset"]
        if not spot and r.get("marginAsset", "USDT") != "USDT":
            raise ValueError("unsupported_spec")
        filters = {x["filterType"]: x for x in r["filters"]}
        lot = filters.get("MARKET_LOT_SIZE", filters["LOT_SIZE"])
        if dec(lot["stepSize"]) == 0:
            lot = filters["LOT_SIZE"]
        step, minimum, maximum = dec(lot["stepSize"]), dec(lot["minQty"]), dec(lot["maxQty"])
        # Both LOT_SIZE and MARKET_LOT_SIZE apply; a zero step disables only that filter.
        for name in ("LOT_SIZE", "MARKET_LOT_SIZE"):
            rule = filters.get(name, {})
            if dec(rule.get("stepSize", 0)) > 0:
                step = common_step(step, rule["stepSize"])
            minimum = max(minimum, dec(rule.get("minQty", 0)))
            if dec(rule.get("maxQty", 0)) > 0:
                maximum = min(maximum, dec(rule["maxQty"]))
        n = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
        notional = dec(n.get("minNotional", n.get("notional", 0)))
    elif ex == "bitget":
        r = exact(data["data"], "symbol", m.raw_symbol)
        if r.get("status" if spot else "symbolStatus") not in ("online", "normal"):
            raise ValueError("inactive_market")
        base, quote = r["baseCoin"], r["quoteCoin"]
        if spot:
            step = dec(10) ** -int(r["quantityPrecision"])
            minimum, notional = dec(r.get("minTradeAmount") or 0), dec(r["minTradeUSDT"])
        else:
            step, minimum, notional = dec(r["sizeMultiplier"]), dec(r["minTradeNum"]), dec(r["minTradeUSDT"])
            maximum = dec(r.get("maxMarketOrderQty") or "1e30")
    elif ex == "bybit":
        r = exact(data["result"]["list"], "symbol", m.raw_symbol)
        if r["status"] != "Trading" or (not spot and r.get("contractType") != "LinearPerpetual"):
            raise ValueError("inactive_or_nonperpetual")
        base, quote = r["baseCoin"], r["quoteCoin"]
        lot = r["lotSizeFilter"]
        step = dec(lot["basePrecision" if spot else "qtyStep"])
        minimum = dec(lot.get("minOrderQty") or 0)
        notional = dec(lot["minOrderAmt" if spot else "minNotionalValue"])
        maximum = dec(lot.get("maxMktOrderQty") or lot.get("maxMarketOrderQty") or "1e30")
    elif ex == "okx":
        r = exact(data["data"], "instId", m.raw_symbol)
        if r["state"] != "live":
            raise ValueError("inactive_market")
        base = r["baseCcy"] if spot else r["ctValCcy"]
        quote = r["quoteCcy"] if spot else r["settleCcy"]
        if not spot:
            if r["ctType"] != "linear" or base != m.raw_symbol.split("-")[0]:
                raise ValueError("unsupported_spec")
            cs = dec(r["ctVal"]) * dec(r.get("ctMult") or 1)
        step, minimum = dec(r["lotSz"]) * cs, dec(r["minSz"]) * cs
        maximum = dec(r.get("maxMktSz") or "1e30") * cs
    else:
        raise ValueError("unsupported_market")
    spec = Spec(base, quote, str(cs), str(step), str(minimum), str(notional), max_qty=str(maximum))
    spec.validate(m)
    return spec


def parse_book(m, spec, data, received_at):
    check_response(m.exchange, data)
    r, ts = data, None
    if m.exchange == "bybit":
        r = data["result"]
        if r.get("s") != m.raw_symbol:
            raise ValueError("book_symbol_mismatch")
        bids, asks, ts = r["b"], r["a"], r.get("cts", r.get("ts", data.get("time")))
    elif m.exchange == "okx":
        r = data["data"][0]
        bids, asks, ts = r["bids"], r["asks"], r.get("ts")
    elif m.exchange == "bitget":
        r = data["data"]
        bids, asks, ts = r["bids"], r["asks"], r.get("ts")
    else:
        bids, asks = r["bids"], r["asks"]
        if m.exchange == "gate":
            ts = r.get("update", r.get("current"))
            if ts is not None and float(ts) < 1e11:
                ts = float(ts) * 1000
        else:
            ts = r.get("T", r.get("E"))
    factor = dec(spec.contract_size) if m.market_type == "future" and m.exchange in ("gate", "okx") else dec(1)

    def levels(rows, reverse):
        result = []
        for row in rows:
            p, q = (row["p"], row["s"]) if isinstance(row, dict) else row[:2]
            p, q = dec(p), dec(q) * factor
            if p <= 0 or q < 0:
                raise ValueError("invalid_book")
            if q:
                result.append((str(p), str(q)))
        return sorted(result, key=lambda x: dec(x[0]), reverse=reverse)[:50]
    bids, asks = levels(bids, True), levels(asks, False)
    if not bids or not asks or dec(bids[0][0]) >= dec(asks[0][0]):
        raise ValueError("empty_or_crossed_book")
    exchange_at = float(ts) / 1000 if ts is not None else None
    if exchange_at is not None and (received_at - exchange_at > 60 or exchange_at - received_at > 5):
        raise ValueError("stale_book")
    return Book(bids, asks, received_at, exchange_at)
