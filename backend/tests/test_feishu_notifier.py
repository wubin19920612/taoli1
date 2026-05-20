from datetime import UTC, datetime

from app.models.alert import AlertRule, AlertSeverity
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.services.feishu import FeishuConfig, FeishuNotifier


def make_opportunity() -> Opportunity:
    return Opportunity(
        id="opp-1",
        type=OpportunityType.FF,
        symbol="BTCUSDT",
        buy_exchange="binance",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=0.8,
        close_spread_pct=0.5,
        fee_adjusted_open_pct=0.6,
        spread_width_pct=0.3,
        buy_bid=99.0,
        buy_ask=100.0,
        sell_bid=100.8,
        sell_ask=101.0,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=12_000_000,
        funding_rate_buy_pct=0.01,
        funding_rate_sell_pct=-0.02,
        funding_next_rate_buy_pct=0.015,
        funding_next_rate_sell_pct=0.025,
        funding_next_time_buy=datetime(2026, 5, 15, 8, 0, tzinfo=UTC),
        funding_next_time_sell=datetime(2026, 5, 15, 8, 0, tzinfo=UTC),
        net_funding_pct=-0.03,
        net_funding_next_pct=0.01,
        buy_funding_interval_hours=8,
        sell_funding_interval_hours=8,
        net_funding_hourly_pct=-0.00375,
        net_funding_daily_pct=-0.09,
        net_funding_next_hourly_pct=0.00125,
        net_funding_next_daily_pct=0.03,
        mark_index_diff_buy_pct=0.01,
        mark_index_diff_sell_pct=0.02,
        risk_labels=["FUNDING_AGAINST"],
        last_seen_at=datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
    )


def test_build_payload_explains_rule_parameters() -> None:
    rule = AlertRule(
        name="FF 价差",
        severity=AlertSeverity.WARNING,
        types=["FF"],
        include_exchanges=["binance"],
        exclude_exchanges=["gate"],
        include_symbols=["BTCUSDT"],
        exclude_symbols=["BADUSDT"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        max_data_age_seconds=600,
        excluded_risk_labels=["LOW_VOLUME"],
        consecutive_hits=3,
        cooldown_seconds=300,
    )
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))

    payload = notifier._build_payload(rule, make_opportunity(), "https://example.com")
    text = payload["content"]["text"]

    assert "【告警触发】" in text
    assert "规则：FF 价差" in text
    assert "等级：warning（普通告警）" in text
    assert "套利类型：FF（永续买入 / 永续卖出）" in text
    assert "包含交易所：binance" in text
    assert "排除交易所：gate" in text
    assert "包含标的：BTCUSDT" in text
    assert "排除标的：BADUSDT" in text
    assert "开仓阈值：>= 0.500%" in text
    assert "净估算阈值：>= 0.250%" in text
    assert "最低成交额：>= 1000K USDT" in text
    assert "数据时效：<= 600s" in text
    assert "排除风险：LOW_VOLUME" in text
    assert "连续命中：3 次" in text
    assert "冷却时间：300s" in text
    assert "【行情快照】" in text
    assert "买入腿：binance future" in text
    assert "卖出腿：okx future" in text
    assert "资金费率：0.01% / -0.02%" in text
    assert "风险：FUNDING_AGAINST" in text
