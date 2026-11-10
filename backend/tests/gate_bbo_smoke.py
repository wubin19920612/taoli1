"""Opt-in, public-WS-only 30s smoke/fixture recorder; never collected by pytest.

Run from backend with PYTHONPATH=.: python tests/gate_bbo_smoke.py
Exercises the production worker, two sockets, spawn transport and parent cache.
"""
import asyncio
import json
import math
from pathlib import Path
from statistics import median
from time import monotonic, perf_counter, time

FIXTURES = Path(__file__).parent / "fixtures" / "gate_bbo"
SYMBOLS = ["AKE_USDT", "JCT_USDT", "BTC_USDT"]


def quantiles(values):
    ordered = sorted(values)
    return {"samples": len(values), "median_ms": median(values) if values else None,
            "p90_ms": ordered[math.ceil(.9 * len(ordered)) - 1] if ordered else None}


def recording_child(incoming, outgoing):
    from app.exchanges.gate_bbo import GateBboWorker

    records, counts = [], {}
    ages = {kind: [] for kind in ("spot", "future")}
    worker = GateBboWorker()
    handle = worker.handle_message
    def record(kind, raw):
        handle(kind, raw)
        message = json.loads(raw)
        result = message.get("result", {})
        symbol = result.get("s") if isinstance(result, dict) else None
        key = kind, symbol, message.get("event")
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= 3:
            records.append({"kind": kind, "received_epoch_ms": time() * 1000, "message": message})
        if message.get("event") == "update" and isinstance(result, dict) and "t" in result:
            ages[kind].append(time() * 1000 - result["t"])
    worker.handle_message = record
    incoming._writer.close()
    outgoing._reader.close()
    outgoing.cancel_join_thread()
    started = monotonic()
    try:
        asyncio.run(worker.run(incoming, outgoing))
    finally:
        elapsed = monotonic() - started
        FIXTURES.mkdir(parents=True, exist_ok=True)
        (FIXTURES / "recorded.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        summary = {kind: {**quantiles(values), "messages_per_second": len(values) / elapsed}
                   for kind, values in ages.items()}
        (FIXTURES / "smoke-child.json").write_text(json.dumps({
            "elapsed_seconds": elapsed, "connections": summary,
        }, indent=2) + "\n", encoding="utf-8")
        incoming.close()
        outgoing.close()


async def main():
    from app.core.config import Settings
    from app.services import gate_bbo_runtime as module

    module.child_entry = recording_child
    rt = module.GateBboRuntime(Settings(gate_bbo_enabled=True))
    rt.set_subscriptions({kind: SYMBOLS for kind in ("spot", "future")})
    ages = {kind: [] for kind in ("spot", "future")}
    seen = {}
    started = monotonic()
    try:
        await rt.start()
        while monotonic() - started < 30:
            await asyncio.sleep(.02)
            for key, quote in rt._quotes.items():
                if quote is not None and seen.get(key) != quote[5]:
                    seen[key] = quote[5]
                    ages[key[0]].append(time() * 1000 - quote[4])
        status = rt.status()
    finally:
        closing = perf_counter()
        await rt.aclose()
        close_seconds = perf_counter() - closing
    child_path = FIXTURES / "smoke-child.json"
    result = {"duration_seconds": 30, "shutdown_seconds": close_seconds,
        "status": status, "parent_now_minus_t": {kind: quantiles(values) for kind, values in ages.items()},
        "child": json.loads(child_path.read_text()) if child_path.exists() else None,
        "observed_markets": sorted(f"{kind}:{symbol}" for kind, symbol in seen)}
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "smoke-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    assert status["batches"] > 0 and close_seconds < 2
    assert set(seen) == {(kind, symbol) for kind in ("spot", "future") for symbol in SYMBOLS}
    assert all(status["connections"][kind]["live"] for kind in ("spot", "future"))


if __name__ == "__main__":
    asyncio.run(main())
