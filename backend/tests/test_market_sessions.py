from datetime import UTC, datetime

from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import RiskSettings
from app.services.data_filters import filter_opportunities
from app.services.market_sessions import (
    is_bitget_rtoken_spot_open,
    is_market_snapshot_tradable,
    is_opportunity_tradable,
    is_us_stock_market_open,
    us_stock_session_close,
)


def test_us_stock_session_uses_new_york_regular_hours() -> None:
    assert is_us_stock_market_open(datetime(2026, 9, 9, 13, 29, tzinfo=UTC)) is False
    assert is_us_stock_market_open(datetime(2026, 9, 9, 13, 30, tzinfo=UTC)) is True
    assert is_us_stock_market_open(datetime(2026, 9, 9, 19, 59, tzinfo=UTC)) is True
    assert is_us_stock_market_open(datetime(2026, 9, 9, 20, 0, tzinfo=UTC)) is False


def test_us_stock_session_excludes_weekends_holidays_and_supports_early_close() -> None:
    assert is_us_stock_market_open(datetime(2026, 9, 12, 15, 0, tzinfo=UTC)) is False
    assert is_us_stock_market_open(datetime(2026, 7, 3, 15, 0, tzinfo=UTC)) is False
    assert us_stock_session_close(datetime(2026, 11, 27, tzinfo=UTC).date()) is not None
    assert is_us_stock_market_open(datetime(2026, 11, 27, 17, 0, tzinfo=UTC)) is True
    assert is_us_stock_market_open(datetime(2026, 11, 27, 18, 0, tzinfo=UTC)) is False


def test_non_rtoken_markets_remain_tradable_around_stock_close() -> None:
    crypto = MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        exchange="bitget",
        market_type=MarketType.SPOT,
        bid=99,
        ask=100,
        timestamp=datetime(2026, 9, 9, tzinfo=UTC),
        raw_symbol="BTCUSDT",
    )

    assert is_bitget_rtoken_spot_open(
        "bitget",
        MarketType.SPOT,
        "BTCUSDT",
        "BTCUSDT",
        datetime(2026, 9, 9, 21, 0, tzinfo=UTC),
    )
    assert is_market_snapshot_tradable(
        crypto,
        datetime(2026, 9, 9, 21, 0, tzinfo=UTC),
    )


def test_rtoken_market_and_opportunity_are_not_tradable_when_closed() -> None:
    rtoken = MarketSnapshot(
        symbol="AAPLUSDT",
        base="AAPL",
        exchange="bitget",
        market_type=MarketType.SPOT,
        bid=99,
        ask=100,
        timestamp=datetime(2026, 9, 9, tzinfo=UTC),
        raw_symbol="RAAPLUSDT",
    )
    future = MarketSnapshot(
        symbol="AAPLUSDT",
        base="AAPL",
        exchange="okx",
        market_type=MarketType.FUTURE,
        bid=102,
        ask=103,
        timestamp=datetime(2026, 9, 9, tzinfo=UTC),
        raw_symbol="AAPL-USDT-SWAP",
    )
    opportunity = Opportunity(
        id="aapl",
        type=OpportunityType.SF,
        symbol="AAPLUSDT",
        buy_exchange="bitget",
        buy_market_type=MarketType.SPOT,
        buy_raw_symbol="RAAPLUSDT",
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        sell_raw_symbol="AAPL-USDT-SWAP",
        open_spread_pct=1,
        close_spread_pct=1,
        fee_adjusted_open_pct=0.8,
        spread_width_pct=0,
        buy_bid=99,
        buy_ask=100,
        sell_bid=102,
        sell_ask=103,
        buy_volume_24h_usdt=1_000_000,
        sell_volume_24h_usdt=1_000_000,
        risk_labels=[],
        last_seen_at=datetime(2026, 9, 9, tzinfo=UTC),
    )

    closed_at = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    assert not is_market_snapshot_tradable(rtoken, closed_at)
    assert is_market_snapshot_tradable(future, closed_at)
    assert not is_opportunity_tradable(opportunity, closed_at)
    assert filter_opportunities([opportunity], RiskSettings(), now=closed_at) == []
    assert filter_opportunities(
        [opportunity],
        RiskSettings(),
        now=datetime(2026, 9, 9, 15, 0, tzinfo=UTC),
    ) == [opportunity]
