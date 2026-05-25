from datetime import UTC, datetime

from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import LivePilotSettings, RiskSettings
from app.models.alert import AlertRule
from app.services.alert_engine import AlertMatch
from app.services.live_pilot import (
    filter_opportunities_by_alert_rules,
    preview_live_pilot_opportunities,
    select_live_pilot_matches,
    select_live_pilot_opportunities,
)


def opportunity(
    opportunity_id: str,
    symbol: str,
    buy_exchange: str,
    sell_exchange: str,
    edge: float,
    funding_edge: float,
    opportunity_type: OpportunityType = OpportunityType.FF,
    buy_market_type: MarketType = MarketType.FUTURE,
    sell_market_type: MarketType = MarketType.FUTURE,
) -> Opportunity:
    return Opportunity(
        id=opportunity_id,
        type=opportunity_type,
        symbol=symbol,
        buy_exchange=buy_exchange,
        buy_market_type=buy_market_type,
        sell_exchange=sell_exchange,
        sell_market_type=sell_market_type,
        open_spread_pct=edge + 0.2,
        close_spread_pct=0.2,
        fee_adjusted_open_pct=edge,
        spread_width_pct=0.3,
        buy_bid=99,
        buy_ask=100,
        sell_bid=101,
        sell_ask=102,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=12_000_000,
        funding_rate_buy_pct=0,
        funding_rate_sell_pct=funding_edge,
        funding_next_rate_buy_pct=0,
        funding_next_rate_sell_pct=funding_edge,
        funding_next_time_buy=datetime(2026, 5, 24, 8, tzinfo=UTC),
        funding_next_time_sell=datetime(2026, 5, 24, 8, tzinfo=UTC),
        net_funding_pct=funding_edge,
        net_funding_next_pct=funding_edge,
        buy_funding_interval_hours=8,
        sell_funding_interval_hours=8,
        net_funding_hourly_pct=None,
        net_funding_daily_pct=None,
        net_funding_next_hourly_pct=None,
        net_funding_next_daily_pct=None,
        mark_index_diff_buy_pct=0,
        mark_index_diff_sell_pct=funding_edge,
        risk_labels=[],
        last_seen_at=datetime(2026, 5, 24, 7, 50, tzinfo=UTC),
    )


def test_live_pilot_disabled_leaves_opportunities_untouched() -> None:
    opportunities = [
        opportunity("btc-a", "BTCUSDT", "binance", "okx", 0.40, 0.00),
        opportunity("eth-a", "ETHUSDT", "gate", "okx", 0.50, -0.20),
    ]

    selected = select_live_pilot_opportunities(
        opportunities,
        LivePilotSettings(enabled=False),
    )

    assert selected == opportunities


def test_live_pilot_prefers_hyper_route_per_symbol_and_skips_strong_negative_funding() -> None:
    opportunities = [
        opportunity("btc-best-edge", "BTCUSDT", "binance", "okx", 0.80, 0.00),
        opportunity("btc-hyper", "BTCUSDT", "hyperliquid", "okx", 0.55, 0.00),
        opportunity("eth-ok", "ETHUSDT", "binance", "okx", 0.60, -0.04),
        opportunity("xrp-negative", "XRPUSDT", "binance", "okx", 0.90, -0.08),
        opportunity("sol-ok", "SOLUSDT", "gate", "bybit", 0.50, 0.02),
    ]

    selected = select_live_pilot_opportunities(
        opportunities,
        LivePilotSettings(
            enabled=True,
            max_symbols=3,
            min_next_funding_edge_pct=-0.05,
            prefer_hyperliquid=True,
        ),
    )

    assert [item.id for item in selected] == ["eth-ok", "btc-hyper", "sol-ok"]


def test_live_pilot_limits_to_top_symbols_after_one_route_per_symbol() -> None:
    opportunities = [
        opportunity("btc", "BTCUSDT", "binance", "okx", 0.40, 0.00),
        opportunity("eth", "ETHUSDT", "binance", "okx", 0.70, 0.00),
        opportunity("sol", "SOLUSDT", "binance", "okx", 0.60, 0.00),
        opportunity("doge", "DOGEUSDT", "binance", "okx", 0.50, 0.00),
    ]

    selected = select_live_pilot_opportunities(
        opportunities,
        LivePilotSettings(enabled=True, max_symbols=2),
    )

    assert [item.id for item in selected] == ["eth", "sol"]


def test_live_pilot_excludes_spot_spot_opportunities_by_default() -> None:
    ff = opportunity("ff", "BTCUSDT", "binance", "okx", 0.50, 0.00)
    ss = opportunity(
        "ss",
        "ETHUSDT",
        "binance",
        "okx",
        0.90,
        0.00,
        opportunity_type=OpportunityType.SS,
        buy_market_type=MarketType.SPOT,
        sell_market_type=MarketType.SPOT,
    )

    selected, stats = preview_live_pilot_opportunities(
        [ss, ff],
        LivePilotSettings(enabled=True, max_symbols=10),
    )

    assert [item.id for item in selected] == ["ff"]
    assert stats.skipped_type == 1
    assert stats.eligible_symbols == 1


def test_live_pilot_can_allow_spot_spot_opportunities_when_configured() -> None:
    ss = opportunity(
        "ss",
        "ETHUSDT",
        "binance",
        "okx",
        0.90,
        0.00,
        opportunity_type=OpportunityType.SS,
        buy_market_type=MarketType.SPOT,
        sell_market_type=MarketType.SPOT,
    )

    selected, stats = preview_live_pilot_opportunities(
        [ss],
        LivePilotSettings(enabled=True, max_symbols=10, exclude_ss=False),
    )

    assert [item.id for item in selected] == ["ss"]
    assert stats.skipped_type == 0
    assert stats.eligible_symbols == 1


def test_live_pilot_enabled_skips_candidates_blocked_by_risk_settings() -> None:
    liquid = opportunity("liquid", "BTCUSDT", "binance", "okx", 0.50, 0.00)
    low_volume = opportunity("low-volume", "LOWUSDT", "binance", "okx", 0.90, 0.00).model_copy(
        update={
            "buy_volume_24h_usdt": 50_000,
            "sell_volume_24h_usdt": 60_000,
        }
    )
    stale = opportunity("stale", "OLDUSDT", "binance", "okx", 0.80, 0.00).model_copy(
        update={"risk_labels": ["STALE_DATA"]}
    )
    thin = opportunity("thin", "THINUSDT", "binance", "okx", 0.70, 0.00).model_copy(
        update={"risk_labels": ["THIN_ORDER_BOOK"]}
    )

    selected, stats = preview_live_pilot_opportunities(
        [low_volume, stale, thin, liquid],
        LivePilotSettings(enabled=True, max_symbols=10),
        RiskSettings(min_volume_24h_usdt=100_000, ticker_collision_symbols=[]),
        now=datetime(2026, 5, 24, 7, 50, tzinfo=UTC),
    )

    assert [item.id for item in selected] == ["liquid"]
    assert stats.skipped_risk == 3
    assert stats.eligible_symbols == 1


def test_live_pilot_preview_candidates_share_alert_rule_thresholds() -> None:
    rule = AlertRule(
        name="live pilot eligible",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.5,
        min_volume_24h_usdt=1_000_000,
    )
    below_rule = opportunity("below", "LOWEDGEUSDT", "binance", "okx", 0.30, 0.00)
    above_rule = opportunity("above", "HIGHEDGEUSDT", "binance", "okx", 0.55, 0.00)

    eligible = filter_opportunities_by_alert_rules(
        [below_rule, above_rule],
        [rule],
        RiskSettings(ticker_collision_symbols=[]),
        now=datetime(2026, 5, 24, 7, 50, tzinfo=UTC),
    )

    selected, stats = preview_live_pilot_opportunities(
        eligible,
        LivePilotSettings(enabled=True, max_symbols=10),
        RiskSettings(ticker_collision_symbols=[]),
        now=datetime(2026, 5, 24, 7, 50, tzinfo=UTC),
    )

    assert [item.id for item in selected] == ["above"]
    assert stats.total_opportunities == 1


def test_live_pilot_limits_alert_matches_to_one_per_symbol() -> None:
    rule_a = AlertRule(name="spread", min_fee_adjusted_open_pct=0.2)
    rule_b = AlertRule(name="funding", min_fee_adjusted_open_pct=0.1)
    btc_low = opportunity("btc-low", "BTCUSDT", "binance", "okx", 0.40, 0.00)
    btc_hyper = opportunity("btc-hyper", "BTCUSDT", "hyperliquid", "okx", 0.30, 0.00)
    eth = opportunity("eth", "ETHUSDT", "binance", "okx", 0.50, 0.00)
    matches = [
        AlertMatch(rule_a, btc_low, []),
        AlertMatch(rule_b, btc_hyper, []),
        AlertMatch(rule_a, eth, []),
    ]

    selected = select_live_pilot_matches(
        matches,
        LivePilotSettings(enabled=True, max_symbols=10, prefer_hyperliquid=True),
    )

    assert [(match.opportunity.symbol, match.opportunity.id) for match in selected] == [
        ("ETHUSDT", "eth"),
        ("BTCUSDT", "btc-hyper"),
    ]


def test_live_pilot_alert_matches_exclude_spot_spot_by_default() -> None:
    rule = AlertRule(name="spread", min_fee_adjusted_open_pct=0.1)
    ss = opportunity(
        "ss",
        "ETHUSDT",
        "binance",
        "okx",
        0.90,
        0.00,
        opportunity_type=OpportunityType.SS,
        buy_market_type=MarketType.SPOT,
        sell_market_type=MarketType.SPOT,
    )
    ff = opportunity("ff", "BTCUSDT", "binance", "okx", 0.50, 0.00)

    selected = select_live_pilot_matches(
        [AlertMatch(rule, ss, []), AlertMatch(rule, ff, [])],
        LivePilotSettings(enabled=True, max_symbols=10),
    )

    assert [match.opportunity.id for match in selected] == ["ff"]
