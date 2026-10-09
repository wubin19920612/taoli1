from datetime import UTC, datetime, timedelta

import pytest

from app.models.fat_finger_backtest import FatFingerBacktestRequest
from app.models.second_level_sampling import SecondLevelMarketSample
from app.services.fat_finger_backtest import run_fat_finger_backtest


def _sample(
    observed_at: datetime,
    exchange: str,
    *,
    spot_bid: float | None = None,
    spot_ask: float | None = None,
    spot_bid_size: float | None = None,
    spot_ask_size: float | None = None,
    future_bid: float | None = None,
    future_ask: float | None = None,
    future_bid_size: float | None = None,
    future_ask_size: float | None = None,
) -> SecondLevelMarketSample:
    return SecondLevelMarketSample(
        observed_at=observed_at,
        exchange=exchange,
        symbol="TESTUSDT",
        status="ok",
        spot_bid=spot_bid,
        spot_ask=spot_ask,
        spot_bid_size=spot_bid_size,
        spot_ask_size=spot_ask_size,
        future_bid=future_bid,
        future_ask=future_ask,
        future_bid_size=future_bid_size,
        future_ask_size=future_ask_size,
    )


def _request(**updates: object) -> FatFingerBacktestRequest:
    values: dict[str, object] = {
        "symbol": "TEST",
        "market_mode": "SF",
        "hours": 1,
        "sample_limit": 10_000,
        "entry_spread_pct": 1,
        "ladder_levels": 1,
        "ladder_step_pct": 0,
        "order_notional_usdt": 100,
        "maker_fill_assumption_pct": 100,
        "maker_fee_pct": 0,
        "taker_fee_pct": 0,
        "taker_slippage_pct": 0,
        "hedge_delay_seconds": 1,
        "order_expiry_seconds": 20,
        "take_profit_pct": 0.1,
        "max_hold_seconds": 30,
        "min_hedge_depth_usdt": 100,
        "max_quote_age_seconds": 5,
        "require_known_hedge_depth": True,
        "cooldown_seconds": 30,
    }
    values.update(updates)
    return FatFingerBacktestRequest(**values)


def test_spot_future_fat_finger_backtest_hedges_and_exits_after_reversion() -> None:
    started_at = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
    samples = [
        _sample(
            started_at,
            "gate",
            spot_bid=99.9,
            spot_ask=100,
            spot_bid_size=10,
            spot_ask_size=10,
        ),
        _sample(
            started_at,
            "binance",
            future_bid=100,
            future_ask=100.1,
            future_bid_size=10,
            future_ask_size=10,
        ),
        _sample(
            started_at + timedelta(seconds=1),
            "gate",
            spot_bid=98.8,
            spot_ask=98.9,
            spot_bid_size=10,
            spot_ask_size=10,
        ),
        _sample(
            started_at + timedelta(seconds=2),
            "binance",
            future_bid=100,
            future_ask=100.1,
            future_bid_size=10,
            future_ask_size=10,
        ),
        _sample(
            started_at + timedelta(seconds=3),
            "gate",
            spot_bid=100,
            spot_ask=100.1,
            spot_bid_size=10,
            spot_ask_size=10,
        ),
    ]

    result = run_fat_finger_backtest(samples, _request())

    assert result.quote_touch_count >= 1
    assert result.hedge_completed_count >= 1
    assert result.closed_trade_count >= 1
    assert result.target_exit_count >= 1
    trade = next(
        item
        for item in result.trades
        if item.maker_exchange == "gate"
        and item.maker_market_type == "spot"
        and item.hedge_exchange == "binance"
        and item.hedge_market_type == "future"
        and item.maker_side == "buy"
    )
    assert trade.exit_reason == "target"
    assert trade.net_pnl_usdt > 0
    assert trade.hedge_delay_seconds == pytest.approx(1)


def test_strict_depth_mode_skips_routes_without_known_hedge_size() -> None:
    started_at = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
    samples = [
        _sample(
            started_at,
            "gate",
            spot_bid=99.9,
            spot_ask=100,
            spot_bid_size=10,
            spot_ask_size=10,
        ),
        _sample(
            started_at,
            "binance",
            future_bid=100,
            future_ask=100.1,
        ),
        _sample(
            started_at + timedelta(seconds=1),
            "gate",
            spot_bid=98.8,
            spot_ask=98.9,
            spot_bid_size=10,
            spot_ask_size=10,
        ),
    ]

    result = run_fat_finger_backtest(samples, _request())

    assert result.order_skipped_depth_count > 0
    assert result.quote_touch_count == 0
    assert result.hedge_completed_count == 0
    assert result.closed_trade_count == 0


def test_spot_future_backtest_includes_same_exchange_route() -> None:
    started_at = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
    samples = [
        _sample(
            started_at,
            "gate",
            spot_bid=99.9,
            spot_ask=100,
            spot_bid_size=10,
            spot_ask_size=10,
            future_bid=100,
            future_ask=100.1,
            future_bid_size=10,
            future_ask_size=10,
        ),
        _sample(
            started_at + timedelta(seconds=1),
            "gate",
            spot_bid=98.8,
            spot_ask=98.9,
            spot_bid_size=10,
            spot_ask_size=10,
            future_bid=100,
            future_ask=100.1,
            future_bid_size=10,
            future_ask_size=10,
        ),
        _sample(
            started_at + timedelta(seconds=2),
            "gate",
            spot_bid=100,
            spot_ask=100.1,
            spot_bid_size=10,
            spot_ask_size=10,
            future_bid=100,
            future_ask=100.1,
            future_bid_size=10,
            future_ask_size=10,
        ),
    ]

    result = run_fat_finger_backtest(samples, _request(hedge_delay_seconds=0))

    assert any(
        trade.maker_exchange == "gate"
        and trade.maker_market_type == "spot"
        and trade.hedge_exchange == "gate"
        and trade.hedge_market_type == "future"
        for trade in result.trades
    )


def test_strict_depth_mode_keeps_position_open_when_exit_depth_is_insufficient() -> None:
    started_at = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
    samples = [
        _sample(
            started_at,
            "gate",
            spot_bid=99.9,
            spot_ask=100,
            spot_bid_size=10,
            spot_ask_size=10,
        ),
        _sample(
            started_at,
            "binance",
            future_bid=100,
            future_ask=100.1,
            future_bid_size=10,
            future_ask_size=10,
        ),
        _sample(
            started_at + timedelta(seconds=1),
            "gate",
            spot_bid=98.8,
            spot_ask=98.9,
            spot_bid_size=10,
            spot_ask_size=10,
        ),
        _sample(
            started_at + timedelta(seconds=2),
            "binance",
            future_bid=100,
            future_ask=100.1,
            future_bid_size=10,
            future_ask_size=10,
        ),
        _sample(
            started_at + timedelta(seconds=3),
            "gate",
            spot_bid=100,
            spot_ask=100.1,
            spot_bid_size=0.01,
            spot_ask_size=0.01,
        ),
        _sample(
            started_at + timedelta(seconds=4),
            "binance",
            future_bid=100,
            future_ask=100.1,
            future_bid_size=0.01,
            future_ask_size=0.01,
        ),
    ]

    result = run_fat_finger_backtest(samples, _request())

    assert result.hedge_completed_count >= 1
    assert result.exit_skipped_depth_count >= 1
    assert result.closed_trade_count == 0
    assert result.open_position_count >= 1
