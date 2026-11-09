"""Public opening funding snapshots. Ratios and UTC seconds, never account data."""
from urllib.parse import urlencode
from .core import dec
from .exchanges import check_response, exact

TTL = 300


def funding_urls(m):
    if m.market_type == "spot":
        return []
    raw, ex = m.raw_symbol, m.exchange
    if ex in ("binance", "aster"):
        # Aster's recommended V3 public API; Binance fundingInfo is an all-symbol
        # configuration endpoint (not a ticker list). Never assume an 8h interval.
        root = ("https://fapi.binance.com/fapi/v1/" if ex == "binance"
                else "https://fapi.asterdex.com/fapi/v3/")
        return [root + "premiumIndex?" + urlencode({"symbol": raw}),
                root + "fundingInfo" + ("?" + urlencode({"symbol": raw}) if ex == "aster" else "")]
    if ex == "gate":
        return ["https://api.gateio.ws/api/v4/futures/usdt/contracts/" + raw]
    if ex == "bybit":
        return ["https://api.bybit.com/v5/market/tickers?" + urlencode({"category": "linear", "symbol": raw})]
    if ex == "okx":
        return ["https://www.okx.com/api/v5/public/funding-rate?" + urlencode({"instId": raw})]
    if ex == "bitget":
        return ["https://api.bitget.com/api/v2/mix/market/current-fund-rate?" + urlencode({"symbol": raw, "productType": "USDT-FUTURES"})]
    raise ValueError("unsupported_market")


def parse_funding(m, data, received_at, info=None):
    if m.market_type == "spot":
        return {"spot": True}
    ex = m.exchange
    check_response(ex, data)
    if ex == "gate":
        r = exact([data], "name", m.raw_symbol)
        rate, nxt, interval = r["funding_rate"], dec(r["funding_next_apply"]), dec(r["funding_interval"])
    elif ex in ("binance", "aster"):
        r = exact(data if isinstance(data, list) else [data], "symbol", m.raw_symbol)
        check_response(ex, info)
        if not isinstance(info, list):
            raise ValueError("funding_interval_unavailable")
        config = exact(info, "symbol", m.raw_symbol)
        rate, nxt = r["lastFundingRate"], dec(r["nextFundingTime"]) / 1000
        interval = dec(config["fundingIntervalHours"]) * 3600
    elif ex == "bybit":
        if data["result"]["category"] != "linear":
            raise ValueError("funding_market_mismatch")
        r = exact(data["result"]["list"], "symbol", m.raw_symbol)
        rate, nxt = r["fundingRate"], dec(r["nextFundingTime"]) / 1000
        interval = dec(r["fundingIntervalHour"]) * 3600
    elif ex == "bitget":
        r = exact(data["data"], "symbol", m.raw_symbol)
        rate, nxt = r["fundingRate"], dec(r["nextUpdate"]) / 1000
        interval = dec(r["fundingRateInterval"]) * 3600
    elif ex == "okx":
        r = exact(data["data"], "instId", m.raw_symbol)
        rate, nxt = r["fundingRate"], dec(r["fundingTime"]) / 1000
        # fundingTime is the upcoming settlement; nextFundingTime is the one AFTER it.
        interval = dec(r["nextFundingTime"]) / 1000 - nxt
    else:
        raise ValueError("unsupported_market")
    server_time = (data.get("time") if isinstance(data, dict) and ex in ("binance", "aster", "bybit") else
                   data.get("requestTime") if ex == "bitget" else r.get("ts") if ex == "okx" else None)
    if server_time is not None and abs(dec(server_time)/1000-dec(received_at)) > 90:
        raise ValueError("stale_funding_response")
    rate = dec(rate)
    if interval <= 0 or nxt <= dec(received_at) or nxt - dec(received_at) > interval + 90:
        raise ValueError("invalid_funding_schedule")
    return dict(rate=str(rate), next=float(nxt), interval=float(interval),
                captured_at=received_at, market_key=m.key, estimated=True)


def fetch_funding(client, m, clock, reserved=False, record=None):
    """At most two sequential GETs; second admission is nonblocking too."""
    urls = funding_urls(m)
    if not urls:
        return {"spot": True}
    payloads = []
    for i, url in enumerate(urls):
        data = client.fetch(m.exchange, url, reserved=reserved and i == 0)
        received = clock()
        if i == 0:
            captured = received
        if record is not None:
            record.append(dict(url=url, received_at=received, response=data))
        payloads.append(data)
    result = parse_funding(m, payloads[0], captured, payloads[1] if len(payloads) > 1 else None)
    result["source_urls"] = urls
    return result


def queue_funding(requests, m, clock):
    """Each GET, including fundingInfo, enters the same opening-priority queue."""
    from concurrent.futures import Future
    result = Future()
    urls, payloads, captured = funding_urls(m), [], []
    current = []

    def cancel_current(future):
        if future.cancelled() and current:
            current[0].cancel()
    result.add_done_callback(cancel_current)
    if not urls:
        result.set_result({"spot": True})
        return result

    def completed(future):
        if result.cancelled():
            return
        try:
            payloads.append(future.result())
            captured.append(clock())
            if len(payloads) < len(urls):
                current[:] = [requests.submit(m.exchange, urls[len(payloads)], 1)]
                current[0].add_done_callback(completed)
            else:
                snapshot = parse_funding(m, payloads[0], captured[0],
                                         payloads[1] if len(payloads) > 1 else None)
                snapshot["source_urls"] = urls
                if result.set_running_or_notify_cancel():
                    result.set_result(snapshot)
        except Exception as exc:
            if result.set_running_or_notify_cancel():
                result.set_exception(exc)
    current[:] = [requests.submit(m.exchange, urls[0], 1)]
    current[0].add_done_callback(completed)
    return result
