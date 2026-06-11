import argparse
import asyncio

from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.exchanges.binance import BinanceAdapter
from app.exchanges.bitget import BitgetAdapter
from app.exchanges.gate import GateAdapter
from app.exchanges.okx import OKXAdapter
from app.services.funding_research import (
    FundingResearchRepository,
    FundingResearchSettings,
    record_funding_research_run,
    summarize_paper_trades,
)


def _adapters():
    return [BinanceAdapter(), OKXAdapter(), GateAdapter(), BitgetAdapter()]


async def run_once(
    *,
    database_path: str,
    manage_paper_trades: bool,
    snapshot_retention_hours: float,
) -> None:
    adapters = _adapters()
    markets = []
    try:
        for adapter in adapters:
            try:
                rows = await adapter.fetch_future_tickers()
                print(f"{adapter.name}: {len(rows)} futures markets")
                markets.extend(rows)
            except Exception as exc:  # noqa: BLE001 - CLI should continue other exchanges.
                print(f"{adapter.name}: failed: {exc}")
        db = await connect_database(database_path)
        try:
            await initialize_schema(db)
            repo = FundingResearchRepository(db)
            result = await record_funding_research_run(
                markets=markets,
                repo=repo,
                settings=FundingResearchSettings(
                    snapshot_retention_hours=snapshot_retention_hours,
                ),
                manage_paper_trades=manage_paper_trades,
                depth_adapters=adapters,
                orderbook_depth_levels=20,
            )
            print(f"recorded market snapshots: {result.market_snapshot_count}")
            print(f"recorded candidate snapshots: {result.candidate_snapshot_count}")
            print(f"pruned expired snapshots: {result.pruned_snapshot_count}")
            print(f"opened paper trades: {len(result.opened_paper_trades)}")
            print(f"closed paper trades: {len(result.closed_paper_trades)}")
            for item in result.candidates[:10]:
                ev = "n/a" if item.ev_pct is None else f"{item.ev_pct:.4f}%"
                funding = (
                    "n/a"
                    if item.expected_net_funding_pct is None
                    else f"{item.expected_net_funding_pct:.4f}%"
                )
                print(
                    f"{item.symbol} {item.long_exchange}/LONG {item.short_exchange}/SHORT "
                    f"{item.decision} ev={ev} score={item.score:.2f} funding={funding} "
                    f"basis={item.expected_basis_change_pct:.4f}%"
                )
            trades = await repo.list_paper_trades(limit=10_000)
            print(summarize_paper_trades(trades).model_dump_json())
        finally:
            await db.close()
    finally:
        for adapter in adapters:
            await adapter.client.aclose()


async def run_loop(
    *,
    database_path: str,
    manage_paper_trades: bool,
    snapshot_retention_hours: float,
    interval_seconds: float,
    max_iterations: int | None = None,
) -> None:
    iteration = 0
    while True:
        iteration += 1
        print(f"funding research scan #{iteration}")
        await run_once(
            database_path=database_path,
            manage_paper_trades=manage_paper_trades,
            snapshot_retention_hours=snapshot_retention_hours,
        )
        if max_iterations is not None and iteration >= max_iterations:
            return
        await asyncio.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run funding research scan once.")
    parser.add_argument("--db", default="data/radar.db", help="SQLite database path")
    parser.add_argument(
        "--manage-paper-trades",
        action="store_true",
        help="Open/close paper trades while recording this scan",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep running scans until interrupted",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=120.0,
        help="Seconds between scans when --loop is enabled",
    )
    parser.add_argument(
        "--snapshot-retention-hours",
        type=float,
        default=72.0,
        help="Hours to keep market and candidate snapshots; set 0 to disable pruning",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Optional scan limit for loop mode",
    )
    args = parser.parse_args()
    if args.loop:
        asyncio.run(
            run_loop(
                database_path=args.db,
                manage_paper_trades=args.manage_paper_trades,
                snapshot_retention_hours=args.snapshot_retention_hours,
                interval_seconds=args.interval,
                max_iterations=args.max_iterations,
            )
        )
    else:
        asyncio.run(
            run_once(
                database_path=args.db,
                manage_paper_trades=args.manage_paper_trades,
                snapshot_retention_hours=args.snapshot_retention_hours,
            )
        )


if __name__ == "__main__":
    main()
