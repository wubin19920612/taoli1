from datetime import UTC, datetime

from app.models.alert import AlertRule
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.services.alert_messages import build_alert_message


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
    assert "买入腿：bitget 股票现货" in text
    assert "卖出腿：okx 合约" in text
    assert "买入原始标的：bitget RSOXLUSDT" in text
    assert "卖出原始标的：okx SOXL-USDT-SWAP" in text
    assert "提示：Bitget 股票现货使用 RToken 原始标的，Astro 对应交易所使用 bitgetr。" in text
    assert "spot" not in text
    assert "future" not in text
