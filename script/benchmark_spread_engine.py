"""Offline, side-effect-free comparison with the frozen 0819151 traversal.

Run from any directory with Python 3.12 and backend dependencies installed:
  python script/benchmark_spread_engine.py --markets public-markets.json --output report.json

The input is the JSON array returned by GET /api/markets. No requests, workers,
notifications or database operations run here. Timing excludes parsing, output
comparison and tracing. Peak allocations include the returned opportunities,
but exclude the preloaded input. This is not a process-RSS or API-latency test.
"""
import argparse
import gc
import hashlib
import json
import statistics
import sys
import time
import tracemalloc
from datetime import UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

from app.models.market import MarketSnapshot
from app.services.spread_engine import build_opportunities
from spread_engine_reference import build_opportunities as reference_build


def compare(markets, repeat, now, stale_after_seconds):
    def run(builder):
        return [
            item
            for mode in ("SF", "FF", "SS")
            for item in builder(markets, mode, now=now, stale_after_seconds=stale_after_seconds)
        ]

    expected = [item.model_dump(mode="json") for item in run(reference_build)]
    actual = [item.model_dump(mode="json") for item in run(build_opportunities)]
    if actual != expected:
        raise AssertionError("Output fields or ordering differ from the original traversal")
    count = len(expected)
    del expected, actual
    builders = {"baseline": reference_build, "optimized": build_opportunities}
    durations = {name: [] for name in builders}
    # Alternate execution order to reduce warm-cache / temperature bias.
    for iteration in range(repeat):
        order = list(builders) if iteration % 2 == 0 else list(reversed(builders))
        for name in order:
            gc.collect()
            start = time.perf_counter()
            results = run(builders[name])
            durations[name].append((time.perf_counter() - start) * 1000)
            del results
    peaks = {}
    for name, builder in builders.items():
        gc.collect()
        tracemalloc.start()
        results = run(builder)
        _, peaks[name] = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        del results
    stats = {}
    for name, samples in durations.items():
        stats[name] = {
            "median_ms": statistics.median(samples),
            "min_ms": min(samples),
            "max_ms": max(samples),
            "samples_ms": samples,
            "peak_python_bytes": peaks[name],
        }
    return {
        "markets": len(markets), "opportunities": count, "outputs_equal": True,
        "now": now.isoformat(), "stale_after_seconds": stale_after_seconds,
        **stats,
        "speedup": stats["baseline"]["median_ms"] / stats["optimized"]["median_ms"],
        "peak_reduction_pct": (1 - peaks["optimized"] / peaks["baseline"]) * 100,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=9)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    source = args.markets.read_bytes()
    markets = [MarketSnapshot.model_validate(item) for item in json.loads(
        source.decode("utf-8-sig")
    )]
    if not markets:
        parser.error("market input must not be empty")
    now = max(
        stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
        for item in markets for stamp in [item.timestamp]
    )
    report = {
        "baseline": "0819151 pair traversal; unchanged pricing/model helpers",
        "python": sys.version,
        "input_sha256": hashlib.sha256(source).hexdigest(),
        "repeat": args.repeat,
        "all_markets": compare(markets, args.repeat, now, None),
        "fresh_30s": compare(markets, args.repeat, now, 30),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
