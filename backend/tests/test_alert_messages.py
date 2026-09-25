from datetime import UTC, datetime

import pytest

from app.models.alert import AlertRule
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import AlertMessageTemplateSettings
from app.services.alert_messages import build_alert_message, build_alert_rating_header


def test_alert_message_distinguishes_market_types_and_bitget_stock_spot() -> None:
    opportunity = Opportunity(
        id="stock-opp",
        type=OpportunityType.SF,
        symbol="SOXLUSDT",
        buy_exchange="bitget",
        buy_market_type=MarketType.SPOT,
        buy_raw_symbol="RSOXLUSDT",
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        sell_raw_symbol="SOXL-USDT-SWAP",
        open_spread_pct=1.405,
        close_spread_pct=1.437,
        fee_adjusted_open_pct=1.205,
        spread_width_pct=0.032,
        buy_bid=311.18,
        buy_ask=311.29,
        sell_bid=315.5,
        sell_ask=315.8,
        buy_volume_24h_usdt=2_680_699,
        sell_volume_24h_usdt=1_104_000,
        funding_rate_buy_pct=0,
        funding_rate_sell_pct=0.04,
        funding_next_rate_buy_pct=None,
        funding_next_rate_sell_pct=0.04,
        funding_next_time_buy=None,
        funding_next_time_sell=None,
        net_funding_pct=0.04,
        net_funding_next_pct=0.04,
        buy_funding_interval_hours=None,
        sell_funding_interval_hours=8,
        risk_labels=[],
        last_seen_at=datetime(2026, 9, 9, tzinfo=UTC),
    )

    text = build_alert_message(AlertRule(name="股票价差"), opportunity)

    assert "价差对：SOXLUSDT | bitget 股票现货 -> okx 合约" in text
    assert "方向：买入 bitget 股票现货 SOXLUSDT，卖出 okx 合约 SOXLUSDT" in text
    assert "资金费率条件开仓阈值：>= 0.900%" in text
    assert "买入腿：bitget 股票现货" in text
    assert "卖出腿：okx 合约" in text
    assert "买入原始标的：bitget RSOXLUSDT" in text
    assert "卖出原始标的：okx SOXL-USDT-SWAP" in text
    assert "提示：Bitget 股票现货使用 RToken 原始标的，Astro 对应交易所使用 bitgetr。" in text
    assert "spot" not in text
    assert "future" not in text


def test_compact_alert_keeps_exact_market_identity_and_essential_metrics() -> None:
    opportunity = _rating_opportunity().model_copy(update={
        "sell_exchange": "hyperliquid",
        "sell_raw_symbol": "xyz:BTC",
        "sell_dex": "xyz",
        "sell_price_multiplier": 1000,
        "buy_funding_interval_hours": 8,
        "sell_funding_interval_hours": 1,
    })
    text = build_alert_message(
        AlertRule(name="价差"), opportunity,
        template=AlertMessageTemplateSettings(format="compact"),
    )

    assert "买 binance 合约 BTCUSDT → 卖 hyperliquid 合约 xyz:BTC（DEX xyz, 价格倍率 1000）" in text
    assert "盘口价差：开仓 1.200% / 平仓 0.800%；费后开仓估算 1.000%" in text
    assert "当前 买 0.010%/8h / 卖 0.050%/1h" in text
    assert "24h成交额：买 12,000,000 USDT / 卖 15,000,000 USDT" in text
    assert "【规则参数】" not in text
    assert "【连续监测】" not in text


def _rating_opportunity() -> Opportunity:
    return Opportunity(
        id="rating-opp", type=OpportunityType.FF, symbol="BTCUSDT",
        buy_exchange="binance", buy_market_type=MarketType.FUTURE,
        sell_exchange="okx", sell_market_type=MarketType.FUTURE,
        open_spread_pct=1.2, close_spread_pct=0.8,
        fee_adjusted_open_pct=1.0, spread_width_pct=0.4,
        buy_bid=99, buy_ask=100, sell_bid=101.2, sell_ask=102,
        buy_volume_24h_usdt=12_000_000, sell_volume_24h_usdt=15_000_000,
        funding_rate_buy_pct=0.01, funding_rate_sell_pct=0.05,
        net_funding_pct=0.04, net_funding_next_pct=0.04,
        buy_funding_interval_hours=8, sell_funding_interval_hours=8,
        risk_labels=[], last_seen_at=datetime(2026, 9, 16, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("updates", "rating", "reason"),
    [
        ({}, "强烈推荐", "双边24h成交额均不低于1,000万USDT"),
        ({"net_funding_next_pct": 0}, "推荐", "资金差接近零"),
        ({"net_funding_next_pct": -0.03}, "需评估", "资金费率与正开仓价差方向相反"),
        ({"sell_volume_24h_usdt": 8_000_000}, "推荐", "未达到高成交门槛"),
        ({"sell_volume_24h_usdt": None}, "推荐", "数据不全"),
        ({"buy_funding_interval_hours": 1}, "需评估", "结算周期不同"),
        ({"buy_funding_interval_hours": None}, "需评估", "结算周期未知"),
        ({"risk_labels": ["MARK_INDEX_DEVIATION"]}, "需评估", "风险标签"),
        ({"fee_adjusted_open_pct": -0.1}, "需评估", "扣除基础成本"),
        ({"net_funding_next_pct": None, "funding_rate_buy_pct": None,
          "funding_rate_sell_pct": None, "net_funding_pct": None}, "需评估", "资金费率数据不足"),
    ],
)
def test_alert_rating_title_reflects_spread_funding_volume_and_risk(
    updates: dict, rating: str, reason: str
) -> None:
    text = build_alert_message(AlertRule(name="价差"), _rating_opportunity().model_copy(update=updates))

    assert text.startswith(f"【{rating}】BTCUSDT FF binance→okx\n评级依据：")
    assert reason in text.split("【告警触发】", 1)[0]


def test_alert_rating_uses_stricter_rule_volume_and_can_be_downgraded() -> None:
    rule = AlertRule(name="高成交", min_volume_24h_usdt=8_000_000)
    opportunity = _rating_opportunity()

    assert build_alert_rating_header(rule, opportunity).startswith("【推荐】")
    assert build_alert_rating_header(rule, opportunity, validation_failed=True).startswith("【需评估】")
    assert "校验未通过" in build_alert_rating_header(rule, opportunity, validation_failed=True)
