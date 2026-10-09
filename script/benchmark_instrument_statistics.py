"""Offline, alternating-order historical calculation benchmark; no network."""
import asyncio
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

from app.services.instrument_statistics import InstrumentStatisticsService
from instrument_statistics_reference import ReferenceStatisticsService
from test_instrument_statistics_equivalence import NOW, scenario


async def main():
    output = []
    for count, selection in ((1, "none"), (2, "dense"), (20, "dense"), (20, "sparse"), (20, "none")):
        lookup, histories = scenario(count=count, selection=selection, seed=73)
        services = {"before": ReferenceStatisticsService(), "after": InstrumentStatisticsService()}
        for service in services.values():
            service._cache.update(histories)
        expected = await services["before"].lookup(lookup, now=NOW)
        actual = await services["after"].lookup(lookup, now=NOW)
        assert actual.model_dump_json() == expected.model_dump_json()
        times = {key: [] for key in services}
        for i in range(11):
            for key in (list(services) if i % 2 else list(reversed(services))):
                started = time.perf_counter()
                await services[key].lookup(lookup, now=NOW)
                times[key].append((time.perf_counter() - started) * 1000)
        peaks = {}
        for key, service in services.items():
            tracemalloc.start()
            await service.lookup(lookup, now=NOW)
            peaks[key] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        output.append({"markets": count, "selection": selection, "spreads": len(actual.spreads),
                       "equivalent": True, "median_ms": {k: statistics.median(v) for k, v in times.items()},
                       "peak_python_bytes": peaks})
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
