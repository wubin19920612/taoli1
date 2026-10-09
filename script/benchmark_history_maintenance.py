"""Offline SQLite maintenance comparison using disposable synthetic databases.

Measures only the maintenance call, not fixture creation/copying. Preserved rows
and integrity are verified afterwards. The databases never contain user data.
"""
import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.database import connect_database
from app.db.repositories import OpportunityHistoryRepository


async def run_case(directory, removed_mb, repeat):
    seed = directory / f"seed-{removed_mb}.db"
    with closing(sqlite3.connect(seed)) as db:
        db.execute("CREATE TABLE preserved (id INTEGER PRIMARY KEY, data BLOB)")
        db.executemany("INSERT INTO preserved VALUES (?, zeroblob(1048576))",
                       [(i,) for i in range(64)])
        db.execute("DELETE FROM preserved WHERE id < ?", (removed_mb,))
        db.commit()
        expected = db.execute("SELECT COUNT(*),SUM(id),SUM(length(data)) FROM preserved").fetchone()
        initial = {name: db.execute(f"PRAGMA {name}").fetchone()[0]
                   for name in ("page_count", "page_size", "freelist_count")}
    results = {name: [] for name in ("unconditional", "adaptive")}
    for i in range(repeat):
        names = list(results) if i % 2 == 0 else list(reversed(results))
        for name in names:
            target = directory / f"{removed_mb}-{i}-{name}.db"
            with closing(sqlite3.connect(seed)) as source, closing(sqlite3.connect(target)) as copy:
                source.backup(copy)
            db = await connect_database(str(target))
            try:
                repo = OpportunityHistoryRepository(db)
                start = time.perf_counter()
                if name == "unconditional":
                    await repo.vacuum()
                    compacted = True
                else:
                    compacted = await repo.vacuum_if_beneficial()
                elapsed = (time.perf_counter() - start) * 1000
                async with db.execute("PRAGMA integrity_check") as cursor:
                    assert (await cursor.fetchone())[0] == "ok"
                async with db.execute("SELECT COUNT(*),SUM(id),SUM(length(data)) FROM preserved") as cursor:
                    assert tuple(await cursor.fetchone()) == expected
                async with db.execute("SELECT page_count,page_size FROM pragma_page_count(),pragma_page_size()") as cursor:
                    pages, size = await cursor.fetchone()
                results[name].append({"ms": elapsed, "vacuum_executed": compacted,
                                      "logical_database_bytes_after": pages * size})
            finally:
                await db.close()
    return {"total_payload_mb": 64, "removed_payload_mb": removed_mb,
            "initial_pages": initial, "rows_and_integrity_preserved": True,
            **{name: {"median_ms": statistics.median(item["ms"] for item in samples),
                      "samples": samples} for name, samples in results.items()}}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("repeat must be positive")
    parent = (ROOT / "output").resolve()
    with tempfile.TemporaryDirectory(prefix="history-maintenance-", dir=parent) as temporary:
        directory = Path(temporary).resolve()
        assert directory.is_relative_to(parent)
        report = {"sqlite_version": sqlite3.sqlite_version, "repeat": args.repeat,
                  "small_reclaim": await run_case(directory, 2, args.repeat),
                  "large_reclaim": await run_case(directory, 24, args.repeat)}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
