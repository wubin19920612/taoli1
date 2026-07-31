from datetime import UTC, datetime, timedelta

from app.models.alert import AlertEvent, AlertRule
from app.models.market import MarketType
from app.models.pair_spread import (
    PairSpreadLegQuery,
    PairSpreadPoint,
    PairSpreadQueryResult,
    PairSpreadValueStats,
)
from app.services.pair_spread_diagnostics import build_pair_spread_diagnostic


def make_result() -> PairSpreadQueryResult:
    start = datetime(2026, 7, 31, 0, 0, tzinfo=UTC)
    spreads = [11.2, 11.23, 5.0, 3.0, 2.0, 1.5, 0.8]
    points = [
        PairSpreadPoint(
            bucket_at=start + timedelta(minutes=index),
            leg1_close=250 + index,
            leg2_close=280 + index,
            spread_abs=30,
            spread_pct=spread,
        )
        for index, spread in enumerate(spreads)
    ]
    return PairSpreadQueryResult(
        leg1=PairSpreadLegQuery(exchange="bitget", symbol="KIOXIAUSDT", market_type=MarketType.FUTURE),
        leg2=PairSpreadLegQuery(exchange="hyperliquid", symbol="KIOXIAUSDT", market_type=MarketType.FUTURE),
        hours=24,
        interval_minutes=1,
        interval_seconds=60,
        observed_at=start + timedelta(minutes=7),
        point_count=len(points),
        first_seen_at=points[0].bucket_at,
        last_seen_at=points[-1].bucket_at,
        spread_abs=PairSpreadValueStats(min=30, max=30, mean=30, current=0),
        spread_pct=PairSpreadValueStats(min=0.8, max=11.23, mean=5, current=0.8),
        points=points,
    )


def test_diagnostic_finds_over_ten_percent_peak_and_continuous_run() -> None:
    diagnostic = build_pair_spread_diagnostic(
        make_result(),
        threshold_pct=1,
        requested_interval_seconds=5,
        interval_seconds=60,
        rules=[
            AlertRule(
                id="rule-1",
                name="规则1",
                types=["FF"],
                include_exchanges=["bitget", "hyperliquid"],
                min_open_spread_pct=1.5,
                consecutive_hits=3,
            )
        ],
        events=[
            AlertEvent(
                rule_id="rule-1",
                opportunity_id="kioxia",
                symbol="KIOXIAUSDT",
                status="muted",
                message="订单簿深度不足",
                created_at=datetime(2026, 7, 31, 0, 2, tzinfo=UTC),
            )
        ],
        suppress_when_card_conditions_fail=True,
    )

    assert diagnostic.peak_spread_pct == 11.23
    assert diagnostic.peak_at == datetime(2026, 7, 31, 0, 1, tzinfo=UTC)
    assert diagnostic.points_over_threshold == 6
    assert diagnostic.longest_run.point_count == 6
    assert diagnostic.longest_run.start_at == datetime(2026, 7, 31, 0, 0, tzinfo=UTC)
    assert diagnostic.longest_run.end_at == datetime(2026, 7, 31, 0, 5, tzinfo=UTC)
    assert diagnostic.inferred_type == "FF"
    assert diagnostic.alert_events.muted == 1
    assert diagnostic.alert_events.sent == 0
    assert diagnostic.alert_rules[0].matches_pair_scope is True
    assert "历史峰值达到规则的开仓价差阈值" in diagnostic.alert_rules[0].reasons
    assert any("全部是 muted" in note for note in diagnostic.notes)
