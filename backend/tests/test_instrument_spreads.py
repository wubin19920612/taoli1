from datetime import UTC, datetime, timedelta

import pytest

from app.models.market import MarketSnapshot, MarketType
from app.services.instrument_spreads import build_instrument_spreads


def market(
    exchange: str,
    market_type: MarketType,
    *,
    bid: float,
    ask: float,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        exchange=exchange,
        market_type=market_type,
        bid=bid,
        ask=ask,
        timestamp=datetime(2026, 9, 13, tzinfo=UTC),
        raw_symbol="BTCUSDT",
    )


def test_build_instrument_spreads_keeps_best_executable_direction_and_sorts() -> None:
    now = datetime(2026, 9, 13, tzinfo=UTC)
    comparisons = build_instrument_spreads(
        [
            market("binance", MarketType.SPOT, bid=99, ask=100),
            market("binance", MarketType.FUTURE, bid=101, ask=102),
            market("okx", MarketType.FUTURE, bid=98, ask=99),
        ],
        now=now,
    )

    assert len(comparisons) == 3
    assert [item.id for item in comparisons] == [
        "okx:future->binance:future",
        "binance:spot->binance:future",
        "okx:future->binance:spot",
    ]
    assert comparisons[0].executable_spread_pct == pytest.approx(2.0)
    assert comparisons[0].price_difference == 2
    assert comparisons[0].opportunity_type == "FF"
    assert comparisons[0].astro_supported is True
    assert comparisons[1].executable_spread_pct == pytest.approx(200 / 201)
    assert comparisons[1].opportunity_type == "SF"
    assert comparisons[1].astro_supported is True
    assert comparisons[2].executable_spread_pct == 0
    assert comparisons[2].opportunity_type is None
    assert comparisons[2].astro_supported is False


def test_build_instrument_spreads_lists_spot_pair_but_marks_astro_unsupported() -> None:
    now = datetime(2026, 9, 13, tzinfo=UTC)
    [comparison] = build_instrument_spreads(
        [
            market("binance", MarketType.SPOT, bid=99, ask=100),
            market("gate", MarketType.SPOT, bid=102, ask=103),
        ],
        now=now,
    )

    assert comparison.id == "binance:spot->gate:spot"
    assert comparison.opportunity_type == "SS"
    assert comparison.astro_supported is False
    assert comparison.astro_blocker == "Astro 暂不支持现货对现货卡片"


def test_build_instrument_spreads_excludes_stale_market_snapshots() -> None:
    now = datetime(2026, 9, 13, 1, tzinfo=UTC)
    fresh = market("binance", MarketType.FUTURE, bid=101, ask=102).model_copy(
        update={"timestamp": now - timedelta(seconds=30)}
    )
    stale = market("okx", MarketType.FUTURE, bid=98, ask=99).model_copy(
        update={"timestamp": now - timedelta(seconds=31)}
    )

    comparisons = build_instrument_spreads(
        [fresh, stale],
        stale_after_seconds=30,
        now=now,
    )

    assert comparisons == []
