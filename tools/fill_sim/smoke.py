"""Explicit live smoke; never invoked by unittest or by report."""
from dataclasses import asdict
import argparse
import json
from pathlib import Path
import time
from .core import EXCHANGES, Market, AMOUNTS, opening
from .exchanges import endpoint, parse_spec, parse_book
from .network import Limiter, PublicClient, RateLimited
from .funding import fetch_funding


class SmokeClient(PublicClient):
    def fetch(self, ex, url, reserved=False):
        if not reserved:
            while not self.limiter.acquire([ex]):
                if self.limiter.stats[ex]["paused_until"] > time.time():
                    raise RateLimited("exchange_paused")
                time.sleep(.15)
        return super().fetch(ex, url, reserved=True)


def run(fixtures=None, include_spot=False):
    client = SmokeClient(Limiter())
    example = (Path(__file__).parent / "deploy/fill-sim.env.example").read_text(encoding="utf-8")
    fee_line = next(line.split("=",1)[1] for line in example.splitlines() if line.startswith("FILL_SIM_FEES_JSON="))
    fees = json.loads(fee_line.strip("'\""))
    failures, books, funding_count = 0, {}, 0
    for market_type in (("future", "spot") if include_spot else ("future",)):
        for ex in EXCHANGES:
            raw = "BTC_USDT" if ex == "gate" else ("BTC-USDT" + ("-SWAP" if market_type == "future" else "") if ex == "okx" else "BTCUSDT")
            m = Market(ex, market_type, raw)
            try:
                payloads = {}
                for kind in ("spec", "depth"):
                    url = endpoint(m, kind)
                    if client.limiter.stats[ex]["paused_until"] > time.time():
                        raise RateLimited("exchange_paused")
                    while not client.limiter.acquire([ex]):
                        time.sleep(0.15)
                    payload = client.fetch(ex, url, reserved=True)
                    received = time.time()
                    if kind == "spec":
                        spec = parse_spec(m, payload)
                        # Retain the exact symbol's unmodified server row, not hundreds of unrelated markets.
                        if ex in ("binance", "aster"):
                            payload["symbols"] = [r for r in payload["symbols"] if r["symbol"] == raw]
                    else:
                        book = parse_book(m, spec, payload, received)
                    payloads[kind] = {"url": url, "received_at": received, "response": payload}
                if fixtures:
                    root = Path(fixtures)
                    root.mkdir(parents=True, exist_ok=True)
                    (root / (ex + "_" + market_type + ".json")).write_text(json.dumps(payloads, indent=2), encoding="utf-8")
                books[ex, market_type] = (book, spec)
                print(json.dumps({"exchange": ex, "market_type": market_type, "raw_symbol": raw,
                                  "spec": asdict(spec), "bids": len(book.bids), "asks": len(book.asks),
                                  "best_bid": book.bids[0], "best_ask": book.asks[0],
                                  "received_at": book.received_at, "exchange_at": book.exchange_at}), flush=True)
                if market_type == "future":
                    records = []
                    funding = fetch_funding(client, m, time.time, record=records)
                    if fixtures:
                        root = Path(fixtures) / "funding"
                        root.mkdir(parents=True, exist_ok=True)
                        # Config arrays are trimmed only after exact-symbol parsing.
                        for row in records:
                            if isinstance(row["response"], list):
                                row["response"] = [r for r in row["response"] if r.get("symbol") == raw]
                        (root / (ex + ".json")).write_text(json.dumps(records, indent=2), encoding="utf-8")
                    print(json.dumps({"exchange": ex, "raw_symbol": raw, "funding": funding}), flush=True)
                    funding_count += 1
            except (OSError, ValueError, KeyError, IndexError, RateLimited) as exc:
                failures += 1
                print(json.dumps({"exchange": ex, "market_type": market_type, "error_type": type(exc).__name__, "error": str(exc)}), flush=True)
    if ("gate", "future") in books and ("aster", "future") in books:
        a, sa = books["gate", "future"]
        b, sb = books["aster", "future"]
        # A hand-written trigger, using the captured books; no taoli1 API/token.
        for amount in AMOUNTS:
            result = opening(a, b, sa, sb, amount, fees["gate"]["future"], fees["aster"]["future"])
            print(json.dumps({"synthetic_trigger": "BTC gate -> aster", "smoke_fee_assumptions": True, "fill": result}), flush=True)
    print("SMOKE exchanges=%d markets=%d failures=%d" % (len(EXCHANGES), len(books), failures), flush=True)
    print("FUNDING exchanges=%d parsed=%d failures=%d" % (len(EXCHANGES), funding_count, len(EXCHANGES)-funding_count), flush=True)
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures")
    parser.add_argument("--include-spot", action="store_true")
    args = parser.parse_args()
    raise SystemExit(bool(run(args.fixtures, args.include_spot)))
