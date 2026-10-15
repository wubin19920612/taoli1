"""Compare the pure collector computation against a reviewed Git revision.

Loads historical Python source from this repository without starting workers or
making network/database calls. Only use trusted repository revisions. Timings
exclude loading/parsing; traced peaks include returned opportunities but exclude
the input. Run with --markets <GET-/api/markets-JSON> --output <report.json>.
"""
import argparse
import gc
import hashlib
import json
import statistics
import subprocess
import sys
import time
import tracemalloc
import types
from datetime import UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.market import MarketSnapshot
from app.services.collector import MarketCollector
from app.services.snapshot_store import SnapshotStore


def load_reference(revision):
    modules = {}
    for name in ("spread_engine", "risk_labels", "collector"):
        path = f"backend/app/services/{name}.py"
        source = subprocess.run(
            ["git", "show", f"{revision}:{path}"], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout
        module = types.ModuleType(f"_collector_reference_{name}")
        sys.modules[module.__name__] = module
        # Execute only reviewed repository source, as stated in the CLI contract.
        exec(compile(source, f"{revision}:{path}", "exec"), module.__dict__)  # noqa: S102
        modules[name] = module
    modules["collector"].build_opportunities = modules["spread_engine"].build_opportunities
    modules["collector"].apply_risk_labels = modules["risk_labels"].apply_risk_labels
    return modules["collector"].MarketCollector([], SnapshotStore())


def measure(markets, baseline, repeat):
    now = max(
        item.timestamp if item.timestamp.tzinfo else item.timestamp.replace(tzinfo=UTC)
        for item in markets
    )
    collectors = {"baseline": baseline, "optimized": MarketCollector([], SnapshotStore())}
    before = baseline._build_labeled_opportunities(markets, now=now)
    after = collectors["optimized"]._build_labeled_opportunities(markets, now=now)
    if len(before) != len(after) or any(
        a.model_dump() != b.model_dump() for a, b in zip(before, after, strict=True)
    ):
        raise AssertionError("Collector output fields or stable ordering differ")
    count = len(after)
    del before, after
    timings = {key: [] for key in collectors}
    for i in range(repeat):
        names = list(collectors) if i % 2 == 0 else list(reversed(collectors))
        for name in names:
            gc.collect()
            start = time.perf_counter()
            result = collectors[name]._build_labeled_opportunities(markets, now=now)
            timings[name].append((time.perf_counter() - start) * 1000)
            del result
    stats = {}
    for name, collector in collectors.items():
        gc.collect()
        tracemalloc.start()
        result = collector._build_labeled_opportunities(markets, now=now)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        del result
        stats[name] = {"median_ms": statistics.median(timings[name]),
                       "samples_ms": timings[name], "peak_python_bytes": peak}
    return {
        "markets": len(markets), "opportunities": count, "outputs_equal": True,
        "now": now.isoformat(), **stats,
        "speedup": stats["baseline"]["median_ms"] / stats["optimized"]["median_ms"],
        "peak_reduction_pct": (1 - stats["optimized"]["peak_python_bytes"]
                               / stats["baseline"]["peak_python_bytes"]) * 100,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", default="3d9bf3b99700eaae15e6e1903b3d92d19c4d69eb")
    parser.add_argument("--repeat", type=int, default=9)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("repeat must be positive")
    source = args.markets.read_bytes()
    markets = [MarketSnapshot.model_validate(item) for item in json.loads(source)]
    if not markets:
        parser.error("market input must not be empty")
    report = {"baseline_revision": args.baseline, "python": sys.version,
              "input_sha256": hashlib.sha256(source).hexdigest(), "repeat": args.repeat,
              **measure(markets, load_reference(args.baseline), args.repeat)}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
