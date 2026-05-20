from datetime import UTC, datetime, timedelta

from app.models.alert import AlertRule
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
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
