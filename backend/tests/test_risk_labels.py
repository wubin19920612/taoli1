from datetime import UTC, datetime, timedelta

from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import RiskSettings
from app.services.risk_labels import apply_risk_labels, has_non_actionable_risk


def opportunity(**overrides) -> Opportunity:
    base = dict(
        id="abc",
        type=OpportunityType.FF,
        symbol="AIUSDT",
        buy_exchange="gate",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=12.0,
        close_spread_pct=16.5,
        fee_adjusted_open_pct=11.75,
        spread_width_pct=4.5,
        buy_bid=1.0,
        buy_ask=1.01,
        sell_bid=1.15,
        sell_ask=1.17,
        buy_volume_24h_usdt=50_000,
        sell_volume_24h_usdt=20_000_000,
        funding_rate_buy_pct=0.05,
        funding_rate_sell_pct=-0.02,
        net_funding_pct=-0.07,
        mark_index_diff_buy_pct=0.1,
        mark_index_diff_sell_pct=1.1,
        risk_labels=[],
        last_seen_at=datetime.now(UTC) - timedelta(seconds=90),
    )
    base.update(overrides)
    return Opportunity(**base)


def test_applies_expected_risk_labels() -> None:
    settings = RiskSettings(
        min_volume_24h_usdt=100_000,
        stale_after_seconds=30,
        huge_spread_pct=10,
        wide_spread_pct=3,
        mark_index_deviation_pct=1,
        ticker_collision_symbols=["AIUSDT"],
    )

    labeled = apply_risk_labels(opportunity(), settings=settings, now=datetime.now(UTC))

    assert "LOW_VOLUME" in labeled.risk_labels
    assert "STALE_DATA" in labeled.risk_labels
    assert "HUGE_SPREAD_VERIFY" in labeled.risk_labels
    assert "WIDE_SPREAD" in labeled.risk_labels
    assert "SAME_TICKER_RISK" in labeled.risk_labels
    assert "FUNDING_AGAINST" in labeled.risk_labels
    assert "MARK_INDEX_DEVIATION" in labeled.risk_labels


def test_clean_opportunity_has_no_labels() -> None:
    settings = RiskSettings(ticker_collision_symbols=[])
    labeled = apply_risk_labels(
        opportunity(
            symbol="BTCUSDT",
            open_spread_pct=0.4,
            close_spread_pct=0.5,
            spread_width_pct=0.1,
            buy_volume_24h_usdt=100_000_000,
            sell_volume_24h_usdt=100_000_000,
            funding_rate_buy_pct=0.0,
            funding_rate_sell_pct=0.02,
            net_funding_pct=0.02,
            mark_index_diff_buy_pct=0.01,
            mark_index_diff_sell_pct=0.02,
            last_seen_at=datetime.now(UTC),
        ),
        settings=settings,
        now=datetime.now(UTC),
    )

    assert labeled.risk_labels == []


def test_non_actionable_filter_blocks_obvious_bad_opportunities() -> None:
    assert has_non_actionable_risk(
        opportunity(risk_labels=["HUGE_SPREAD_VERIFY"])
    )
    assert has_non_actionable_risk(
        opportunity(risk_labels=["LOW_VOLUME", "FUNDING_AGAINST"])
    )
    assert has_non_actionable_risk(
        opportunity(risk_labels=["SAME_TICKER_RISK"])
    )
    assert not has_non_actionable_risk(
        opportunity(risk_labels=["FUNDING_AGAINST"])
    )
