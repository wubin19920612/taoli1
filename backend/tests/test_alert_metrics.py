from datetime import UTC, datetime
from typing import Any

import pytest

from app.models.market import MarketType
from app.models.history import OpportunityHistoryRow
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import RiskSettings
from app.services.alert_metrics import combined_open_edge_pct, funding_edge_pct
from app.services.risk_labels import apply_risk_labels


def opportunity(**overrides: Any) -> Opportunity:
    base: dict[str, Any] = {
        "id": "opp-1",
        "type": OpportunityType.FF,
        "symbol": "QCOMUSDT",
        "buy_exchange": "gate",
        "buy_market_type": MarketType.FUTURE,
        "sell_exchange": "okx",
        "sell_market_type": MarketType.FUTURE,
        "open_spread_pct": 0.8,
        "close_spread_pct": 0.4,
        "fee_adjusted_open_pct": 0.4,
        "spread_width_pct": 0.4,
        "buy_bid": 99,
        "buy_ask": 100,
        "sell_bid": 101,
        "sell_ask": 102,
        "buy_volume_24h_usdt": 10_000_000,
        "sell_volume_24h_usdt": 12_000_000,
        "funding_rate_buy_pct": 0.01,
        "funding_rate_sell_pct": 0.02,
        "funding_next_rate_buy_pct": 0.03,
        "funding_next_rate_sell_pct": 0.08,
        "net_funding_pct": 0.01,
        "net_funding_next_pct": 0.05,
        "buy_funding_interval_hours": 8,
        "sell_funding_interval_hours": 8,
        "net_funding_hourly_pct": 0.00125,
        "net_funding_daily_pct": 0.03,
        "net_funding_next_hourly_pct": 0.00625,
        "net_funding_next_daily_pct": 0.15,
        "mark_index_diff_buy_pct": 0.01,
        "mark_index_diff_sell_pct": 0.02,
        "risk_labels": [],
        "last_seen_at": datetime(2026, 5, 24, 8, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return Opportunity.model_validate(base)


def test_funding_edge_uses_next_cycle_rate_instead_of_daily_normalization() -> None:
    item = opportunity(
        fee_adjusted_open_pct=0.4,
        net_funding_next_pct=-0.10,
        net_funding_next_daily_pct=-2.40,
        net_funding_daily_pct=1.20,
    )

    assert funding_edge_pct(item) == pytest.approx(-0.10)
    assert combined_open_edge_pct(item) == pytest.approx(0.30)


def test_funding_edge_does_not_treat_mark_index_premium_as_next_funding_rate() -> None:
    item = opportunity(
        funding_next_rate_buy_pct=None,
        funding_next_rate_sell_pct=0.12,
        net_funding_next_pct=None,
        net_funding_next_hourly_pct=None,
        net_funding_next_daily_pct=None,
        mark_index_diff_buy_pct=0.05,
        mark_index_diff_sell_pct=9.99,
    )

    assert funding_edge_pct(item) == pytest.approx(0.11)


def test_funding_edge_prefers_official_next_rate_over_mark_index_estimate() -> None:
    item = opportunity(
        funding_next_rate_buy_pct=0.02,
        funding_next_rate_sell_pct=0.04,
        net_funding_next_pct=None,
        net_funding_next_hourly_pct=None,
        net_funding_next_daily_pct=None,
        mark_index_diff_buy_pct=5.00,
        mark_index_diff_sell_pct=-5.00,
    )

    assert funding_edge_pct(item) == pytest.approx(0.02)


def test_small_edge_risk_uses_single_cycle_funding_edge() -> None:
    item = opportunity(
        fee_adjusted_open_pct=0.40,
        net_funding_next_pct=-0.10,
        net_funding_next_daily_pct=-2.40,
    )
    settings = RiskSettings(
        signal_slippage_buffer_pct=0,
        min_effective_open_pct=0.05,
        ticker_collision_symbols=[],
    )

    labeled = apply_risk_labels(item, settings=settings, now=item.last_seen_at)

    assert "EDGE_AFTER_SLIPPAGE_TOO_SMALL" not in labeled.risk_labels


def test_funding_edge_handles_history_rows_without_mark_index_estimates() -> None:
    row = OpportunityHistoryRow(
        observed_at=datetime(2026, 5, 24, 8, 0, tzinfo=UTC),
        opportunity_id="opp-history",
        type=OpportunityType.FF,
        symbol="QCOMUSDT",
        buy_exchange="gate",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=0.8,
        close_spread_pct=0.4,
        fee_adjusted_open_pct=0.4,
        spread_width_pct=0.4,
        funding_rate_buy_pct=0.01,
        funding_rate_sell_pct=0.03,
        funding_next_rate_buy_pct=None,
        funding_next_rate_sell_pct=None,
        net_funding_pct=0.02,
        net_funding_next_pct=None,
        buy_funding_interval_hours=8,
        sell_funding_interval_hours=8,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=12_000_000,
        risk_labels=[],
    )

    assert funding_edge_pct(row) == pytest.approx(0.02)
