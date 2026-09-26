from datetime import UTC, datetime

import pytest

from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig
from app.services.market_labels import astro_exchange_route_variants


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


def test_negative_ff_open_requires_explicit_funding_reverse_mode() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())
    reverse_entry = opportunity().model_copy(
        update={"open_spread_pct": -0.9, "close_spread_pct": 0}
    )

    blocked = planner.plan(reverse_entry)
    allowed = planner.plan(reverse_entry, allow_negative_open=True)

    assert blocked.can_submit is False
    assert any("Open spread must be positive" in blocker for blocker in blocked.blockers)
    assert allowed.can_submit is True
    assert allowed.pair is not None
    assert allowed.pair["type"] == "FF"
    assert allowed.pair["openPosition"] == "-0.009000"
    assert allowed.pair["closePosition"] == "-0.010000"
    assert any("funding-only reverse-entry" in warning for warning in allowed.warnings)


def test_lighter_preview_uses_ordinary_route_and_offers_gc_variant() -> None:
    planner = AstroPairPlanner()
    for buy, sell, expected in (
        ("lighter", "binance", ("lighter", "binance")),
        ("bitget", "lighter", ("bitget", "lighter")),
        ("lighter", "lighter", ("lighter", "lighter")),
    ):
        plan = planner.plan(opportunity().model_copy(update={"buy_exchange": buy, "sell_exchange": sell}))
        assert plan.can_submit is True
        assert (plan.pair["buyEx"], plan.pair["sellEx"]) == expected
        assert astro_exchange_route_variants(*expected)[0] == expected
    assert astro_exchange_route_variants("lighter", "okx") == [
        ("lighter", "okx"), ("gc-lighter", "gc-okx")
    ]
    assert astro_exchange_route_variants("okx", "lighter") == [
        ("okx", "lighter"), ("gc-okx", "gc-lighter")
    ]


@pytest.mark.parametrize("lighter_exchange", ["lighter", "gc-lighter"])
def test_lighter_unknown_counterparty_is_blocked_even_for_manual_preview(
    lighter_exchange: str,
) -> None:
    plan = AstroPairPlanner().plan(
        opportunity().model_copy(update={"buy_exchange": lighter_exchange, "sell_exchange": "aster"}),
        allow_manual_override=True,
    )
    assert not plan.can_submit
    assert plan.pair is None
    assert "Lighter" in plan.blockers[0]


def test_ss_opportunity_is_blocked_because_astro_sdk_does_not_document_ss() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())

    plan = planner.plan(opportunity(OpportunityType.SS, MarketType.SPOT, MarketType.SPOT))

    assert plan.can_submit is False
    assert plan.pair is None
    assert any("SS" in blocker for blocker in plan.blockers)


def test_manual_reverse_sf_builds_fs_pair_and_turns_risks_into_warnings() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())
    reverse_sf = opportunity(
        OpportunityType.SF,
        MarketType.FUTURE,
        MarketType.SPOT,
    ).model_copy(
        update={
            "open_spread_pct": -0.8,
            "close_spread_pct": -0.35,
        }
    )

    plan = planner.plan(reverse_sf, allow_manual_override=True)

    assert plan.can_submit is True
    assert plan.blockers == []
    assert plan.pair is not None
    assert plan.pair["type"] == "FS"
    assert plan.pair["buyEx"] == "binance"
    assert plan.pair["sellEx"] == "okx"
    assert plan.pair["openPosition"] == "-0.008000"
    assert plan.pair["closePosition"] == "-0.009000"
    assert any("Astro FS" in warning for warning in plan.warnings)
    assert any("Open spread must be positive" in warning for warning in plan.warnings)


def test_sf_opportunity_maps_spot_to_future() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig(default_max_trade_usdt=25))

    plan = planner.plan(opportunity(OpportunityType.SF, MarketType.SPOT, MarketType.FUTURE))

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["type"] == "SF"
    assert plan.pair["maxTradeUSDT"] == "25"


def test_bitget_rtoken_sf_opportunity_maps_to_astro_bitgetr() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())
    rtoken_opportunity = opportunity(OpportunityType.SF, MarketType.SPOT, MarketType.FUTURE).model_copy(
        update={
            "symbol": "AAPLUSDT",
            "buy_exchange": "bitget",
            "buy_raw_symbol": "RAAPLUSDT",
        }
    )

    plan = planner.plan(
        rtoken_opportunity,
        now=datetime(2026, 5, 15, 15, 0, tzinfo=UTC),
    )

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["buyEx"] == "bitgetr"
    assert plan.pair["sellEx"] == "okx"
    assert not plan.blockers
    assert any(
        item.field == "buyEx/sellEx"
        and item.assumed_value == "bitgetr->okx"
        and "bitgetr" in item.note
        for item in plan.assumptions
    )


def test_hyperliquid_opportunity_maps_to_astro_hl() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())

    plan = planner.plan(
        opportunity().model_copy(
            update={"buy_exchange": "hyperliquid", "buy_raw_symbol": "BTC"}
        )
    )

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["buyEx"] == "hl"
    assert plan.pair["sellEx"] == "okx"
    assert "aHlDex" not in plan.pair


def test_cifr_ff_uses_para_market_on_the_correct_hyperliquid_leg() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())
    for hl_side, astro_key in (("buy", "aHlDex"), ("sell", "bHlDex")):
        pair = opportunity().model_copy(
            update={
                "symbol": "CIFRUSDT",
                f"{hl_side}_exchange": "hyperliquid",
                f"{hl_side}_raw_symbol": "para:CIFR",
            }
        )

        plan = planner.plan(pair)

        assert plan.can_submit is True
        assert plan.pair is not None
        assert plan.pair["type"] == "FF"
        assert plan.pair[astro_key] == "para"
        assert ("aHlDex" if hl_side == "sell" else "bHlDex") not in plan.pair


def test_auto_card_does_not_guess_hyperliquid_market() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())
    missing_market = opportunity().model_copy(
        update={"sell_exchange": "hyperliquid", "sell_raw_symbol": None}
    )

    plan = planner.plan(missing_market)
    manual_plan = planner.plan(missing_market, allow_manual_override=True)

    assert plan.can_submit is False
    assert any("HL 市场未确认" in blocker for blocker in plan.blockers)
    assert manual_plan.can_submit is False
    assert any("HL 市场未确认" in blocker for blocker in manual_plan.blockers)


def test_aliased_future_pair_builds_fr_card_with_ratio_positions() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())
    aliased = opportunity().model_copy(
        update={
            "symbol": "OPENAIUSDT",
            "buy_exchange": "bitget",
            "buy_raw_symbol": "OPENAIUSDT",
            "sell_exchange": "hyperliquid",
            "sell_raw_symbol": "io:OAI",
            "open_spread_pct": 2.55,
            "close_spread_pct": 1.25,
        }
    )

    plan = planner.plan(aliased)

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["name"] == "OPENAI-OAI"
    assert plan.pair["type"] == "FR"
    assert plan.pair["buyEx"] == "bitget"
    assert plan.pair["sellEx"] == "hl"
    assert plan.pair["bHlDex"] == "io"
    assert plan.pair["regressionValue"] == "1"
    assert plan.pair["rateMultiply"] == "1"
    assert plan.pair["openPosition"] == "1.025829"
    assert plan.pair["closePosition"] == "1.000000"
    assert any(
        item.field == "openPosition"
        and item.assumed_value == "1.025829"
        and "price ratio" in item.note
        for item in plan.assumptions
    )


def test_fr_zero_close_buffer_still_preserves_strict_astro_order() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig(default_close_position_buffer_pct=0))
    aliased = opportunity().model_copy(
        update={
            "symbol": "OPENAIUSDT",
            "buy_raw_symbol": "OPENAIUSDT",
            "sell_raw_symbol": "io:OAI",
            "open_spread_pct": 0.88,
            "close_spread_pct": 0.94,
            "net_funding_next_pct": -1.6,
        }
    )

    plan = planner.plan(aliased)

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["type"] == "FR"
    assert plan.pair["openPosition"] == "1.008839"
    assert plan.pair["closePosition"] == "1.008838"
    assert any("precision unit" in warning for warning in plan.warnings)


def test_fr_sub_precision_close_buffer_still_preserves_strict_order() -> None:
    planner = AstroPairPlanner(
        AstroPlannerConfig(default_close_position_buffer_pct=0.00001)
    )
    aliased = opportunity().model_copy(
        update={
            "symbol": "OPENAIUSDT",
            "buy_raw_symbol": "OPENAIUSDT",
            "sell_raw_symbol": "io:OAI",
            "open_spread_pct": 0.88,
            "close_spread_pct": 0.94,
            "net_funding_next_pct": -1.6,
        }
    )

    plan = planner.plan(aliased)

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["type"] == "FR"
    assert plan.pair["openPosition"] == "1.008839"
    assert plan.pair["closePosition"] == "1.008838"


def test_standard_exchange_raw_symbols_stay_an_ff_card() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())
    standard_pair = opportunity().model_copy(
        update={
            "buy_raw_symbol": "BTC-USDT-SWAP",
            "sell_raw_symbol": "BTCUSDT",
        }
    )

    plan = planner.plan(standard_pair)

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["name"] == "BTC"
    assert plan.pair["type"] == "FF"


def test_closed_bitget_rtoken_opportunity_is_blocked() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig())
    rtoken_opportunity = opportunity(OpportunityType.SF, MarketType.SPOT, MarketType.FUTURE).model_copy(
        update={
            "symbol": "AAPLUSDT",
            "buy_exchange": "bitget",
            "buy_raw_symbol": "RAAPLUSDT",
        }
    )

    plan = planner.plan(
        rtoken_opportunity,
        now=datetime(2026, 9, 9, 21, 0, tzinfo=UTC),
    )

    assert plan.can_submit is False
    assert plan.pair is None
    assert "Bitget 股票现货当前处于休市时间" in plan.blockers[0]


def test_open_enabled_config_builds_open_enabled_pair() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig(default_open_enabled=True))

    plan = planner.plan(opportunity())

    assert plan.can_submit is True
    assert plan.pair is not None
    assert plan.pair["status"] is True
    assert plan.pair["disableOpen"] is False


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


def test_zero_close_position_buffer_still_preserves_strict_astro_order() -> None:
    planner = AstroPairPlanner(AstroPlannerConfig(default_close_position_buffer_pct=0))

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
    assert plan.pair["closePosition"] == "0.008799"
    assert any("precision unit" in warning for warning in plan.warnings)


def test_sub_precision_close_position_buffer_still_preserves_strict_order() -> None:
    planner = AstroPairPlanner(
        AstroPlannerConfig(default_close_position_buffer_pct=0.00001)
    )

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
    assert plan.pair["closePosition"] == "0.008799"


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
