"""Run from backend: PYTHONPATH=. python scripts/benchmark_spread_events.py.

Real schema, 7 days / 120 seconds * 100 series = 504000 rows. Fixture creation
is outside tracemalloc/timing; measurements cover the entire async read-only query,
including thread/connection setup, streaming aggregation, ranking and enrichment.
tracemalloc reports Python allocations, not SQLite native caches or process RSS.
"""

import asyncio
import gc
import json
import sqlite3
import tempfile
import time
import tracemalloc
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.models.spread_events import SpreadEventsParams
from app.services.spread_events import query_spread_events

BASE = datetime(2026, 11, 1, tzinfo=UTC)
STEPS = 7 * 24 * 3600 // 120
SERIES = 100


async def benchmark():
    with tempfile.TemporaryDirectory(prefix="spread-events-benchmark-") as directory:
        path = Path(directory) / "history.db"
        setup = await connect_database(str(path))
        try:
            await initialize_schema(setup)
        finally:
            await setup.close()

        def rows():
            for step in range(STEPS):
                at = (BASE + timedelta(seconds=step * 120)).isoformat()
                for series in range(SERIES):
                    # Frequent completed events stress bounded top-limit selection.
                    value = 1000 if step % 5 == 4 else 3000 + series * 100 + step % 4 * 10
                    identity = json.dumps({
                        "buy_market": {"exchange": "binance", "market_type": "future",
                                       "raw_symbol": f"COIN{series}USDT", "dex": None},
                        "sell_market": {"exchange": "okx", "market_type": "future",
                                        "raw_symbol": f"COIN{series}-USDT-SWAP", "dex": None},
                    })
                    yield at, f"series-{series:03d}", f"COIN{series}USDT", value, value, identity

        with closing(sqlite3.connect(path)) as db:
            db.executemany(
                "INSERT INTO opportunity_history (observed_at, opportunity_id, type, symbol, "
                "buy_exchange, buy_market_type, sell_exchange, sell_market_type, "
                "open_spread_scaled, close_spread_scaled, fee_adjusted_open_scaled, "
                "spread_width_scaled, market_identity_json) "
                "VALUES (?, ?, 'FF', ?, 'binance', 'future', 'okx', 'future', ?, 0, ?, 0, ?)",
                rows(),
            )
            db.commit()

        for sort in ("peak", "duration", "start"):
            params = SpreadEventsParams(start_at=BASE, end_at=BASE + timedelta(days=7),
                                        sort=sort, limit=500)
            gc.collect()
            tracemalloc.start()
            started = time.perf_counter()
            try:
                result = await query_spread_events(path, params)
                elapsed = time.perf_counter() - started
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            print(json.dumps({
                "fixture_rows": STEPS * SERIES, "sort": sort, "limit": params.limit,
                "scanned_rows": result.scanned_rows, "series_count": result.series_count,
                "events": len(result.events), "truncated": result.truncated,
                "peak_extra_mib": round(peak / 1024**2, 3), "elapsed_s": round(elapsed, 3),
            }), flush=True)
            assert result.scanned_rows == 500_000 and result.series_count == SERIES
            assert len(result.events) == 500 and result.truncated
            assert peak <= 100 * 1024**2, "Python allocation peak exceeded 100 MiB"


if __name__ == "__main__":
    asyncio.run(benchmark())
