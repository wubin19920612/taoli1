from datetime import UTC, datetime

from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig


def opportunity(
    opportunity_type: OpportunityType = OpportunityType.FF,
    buy_market_type: MarketType = MarketType.FUTURE,
    sell_market_type: MarketType = MarketType.FUTURE,
) -> Opportunity:
    return Opportunity(
        id="opp-1",
        type=opportunity_type,
        symbol="BTCUSDT",
        buy_exchange="binance",
        buy_market_type=buy_market_type,
        sell_exchange="okx",
        sell_market_type=sell_market_type,
        open_spread_pct=0.8,
        close_spread_pct=0.35,
        fee_adjusted_open_pct=0.55,
        spread_width_pct=0.45,
        buy_bid=99,
        buy_ask=100,
        sell_bid=100.8,
        sell_ask=101,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=12_000_000,
        funding_rate_buy_pct=0.01,
        funding_rate_sell_pct=0.02,
        funding_next_rate_buy_pct=0.01,
        funding_next_rate_sell_pct=0.03,
        funding_next_time_buy=datetime(2026, 5, 20, 8, tzinfo=UTC),
        funding_next_time_sell=datetime(2026, 5, 20, 8, tzinfo=UTC),
        net_funding_pct=0.01,
        net_funding_next_pct=0.02,
        buy_funding_interval_hours=8,
        sell_funding_interval_hours=8,
        net_funding_hourly_pct=0.00125,
        net_funding_daily_pct=0.03,
        net_funding_next_hourly_pct=0.0025,
        net_funding_next_daily_pct=0.06,
        mark_index_diff_buy_pct=0.01,
        mark_index_diff_sell_pct=0.01,
        risk_labels=[],
        last_seen_at=datetime(2026, 5, 20, 1, tzinfo=UTC),
    )


def test_ff_opportunity_builds_safe_dry_run_pair() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())

    plan = planner.plan(opportunity())

    assert plan.mode == "dry_run"
    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["name"] == "BTC"
    assert plan.pair["type"] == "FF"
    assert plan.pair["buyEx"] == "binance"
    assert plan.pair["sellEx"] == "okx"
    assert plan.pair["status"] is False
    assert plan.pair["disableOpen"] is True
    assert plan.pair["disableClose"] is False
    assert plan.pair["maxTradeUSDT"] == "10"
    assert plan.pair["leverage"] == "1"
    assert plan.pair["openPosition"] == "0.008000"
    assert plan.pair["closePosition"] == "0.000000"
    assert plan.sdk_payload == {"action": "add", "pair": plan.pair}
    assert any(item.field == "openPosition" and item.assumed_value == "0.008000" for item in plan.assumptions)
    assert any(item.field == "closePosition" and "predicted funding is favorable" in item.note for item in plan.assumptions)
    assert any(item.field == "name" and item.needs_verification for item in plan.assumptions)


def test_ss_opportunity_is_blocked_because_astro_sdk_does_not_document_ss() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())

    plan = planner.plan(opportunity(OpportunityType.SS, MarketType.SPOT, MarketType.SPOT))

    assert plan.can_submit is False
    assert plan.pair is None
    assert any("SS" in blocker for blocker in plan.blockers)


def test_sf_opportunity_maps_spot_to_future() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig(default_max_trade_usdt=25))

    plan = planner.plan(opportunity(OpportunityType.SF, MarketType.SPOT, MarketType.FUTURE))

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["type"] == "SF"
    assert plan.pair["maxTradeUSDT"] == "25"


def test_close_position_is_adjusted_below_open_position_for_astro_submission() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())

    plan = planner.plan(
        opportunity().model_copy(
            update={
                "open_spread_pct": 0.88,
                "close_spread_pct": 0.94,
                "net_funding_next_pct": -1.6,
            }
        )
    )

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["openPosition"] == "0.008800"
    assert plan.pair["closePosition"] == "0.007800"
    assert any(item.field == "closePosition" and item.assumed_value == "0.007800" for item in plan.assumptions)
    assert any("closePosition was adjusted" in warning for warning in plan.warnings)


def test_close_position_adjustment_uses_configured_buffer() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig(default_close_position_buffer_pct=0.2))

    plan = planner.plan(
        opportunity().model_copy(
            update={
                "open_spread_pct": 0.88,
                "close_spread_pct": 0.94,
                "net_funding_next_pct": -1.6,
            }
        )
    )

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["closePosition"] == "0.006800"


def test_unfavorable_predicted_funding_raises_close_position() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())

    plan = planner.plan(
        opportunity().model_copy(
            update={
                "net_funding_next_pct": -0.02,
                "buy_funding_interval_hours": 8,
                "sell_funding_interval_hours": 8,
            }
        )
    )

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["closePosition"] == "0.000200"
    assert any(
        item.field == "closePosition" and "unfavorable predicted funding" in item.note
        for item in plan.assumptions
    )


def test_same_next_cycle_rates_are_neutral_even_when_intervals_differ() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())

    plan = planner.plan(
        opportunity().model_copy(
            update={
                "funding_next_rate_buy_pct": 0.04,
                "funding_next_rate_sell_pct": 0.04,
                "net_funding_next_pct": None,
                "net_funding_next_hourly_pct": None,
                "net_funding_next_daily_pct": None,
                "buy_funding_interval_hours": 4,
                "sell_funding_interval_hours": 8,
            }
        )
    )

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["closePosition"] == "0.000000"
    assert any(
        item.field == "closePosition"
        and "predicted funding is favorable or neutral" in item.note
        for item in plan.assumptions
    )


def test_current_funding_is_used_when_predicted_funding_is_unavailable() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())

    plan = planner.plan(
        opportunity().model_copy(
            update={
                "funding_rate_buy_pct": 0.08,
                "funding_rate_sell_pct": 0.04,
                "funding_next_rate_buy_pct": None,
                "funding_next_rate_sell_pct": None,
                "net_funding_pct": None,
                "mark_index_diff_buy_pct": None,
                "mark_index_diff_sell_pct": None,
                "net_funding_hourly_pct": None,
                "net_funding_daily_pct": None,
                "net_funding_next_pct": None,
                "net_funding_next_hourly_pct": None,
                "net_funding_next_daily_pct": None,
                "buy_funding_interval_hours": 8,
                "sell_funding_interval_hours": 8,
            }
        )
    )

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["closePosition"] == "0.000400"
    assert any(
        item.field == "closePosition" and "current funding" in item.note
        for item in plan.assumptions
    )
