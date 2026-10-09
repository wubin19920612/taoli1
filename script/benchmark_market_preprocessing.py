"""Offline alias/watch normalization comparisons on fixed public market data."""
import argparse
import asyncio
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

from app.models.market import MarketSnapshot
from app.models.settings import RiskSettings
from app.services.collector import MarketCollector
from app.services.snapshot_store import SnapshotStore
from app.services.symbol_aliases import SymbolAliasResolver, apply_symbol_aliases
from symbol_aliases_reference import ReferenceAliasResolver
from test_index_watch_filter_equivalence import reference_filter
from test_symbol_alias_index import aliases_for


async def watch_benchmark(markets):
    results = []
    for count in (0, 1, 30):
        watched = {m.symbol for m in markets[:count]}

        async def watched_symbols(values=watched):
            return values

        collector = MarketCollector([], SnapshotStore(), index_component_monitor=SimpleNamespace(
            watched_symbols=watched_symbols,
        ))
        expected = await reference_filter(collector, markets)
        actual = await collector._index_component_markets(markets)
        assert len(actual) == len(expected)
        assert all(a is b for a, b in zip(actual, expected, strict=True))
        times = {key: [] for key in ("before", "after")}
        for i in range(9):
            for key in (list(times) if i % 2 else list(reversed(times))):
                started = time.perf_counter()
                if key == "before":
                    await reference_filter(collector, markets)
                else:
                    await collector._index_component_markets(markets)
                times[key].append((time.perf_counter() - started) * 1000)
        results.append({"watch_count": len(watched), "selected": len(actual), "equivalent": True,
                        "median_ms": {k: statistics.median(v) for k, v in times.items()}})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", required=True)
    args = parser.parse_args()
    markets = [MarketSnapshot.model_validate(m)
               for m in json.loads(Path(args.markets).read_text(encoding="utf-8-sig"))]
    # Public API snapshots have already applied aliases. Reconstruct inputs to
    # exercise actual positive matches without multiplying prices twice.
    reconstructed = 0
    for i, market in enumerate(markets):
        if market.symbol_alias_original_symbol is not None:
            multiplier = market.symbol_alias_price_multiplier
            original = market.symbol_alias_original_symbol
            values = {field: getattr(market, field) / multiplier
                      for field in ("bid", "ask", "mark_price", "index_price")
                      if getattr(market, field) is not None}
            values.update({field: getattr(market, field) * multiplier
                           for field in ("bid_size", "ask_size")
                           if getattr(market, field) is not None})
            values.update(symbol=original, base=original.removesuffix("USDT"),
                          symbol_alias_original_symbol=None, symbol_alias_price_multiplier=1.0)
            markets[i] = market.model_copy(update=values)
            reconstructed += 1
    report = []
    for label, values, aliases in (
        ("one_market_current_aliases", markets[:1], RiskSettings().symbol_aliases),
        ("current_aliases", markets, RiskSettings().symbol_aliases),
        ("80_alias_entries", markets, aliases_for(73, 80)),
    ):
        classes = {"before": ReferenceAliasResolver, "after": SymbolAliasResolver}
        outputs, times, peaks = {}, {key: [] for key in classes}, {}
        for i in range(11):
            for key in (list(classes) if i % 2 else list(reversed(classes))):
                with patch("app.services.symbol_aliases.SymbolAliasResolver", classes[key]):
                    started = time.perf_counter()
                    outputs[key] = apply_symbol_aliases(values, aliases)
                    times[key].append((time.perf_counter() - started) * 1000)
        assert [m.model_dump() for m in outputs["before"]] == [m.model_dump() for m in outputs["after"]]
        for key, factory in classes.items():
            with patch("app.services.symbol_aliases.SymbolAliasResolver", factory):
                tracemalloc.start()
                apply_symbol_aliases(values, aliases)
                peaks[key] = tracemalloc.get_traced_memory()[1]
                tracemalloc.stop()
        report.append({"case": label, "markets": len(values), "user_alias_entries": len(aliases),
                       "equivalent": True, "median_ms": {k: statistics.median(v) for k, v in times.items()},
                       "peak_python_bytes": peaks})
    print(json.dumps({"reconstructed_alias_inputs": reconstructed, "aliases": report,
                      "watch_filter": asyncio.run(watch_benchmark(markets))}, indent=2))


if __name__ == "__main__":
    main()
