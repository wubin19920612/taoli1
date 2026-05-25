from datetime import UTC, datetime

import pytest

from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.models.settings import AstroCardSettings, RiskSettings
from app.services.orderbook_validator import OrderBookDepthValidator


def opportunity() -> Opportunity:
    return Opportunity(
        id="opp-1",
        type=OpportunityType.FF,
        symbol="BTCUSDT",
        buy_exchange="binance",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=1.0,
        close_spread_pct=0.5,
        fee_adjusted_open_pct=0.75,
        spread_width_pct=0.5,
        buy_bid=99,
        buy_ask=100,
        sell_bid=101,
        sell_ask=102,
        buy_volume_24h_usdt=20_000_000,
        sell_volume_24h_usdt=22_000_000,
        funding_rate_buy_pct=0,
        funding_rate_sell_pct=0,
        net_funding_pct=0,
        risk_labels=[],
        last_seen_at=datetime.now(UTC),
    )


class FakeAdapter:
    def __init__(self, exchange: str, book: OrderBookSnapshot | None):
        self.name = exchange
        self.book = book
        self.calls: list[tuple[str, MarketType, str, int]] = []

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot | None:
        self.calls.append((symbol, market_type, raw_symbol, limit))
        return self.book


def book(exchange: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        exchange=exchange,
        market_type=MarketType.FUTURE,
        symbol="BTCUSDT",
        raw_symbol="BTCUSDT",
        bids=[OrderBookLevel(price=price, size=size) for price, size in bids],
        asks=[OrderBookLevel(price=price, size=size) for price, size in asks],
        timestamp=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_validator_uses_minimum_signal_notional_when_card_size_is_smaller() -> None:
    buy_book = book("binance", bids=[(99, 20)], asks=[(100, 20)])
    sell_book = book("okx", bids=[(101, 20)], asks=[(102, 20)])
    validator = OrderBookDepthValidator(
        [FakeAdapter("binance", buy_book), FakeAdapter("okx", sell_book)]
    )

    result = await validator.validate(
        opportunity(),
        risk_settings=RiskSettings(
            signal_validation_notional_usdt=1000,
            signal_slippage_buffer_pct=0.05,
            min_effective_open_pct=0.1,
            ticker_collision_symbols=[],
        ),
        card_settings=AstroCardSettings(max_trade_usdt=80, max_notional=80),
    )

    assert result.passed is True
    assert result.target_notional_usdt == 1000
    assert result.buy_filled_usdt == pytest.approx(1000)
    assert result.sell_filled_usdt == pytest.approx(1000)
    assert result.executable_open_pct == pytest.approx(1.0)
    assert result.effective_executable_edge_pct == pytest.approx(0.70)


@pytest.mark.asyncio
async def test_validator_can_use_explicit_override_notional_for_small_live_pilot() -> None:
    buy_book = book("binance", bids=[(99, 20)], asks=[(100, 20)])
    sell_book = book("okx", bids=[(101, 20)], asks=[(102, 20)])
    validator = OrderBookDepthValidator(
        [FakeAdapter("binance", buy_book), FakeAdapter("okx", sell_book)]
    )

    result = await validator.validate(
        opportunity(),
        risk_settings=RiskSettings(
            signal_validation_notional_usdt=1000,
            signal_slippage_buffer_pct=0.05,
            min_effective_open_pct=0.1,
            ticker_collision_symbols=[],
        ),
        card_settings=AstroCardSettings(max_trade_usdt=100, max_notional=100),
        override_notional_usdt=100,
    )

    assert result.passed is True
    assert result.target_notional_usdt == 100
    assert result.buy_filled_usdt == pytest.approx(100)
    assert result.sell_filled_usdt == pytest.approx(100)


@pytest.mark.asyncio
async def test_validator_blocks_when_multi_level_vwap_erases_edge() -> None:
    buy_book = book(
        "binance",
        bids=[(99, 20)],
        asks=[(100, 5), (101, 20)],
    )
    sell_book = book(
        "okx",
        bids=[(101, 5), (100.2, 20)],
        asks=[(102, 20)],
    )
    validator = OrderBookDepthValidator(
        [FakeAdapter("binance", buy_book), FakeAdapter("okx", sell_book)]
    )

    result = await validator.validate(
        opportunity(),
        risk_settings=RiskSettings(
            signal_validation_notional_usdt=1000,
            signal_slippage_buffer_pct=0.05,
            min_effective_open_pct=0.25,
            ticker_collision_symbols=[],
        ),
        card_settings=AstroCardSettings(max_trade_usdt=1000, max_notional=1000),
    )

    assert result.passed is False
    assert result.buy_vwap is not None
    assert result.sell_vwap is not None
    assert result.executable_open_pct < 0.5
    assert "effective executable edge" in " ".join(result.blockers)
