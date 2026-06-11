from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from app.models.history import OpportunityHistoryRow
from app.services.funding_research.models import FundingResearchLegacyBacktestSummary


@dataclass(frozen=True)
class LegacyBacktestSettings:
    min_entry_edge_pct: float = 0.4
    min_next_funding_pct: float = 0.2
    cost_pct: float = 0.3
    max_hold_observations: int = 3


@dataclass(frozen=True)
class LegacyGridSearchResult:
    settings: LegacyBacktestSettings
    summary: FundingResearchLegacyBacktestSummary

    @property
    def sort_score(self) -> tuple[float, float, int]:
        average = self.summary.average_pnl_pct
        win_rate = self.summary.win_rate_pct
        return (
            average if average is not None else -999.0,
            win_rate if win_rate is not None else -999.0,
            self.summary.trades,
        )


def _entry_edge(row: OpportunityHistoryRow) -> float:
    return (row.net_funding_next_pct or row.net_funding_pct or 0.0) + row.fee_adjusted_open_pct


def _exit_basis_pnl(entry: OpportunityHistoryRow, exit_row: OpportunityHistoryRow) -> float:
    return entry.open_spread_pct - exit_row.open_spread_pct


def _trade_pnl(
    entry: OpportunityHistoryRow,
    exit_row: OpportunityHistoryRow,
    settings: LegacyBacktestSettings,
) -> float:
    funding = entry.net_funding_next_pct or entry.net_funding_pct or 0.0
    return funding + _exit_basis_pnl(entry, exit_row) - settings.cost_pct


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def backtest_legacy_opportunity_history(
    rows: list[OpportunityHistoryRow],
    *,
    settings: LegacyBacktestSettings | None = None,
) -> FundingResearchLegacyBacktestSummary:
    resolved = settings or LegacyBacktestSettings()
    chronological = sorted(rows, key=lambda item: item.observed_at)
    by_opportunity: dict[str, list[OpportunityHistoryRow]] = defaultdict(list)
    for row in chronological:
        by_opportunity[row.opportunity_id].append(row)

    pnl_values: list[float] = []
    entry_edges: list[float] = []
    for series in by_opportunity.values():
        index = 0
        while index < len(series) - 1:
            row = series[index]
            funding = row.net_funding_next_pct or row.net_funding_pct or 0.0
            edge = _entry_edge(row)
            if edge < resolved.min_entry_edge_pct or funding < resolved.min_next_funding_pct:
                index += 1
                continue
            exit_index = min(index + resolved.max_hold_observations, len(series) - 1)
            exit_row = series[exit_index]
            pnl_values.append(_trade_pnl(row, exit_row, resolved))
            entry_edges.append(edge)
            index = exit_index + 1

    winners = sum(value > 0 for value in pnl_values)
    losers = sum(value < 0 for value in pnl_values)
    return FundingResearchLegacyBacktestSummary(
        rows_seen=len(rows),
        trades=len(pnl_values),
        winners=winners,
        losers=losers,
        win_rate_pct=winners / len(pnl_values) * 100 if pnl_values else None,
        total_pnl_pct=sum(pnl_values),
        average_pnl_pct=_average(pnl_values),
        average_entry_edge_pct=_average(entry_edges),
        max_win_pct=max(pnl_values) if pnl_values else None,
        max_loss_pct=min(pnl_values) if pnl_values else None,
        notes=[
            "legacy opportunity_history lacks raw mark/index and orderbook snapshots",
            "basis PnL is approximated from open_spread_pct changes",
            "funding PnL uses net_funding_next_pct when available, otherwise net_funding_pct",
        ],
    )


def grid_search_legacy_opportunity_history(
    rows: list[OpportunityHistoryRow],
    *,
    min_entry_edge_values: list[float],
    min_next_funding_values: list[float],
    cost_values: list[float],
    max_hold_observation_values: list[int],
    min_trades: int = 10,
    top_n: int = 20,
) -> list[LegacyGridSearchResult]:
    results: list[LegacyGridSearchResult] = []
    for min_entry_edge in min_entry_edge_values:
        for min_next_funding in min_next_funding_values:
            for cost in cost_values:
                for max_hold in max_hold_observation_values:
                    settings = LegacyBacktestSettings(
                        min_entry_edge_pct=min_entry_edge,
                        min_next_funding_pct=min_next_funding,
                        cost_pct=cost,
                        max_hold_observations=max_hold,
                    )
                    summary = backtest_legacy_opportunity_history(rows, settings=settings)
                    if summary.trades < min_trades:
                        continue
                    results.append(LegacyGridSearchResult(settings=settings, summary=summary))
    return sorted(results, key=lambda item: item.sort_score, reverse=True)[:top_n]
