from datetime import UTC, datetime, timedelta

from app.models.history import OpportunityHistoryRow
from app.models.market import MarketType
from app.models.opportunity import OpportunityType
from app.services.funding_research.legacy_backtest import (
    LegacyBacktestSettings,
    backtest_legacy_opportunity_history,
    grid_search_legacy_opportunity_history,
)


def row(index: int, open_spread: float, next_funding: float) -> OpportunityHistoryRow:
    return OpportunityHistoryRow(
        observed_at=datetime(2026, 6, 1, 8, 0, tzinfo=UTC) + timedelta(minutes=index),
        opportunity_id="opp-1",
        type=OpportunityType.FF,
        symbol="LABUSDT",
        buy_exchange="binance",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=open_spread,
        close_spread_pct=open_spread - 0.1,
        fee_adjusted_open_pct=0.3,
        spread_width_pct=0.1,
        net_funding_next_pct=next_funding,
        risk_labels=[],
    )


def test_legacy_backtest_uses_spread_change_and_next_funding() -> None:
    summary = backtest_legacy_opportunity_history(
        [
            row(0, 1.0, 0.4),
            row(1, 0.7, 0.2),
            row(2, 0.5, 0.1),
        ],
        settings=LegacyBacktestSettings(
            min_entry_edge_pct=0.5,
            min_next_funding_pct=0.2,
            cost_pct=0.1,
            max_hold_observations=2,
        ),
    )

    assert summary.rows_seen == 3
    assert summary.trades == 1
    assert summary.winners == 1
    assert summary.average_pnl_pct == 0.8


def test_legacy_grid_search_ranks_profitable_settings() -> None:
    results = grid_search_legacy_opportunity_history(
        [
            row(0, 1.0, 0.4),
            row(1, 0.7, 0.2),
            row(2, 0.5, 0.1),
        ],
        min_entry_edge_values=[0.5, 1.5],
        min_next_funding_values=[0.2],
        cost_values=[0.1, 0.3],
        max_hold_observation_values=[2],
        min_trades=1,
        top_n=2,
    )

    assert len(results) == 2
    assert results[0].settings.cost_pct == 0.1
    assert results[0].summary.average_pnl_pct == 0.8
