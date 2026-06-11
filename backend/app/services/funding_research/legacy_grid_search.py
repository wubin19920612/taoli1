import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from app.db.database import connect_database
from app.db.repositories import OpportunityHistoryRepository
from app.services.funding_research.legacy_backtest import grid_search_legacy_opportunity_history


def _float_values(raw: str) -> list[float]:
    return [float(item.strip()) for item in raw.split(",") if item.strip()]


def _int_values(raw: str) -> list[int]:
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


async def run_grid_search(
    *,
    database_path: str,
    hours: float,
    limit: int,
    symbol: str | None,
    min_entry_edges: list[float],
    min_next_fundings: list[float],
    costs: list[float],
    max_holds: list[int],
    min_trades: int,
    top_n: int,
) -> None:
    db = await connect_database(database_path)
    try:
        repo = OpportunityHistoryRepository(db)
        normalized_symbol = symbol.upper().replace("-", "").replace("_", "") if symbol else None
        rows = await repo.list(
            symbol=normalized_symbol,
            since=datetime.now(UTC) - timedelta(hours=hours),
            limit=limit,
        )
        results = grid_search_legacy_opportunity_history(
            rows,
            min_entry_edge_values=min_entry_edges,
            min_next_funding_values=min_next_fundings,
            cost_values=costs,
            max_hold_observation_values=max_holds,
            min_trades=min_trades,
            top_n=top_n,
        )
        print(f"rows loaded: {len(rows)}")
        if not results:
            print("no parameter sets met the minimum trade count")
            return
        print(
            "rank min_entry min_next_funding cost max_hold trades win_rate avg_pnl "
            "max_win max_loss avg_entry_edge"
        )
        for index, item in enumerate(results, start=1):
            summary = item.summary
            settings = item.settings
            win_rate = "n/a" if summary.win_rate_pct is None else f"{summary.win_rate_pct:.2f}%"
            avg_pnl = "n/a" if summary.average_pnl_pct is None else f"{summary.average_pnl_pct:.4f}%"
            max_win = "n/a" if summary.max_win_pct is None else f"{summary.max_win_pct:.4f}%"
            max_loss = "n/a" if summary.max_loss_pct is None else f"{summary.max_loss_pct:.4f}%"
            avg_edge = (
                "n/a"
                if summary.average_entry_edge_pct is None
                else f"{summary.average_entry_edge_pct:.4f}%"
            )
            print(
                f"{index} {settings.min_entry_edge_pct:.2f} "
                f"{settings.min_next_funding_pct:.2f} {settings.cost_pct:.2f} "
                f"{settings.max_hold_observations} {summary.trades} {win_rate} "
                f"{avg_pnl} {max_win} {max_loss} {avg_edge}"
            )
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid-search legacy opportunity_history backtests.")
    parser.add_argument("--db", default="data/radar.db", help="SQLite database path")
    parser.add_argument("--hours", type=float, default=24 * 7, help="Lookback window in hours")
    parser.add_argument("--limit", type=int, default=100_000, help="Maximum rows to load")
    parser.add_argument("--symbol", default=None, help="Optional normalized symbol, e.g. BTCUSDT")
    parser.add_argument("--min-entry-edges", default="0.4,0.6,0.8,1.0,1.2")
    parser.add_argument("--min-next-fundings", default="0.2,0.4,0.6,0.8")
    parser.add_argument("--costs", default="0.3,0.35,0.4")
    parser.add_argument("--max-holds", default="2,3,4,5")
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--top-n", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(
        run_grid_search(
            database_path=args.db,
            hours=args.hours,
            limit=args.limit,
            symbol=args.symbol,
            min_entry_edges=_float_values(args.min_entry_edges),
            min_next_fundings=_float_values(args.min_next_fundings),
            costs=_float_values(args.costs),
            max_holds=_int_values(args.max_holds),
            min_trades=args.min_trades,
            top_n=args.top_n,
        )
    )


if __name__ == "__main__":
    main()
