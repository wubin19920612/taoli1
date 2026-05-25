from datetime import UTC, datetime, timedelta

import pytest

from app.models.alert import AlertRule
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import RiskSettings
from app.services.alert_engine import AlertEngine


def opportunity(spread: float = 0.8) -> Opportunity:
    return Opportunity(
        id="opp-1",
        type=OpportunityType.FF,
        symbol="BTCUSDT",
        buy_exchange="binance",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=spread,
        close_spread_pct=spread + 0.1,
        fee_adjusted_open_pct=spread - 0.2,
        spread_width_pct=0.1,
        buy_bid=99,
        buy_ask=100,
        sell_bid=101,
        sell_ask=102,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=20_000_000,
        funding_rate_buy_pct=0,
        funding_rate_sell_pct=0.02,
        net_funding_pct=0.02,
        mark_index_diff_buy_pct=0.01,
        mark_index_diff_sell_pct=0.01,
        risk_labels=[],
        last_seen_at=datetime.now(UTC),
    )


def test_requires_consecutive_hits_before_firing() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="ff spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.3,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=3,
        cooldown_seconds=300,
    )

    now = datetime.now(UTC)
    assert engine.evaluate([opportunity()], [rule], now=now) == []
    assert engine.evaluate([opportunity()], [rule], now=now + timedelta(seconds=8)) == []
    fired = engine.evaluate([opportunity()], [rule], now=now + timedelta(seconds=16))

    assert len(fired) == 1
    assert fired[0].rule.id == rule.id
    assert fired[0].opportunity.id == "opp-1"


def test_match_includes_recent_observations_for_consecutive_hits() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="ff spread",
        types=["FF"],
        min_open_spread_pct=0.3,
        min_fee_adjusted_open_pct=0.2,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=3,
        cooldown_seconds=300,
    )
    now = datetime.now(UTC)

    assert engine.evaluate(
        [
            opportunity(spread=0.55).model_copy(
                update={"fee_adjusted_open_pct": 0.35, "net_funding_next_pct": 0.01}
            )
        ],
        [rule],
        now=now,
    ) == []
    assert engine.evaluate(
        [
            opportunity(spread=0.65).model_copy(
                update={"fee_adjusted_open_pct": 0.45, "net_funding_next_pct": 0.02}
            )
        ],
        [rule],
        now=now + timedelta(seconds=8),
    ) == []
    fired = engine.evaluate(
        [
            opportunity(spread=0.75).model_copy(
                update={"fee_adjusted_open_pct": 0.55, "net_funding_next_pct": 0.03}
            )
        ],
        [rule],
        now=now + timedelta(seconds=16),
    )

    assert len(fired) == 1
    observations = fired[0].observations
    assert [item.open_spread_pct for item in observations] == [0.55, 0.65, 0.75]
    assert [item.funding_edge_pct for item in observations] == [0.01, 0.02, 0.03]
    assert [item.combined_open_edge_pct for item in observations] == pytest.approx([0.36, 0.47, 0.58])


def test_cooldown_suppresses_repeated_alerts() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="ff spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.3,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
        cooldown_seconds=300,
    )
    now = datetime.now(UTC)

    assert len(engine.evaluate([opportunity()], [rule], now=now)) == 1
    assert engine.evaluate([opportunity()], [rule], now=now + timedelta(seconds=60)) == []
    assert len(engine.evaluate([opportunity()], [rule], now=now + timedelta(seconds=301))) == 1


def test_decaying_signal_is_suppressed_after_consecutive_hits() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="avoid fading edge",
        types=["FF"],
        min_open_spread_pct=0.3,
        min_fee_adjusted_open_pct=0.2,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=3,
        cooldown_seconds=300,
    )
    settings = RiskSettings(max_open_spread_decay_pct=40, ticker_collision_symbols=[])
    now = datetime.now(UTC)

    assert engine.evaluate(
        [opportunity(spread=1.20).model_copy(update={"fee_adjusted_open_pct": 1.00})],
        [rule],
        now=now,
        risk_settings=settings,
    ) == []
    assert engine.evaluate(
        [opportunity(spread=0.90).model_copy(update={"fee_adjusted_open_pct": 0.70})],
        [rule],
        now=now + timedelta(seconds=8),
        risk_settings=settings,
    ) == []
    fired = engine.evaluate(
        [opportunity(spread=0.55).model_copy(update={"fee_adjusted_open_pct": 0.35})],
        [rule],
        now=now + timedelta(seconds=16),
        risk_settings=settings,
    )

    assert fired == []


def test_stable_signal_survives_decay_guard() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="stable edge",
        types=["FF"],
        min_open_spread_pct=0.3,
        min_fee_adjusted_open_pct=0.2,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=3,
        cooldown_seconds=300,
    )
    settings = RiskSettings(max_open_spread_decay_pct=40, ticker_collision_symbols=[])
    now = datetime.now(UTC)

    assert engine.evaluate(
        [opportunity(spread=1.00).model_copy(update={"fee_adjusted_open_pct": 0.80})],
        [rule],
        now=now,
        risk_settings=settings,
    ) == []
    assert engine.evaluate(
        [opportunity(spread=0.92).model_copy(update={"fee_adjusted_open_pct": 0.72})],
        [rule],
        now=now + timedelta(seconds=8),
        risk_settings=settings,
    ) == []
    fired = engine.evaluate(
        [opportunity(spread=0.88).model_copy(update={"fee_adjusted_open_pct": 0.68})],
        [rule],
        now=now + timedelta(seconds=16),
        risk_settings=settings,
    )

    assert len(fired) == 1


def test_excluded_risk_label_blocks_alert() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="no high risk",
        types=["FF"],
        min_open_spread_pct=0.5,
        excluded_risk_labels=["HUGE_SPREAD_VERIFY"],
        consecutive_hits=1,
    )
    opp = opportunity()
    opp = opp.model_copy(update={"risk_labels": ["HUGE_SPREAD_VERIFY"]})

    assert engine.evaluate([opp], [rule], now=datetime.now(UTC)) == []


def test_rule_exclude_symbols_do_not_block_alerts() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="inherit hidden blacklist",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.3,
        min_volume_24h_usdt=1_000_000,
        exclude_symbols=["BTCUSDT"],
        consecutive_hits=1,
    )

    fired = engine.evaluate([opportunity()], [rule], now=datetime.now(UTC))

    assert len(fired) == 1


def test_positive_funding_can_lift_fee_adjusted_edge_over_threshold() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="funding adjusted",
        types=["SF"],
        min_open_spread_pct=0.3,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity(spread=0.45).model_copy(
        update={
            "type": OpportunityType.SF,
            "buy_market_type": MarketType.SPOT,
            "fee_adjusted_open_pct": 0.20,
            "net_funding_pct": 0.08,
            "net_funding_next_pct": 0.10,
        }
    )

    fired = engine.evaluate([opp], [rule], now=datetime.now(UTC))

    assert len(fired) == 1


def test_funding_adjustment_uses_single_cycle_edge_without_daily_normalization() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="funding interval adjusted",
        types=["FF"],
        min_open_spread_pct=0.3,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity(spread=0.45).model_copy(
        update={
            "fee_adjusted_open_pct": 0.20,
            "funding_rate_buy_pct": 0.02,
            "funding_rate_sell_pct": 0.12,
            "funding_next_rate_buy_pct": None,
            "funding_next_rate_sell_pct": None,
            "net_funding_pct": 0.10,
            "net_funding_next_pct": None,
            "buy_funding_interval_hours": 8,
            "sell_funding_interval_hours": 1,
            "net_funding_hourly_pct": 0.01,
            "net_funding_daily_pct": 0.24,
            "net_funding_next_hourly_pct": None,
            "net_funding_next_daily_pct": None,
            "mark_index_diff_buy_pct": None,
            "mark_index_diff_sell_pct": None,
        }
    )

    fired = engine.evaluate([opp], [rule], now=datetime.now(UTC))

    assert len(fired) == 1
    assert fired[0].observations[0].funding_edge_pct == pytest.approx(0.10)


def test_negative_funding_can_block_fee_adjusted_edge_under_threshold() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="funding adjusted",
        types=["SF"],
        min_open_spread_pct=0.3,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity(spread=0.65).model_copy(
        update={
            "type": OpportunityType.SF,
            "buy_market_type": MarketType.SPOT,
            "fee_adjusted_open_pct": 0.40,
            "net_funding_pct": -0.05,
            "net_funding_next_pct": -0.30,
        }
    )

    assert engine.evaluate([opp], [rule], now=datetime.now(UTC)) == []


def test_large_price_edge_can_cover_negative_funding() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="funding adjusted",
        types=["SF"],
        min_open_spread_pct=0.3,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity(spread=0.95).model_copy(
        update={
            "type": OpportunityType.SF,
            "buy_market_type": MarketType.SPOT,
            "fee_adjusted_open_pct": 0.70,
            "net_funding_pct": -0.20,
            "net_funding_next_pct": -0.30,
        }
    )

    fired = engine.evaluate([opp], [rule], now=datetime.now(UTC))

    assert len(fired) == 1


def test_all_missing_volume_does_not_block_alert_when_rule_requires_volume() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="volume aware",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.3,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity().model_copy(
        update={
            "buy_volume_24h_usdt": None,
            "sell_volume_24h_usdt": None,
        }
    )

    assert len(engine.evaluate([opp], [rule], now=datetime.now(UTC))) == 1


def test_known_low_volume_blocks_alert_when_other_side_is_missing() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="volume aware",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.3,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    opp = opportunity().model_copy(
        update={
            "buy_volume_24h_usdt": 0.0,
            "sell_volume_24h_usdt": None,
        }
    )

    assert engine.evaluate([opp], [rule], now=datetime.now(UTC)) == []


def test_same_symbol_alerts_are_limited_to_top_three_arbitrage_candidates() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="best btc routes",
        types=["FF"],
        min_open_spread_pct=0.3,
        min_fee_adjusted_open_pct=0.2,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
        cooldown_seconds=300,
    )
    base_time = datetime.now(UTC)
    candidates = [
        opportunity(spread=0.90).model_copy(
            update={
                "id": "low-volume",
                "fee_adjusted_open_pct": 0.70,
                "net_funding_next_daily_pct": 0.02,
                "buy_volume_24h_usdt": 1_000_000,
                "sell_volume_24h_usdt": 1_000_000,
            }
        ),
        opportunity(spread=1.10).model_copy(
            update={
                "id": "best-edge",
                "fee_adjusted_open_pct": 0.90,
                "net_funding_next_daily_pct": 0.08,
                "buy_volume_24h_usdt": 20_000_000,
                "sell_volume_24h_usdt": 22_000_000,
            }
        ),
        opportunity(spread=1.00).model_copy(
            update={
                "id": "best-funding",
                "fee_adjusted_open_pct": 0.78,
                "net_funding_next_daily_pct": 0.15,
                "buy_volume_24h_usdt": 18_000_000,
                "sell_volume_24h_usdt": 18_000_000,
            }
        ),
        opportunity(spread=0.85).model_copy(
            update={
                "id": "medium",
                "fee_adjusted_open_pct": 0.62,
                "net_funding_next_daily_pct": 0.03,
                "buy_volume_24h_usdt": 8_000_000,
                "sell_volume_24h_usdt": 8_000_000,
            }
        ),
        opportunity(spread=1.05).model_copy(
            update={
                "id": "best-liquidity",
                "fee_adjusted_open_pct": 0.76,
                "net_funding_next_daily_pct": 0.05,
                "buy_volume_24h_usdt": 100_000_000,
                "sell_volume_24h_usdt": 90_000_000,
            }
        ),
    ]

    fired = engine.evaluate(candidates, [rule], now=base_time)

    assert [match.opportunity.id for match in fired] == [
        "best-edge",
        "best-funding",
        "best-liquidity",
    ]
