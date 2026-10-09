from datetime import UTC, datetime, timedelta

import pytest

from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.services.funding_research.depth import orderbook_depth_stats_for_candidate
from app.services.funding_research.engine import build_candidate, build_funding_research_candidates
from app.services.funding_research.formulas import estimate_okx_with_rate_funding_pct
from app.services.funding_research.models import FundingFormulaEstimate, FundingResearchSettings


def future(
    exchange: str,
    *,
    bid: float,
    ask: float,
    funding: float,
    interval: int,
    mark: float,
    index: float,
    volume: float = 500_000_000,
    bid_size: float = 500,
    ask_size: float = 500,
    next_time: datetime | None = None,
) -> MarketSnapshot:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    return MarketSnapshot(
        symbol="LABUSDT",
        base="LAB",
        exchange=exchange,
        market_type=MarketType.FUTURE,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        volume_24h_usdt=volume,
        funding_rate_pct=funding,
        funding_next_rate_pct=funding,
        funding_interval_hours=interval,
        funding_next_time=next_time or now + timedelta(minutes=45),
        mark_price=mark,
        index_price=index,
        timestamp=now,
        raw_symbol="LABUSDT",
    )


def test_okx_with_rate_formula_applies_two_hour_adjustment() -> None:
    estimate = estimate_okx_with_rate_funding_pct(
        premium_pct=-2.16523356397911,
        interval_hours=2,
        interest_pct=0.01,
        cap_pct=1.5,
        floor_pct=-1.5,
    )

    assert estimate == pytest.approx(-0.5288083909947775)


def test_candidate_prefers_long_deeper_negative_funding_and_short_richer_basis() -> None:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    binance = future(
        "binance",
        bid=9.00,
        ask=9.01,
        funding=-0.474,
        interval=1,
        mark=9.00,
        index=9.42,
    )
    okx = future(
        "okx",
        bid=9.31,
        ask=9.32,
        funding=-0.529,
        interval=2,
        mark=9.31,
        index=9.54,
    )

    candidates = build_funding_research_candidates(
        [binance, okx],
        settings=FundingResearchSettings(target_holding_hours=2, min_volume_24h_usdt=100_000_000),
        now=now,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.long_exchange == "binance"
    assert candidate.short_exchange == "okx"
    assert candidate.expected_net_funding_pct == pytest.approx(0.419)
    assert candidate.basis_alignment == "aligned"
    assert candidate.expected_basis_change_pct > 0
    assert candidate.ev_pct is not None
    assert candidate.primary_opportunity_type == "BASIS_AND_FUNDING_ALIGNED"
    assert "BASIS_AND_FUNDING_ALIGNED" in candidate.opportunity_types
    assert candidate.id


def test_formula_estimate_can_override_stale_exchange_display_funding() -> None:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    gate = future(
        "gate",
        bid=9.02,
        ask=9.03,
        funding=-1.91,
        interval=4,
        mark=9.05,
        index=9.57,
        volume=160_000_000,
        next_time=now + timedelta(hours=2),
    )
    okx = future(
        "okx",
        bid=9.31,
        ask=9.32,
        funding=-1.5,
        interval=2,
        mark=9.31,
        index=9.54,
        volume=1_400_000_000,
        next_time=now + timedelta(minutes=45),
    )
    okx_formula = FundingFormulaEstimate(
        funding_rate_pct=-0.529,
        source="formula",
        formula_version="okx-with-rate-8-over-n",
        interval_hours=2,
        next_time=okx.funding_next_time,
    )

    candidate = build_candidate(
        gate,
        okx,
        settings=FundingResearchSettings(target_holding_hours=4, min_volume_24h_usdt=100_000_000),
        now=now,
        short_funding_estimate=okx_formula,
    )

    assert candidate.long_exchange == "gate"
    assert candidate.short_exchange == "okx"
    assert candidate.short_funding_pct == pytest.approx(-0.529)
    assert candidate.expected_net_funding_pct == pytest.approx(0.852)
    assert candidate.basis_alignment == "aligned"
    assert candidate.uses_gate
    assert "FORMULA_DIVERGENCE" in candidate.opportunity_types
    assert "INTERVAL_MISMATCH" in candidate.opportunity_types


def test_gate_and_hyperliquid_routes_are_classified_for_first_release_scope() -> None:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    gate = future(
        "gate",
        bid=10.00,
        ask=10.01,
        funding=-0.8,
        interval=4,
        mark=10.0,
        index=10.0,
        next_time=now + timedelta(minutes=20),
    )
    hyper = future(
        "hyperliquid",
        bid=10.03,
        ask=10.04,
        funding=0.1,
        interval=1,
        mark=10.03,
        index=10.0,
        next_time=now + timedelta(minutes=20),
    )

    candidate = build_candidate(
        gate,
        hyper,
        settings=FundingResearchSettings(
            target_holding_hours=4,
            min_volume_24h_usdt=100_000_000,
            strong_funding_pct=0.5,
            small_basis_threshold_pct=0.5,
            near_settlement_minutes=30,
        ),
        now=now,
    )

    assert candidate.uses_gate
    assert candidate.uses_hyperliquid
    assert candidate.long_formula_family == "gate_indicative"
    assert candidate.short_formula_family == "hyperliquid_hourly"
    assert "INTERVAL_MISMATCH" in candidate.opportunity_types
    assert "FORMULA_DIVERGENCE" in candidate.opportunity_types
    assert "STRONG_FUNDING_NEAR_SETTLEMENT" in candidate.opportunity_types


def test_conflicted_basis_penalizes_otherwise_positive_funding() -> None:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    cheap_funding = future(
        "thin",
        bid=10.4,
        ask=10.41,
        funding=-1.0,
        interval=2,
        mark=10.4,
        index=10.0,
    )
    rich_funding = future(
        "okx",
        bid=10.0,
        ask=10.01,
        funding=-0.2,
        interval=2,
        mark=10.0,
        index=10.0,
    )

    candidate = build_candidate(
        cheap_funding,
        rich_funding,
        settings=FundingResearchSettings(target_holding_hours=2),
        now=now,
    )

    assert candidate.expected_net_funding_pct == pytest.approx(0.8)
    assert candidate.basis_alignment == "conflicted"
    assert candidate.expected_basis_change_pct < 0
    assert candidate.adverse_basis_pct > 3
    assert candidate.conflicted_reward_risk_ratio == 0
    assert candidate.decision == "NO_TRADE"
    assert "POOR_REWARD_RISK" in candidate.risk_labels


def test_thin_but_tradeable_depth_keeps_high_funding_candidate_on_watchlist() -> None:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    okx = future(
        "okx",
        bid=10.0,
        ask=10.01,
        funding=-1.5,
        interval=2,
        mark=10.0,
        index=10.0,
        bid_size=500,
        ask_size=500,
    )
    gate = future(
        "gate",
        bid=10.0,
        ask=10.01,
        funding=-0.2,
        interval=2,
        mark=10.0,
        index=10.0,
        bid_size=500,
        ask_size=500,
    )

    candidate = build_candidate(
        okx,
        gate,
        settings=FundingResearchSettings(
            target_holding_hours=2,
            notional_per_symbol_usdt=1_000,
            min_depth_multiple=10,
        ),
        now=now,
    )

    assert candidate.expected_net_funding_pct == pytest.approx(1.3)
    assert candidate.ev_pct == pytest.approx(0.5)
    assert candidate.decision == "WATCH"
    assert "THIN_DEPTH" in candidate.risk_labels
    assert "entry depth below full safety multiple" in candidate.reasons


class FakeDepthAdapter:
    def __init__(self, name: str, asks: list[tuple[float, float]], bids: list[tuple[float, float]]) -> None:
        self.name = name
        self.asks = asks
        self.bids = bids
        self.calls = 0

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot:
        self.calls += 1
        now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
        return OrderBookSnapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=raw_symbol,
            asks=[OrderBookLevel(price=price, size=size) for price, size in self.asks[:limit]],
            bids=[OrderBookLevel(price=price, size=size) for price, size in self.bids[:limit]],
            timestamp=now,
        )


@pytest.mark.asyncio
async def test_orderbook_depth_stats_use_multiple_levels_for_entry_depth() -> None:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    long_market = future(
        "okx",
        bid=10.0,
        ask=10.01,
        funding=-1.5,
        interval=2,
        mark=10.0,
        index=10.0,
        bid_size=1,
        ask_size=1,
    )
    short_market = future(
        "gate",
        bid=10.2,
        ask=10.21,
        funding=-0.2,
        interval=2,
        mark=10.2,
        index=10.0,
        bid_size=1,
        ask_size=1,
    )
    candidate = build_candidate(
        long_market,
        short_market,
        settings=FundingResearchSettings(notional_per_symbol_usdt=1_000),
        now=now,
    )

    stats = await orderbook_depth_stats_for_candidate(
        candidate,
        [long_market, short_market],
        [
            FakeDepthAdapter("okx", asks=[(10.01, 1), (10.02, 300)], bids=[(10.0, 1)]),
            FakeDepthAdapter("gate", asks=[(10.21, 1)], bids=[(10.2, 1), (10.19, 300)]),
        ],
        target_notional_usdt=1_000,
        levels=20,
    )

    assert stats is not None
    assert stats.source == "orderbook"
    assert stats.levels == 20
    assert stats.long_entry_depth_usdt is not None
    assert stats.long_entry_depth_usdt > 1_000
    assert stats.short_entry_depth_usdt is not None
    assert stats.short_entry_depth_usdt > 1_000
    assert stats.min_entry_depth_usdt is not None
    assert stats.min_entry_depth_usdt > 1_000
    assert stats.long_entry_vwap is not None
    assert stats.short_entry_vwap is not None


@pytest.mark.asyncio
async def test_orderbook_depth_stats_reuses_cached_books() -> None:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    long_market = future(
        "okx",
        bid=10.0,
        ask=10.01,
        funding=-1.5,
        interval=2,
        mark=10.0,
        index=10.0,
    )
    short_market = future(
        "gate",
        bid=10.2,
        ask=10.21,
        funding=-0.2,
        interval=2,
        mark=10.2,
        index=10.0,
    )
    candidate = build_candidate(long_market, short_market, now=now)
    okx_adapter = FakeDepthAdapter("okx", asks=[(10.01, 300)], bids=[(10.0, 300)])
    gate_adapter = FakeDepthAdapter("gate", asks=[(10.21, 300)], bids=[(10.2, 300)])
    cache = {}

    first = await orderbook_depth_stats_for_candidate(
        candidate,
        [long_market, short_market],
        [okx_adapter, gate_adapter],
        target_notional_usdt=1_000,
        levels=20,
        book_cache=cache,
    )
    second = await orderbook_depth_stats_for_candidate(
        candidate,
        [long_market, short_market],
        [okx_adapter, gate_adapter],
        target_notional_usdt=1_000,
        levels=20,
        book_cache=cache,
    )

    assert first is not None
    assert second is not None
    assert okx_adapter.calls == 1
    assert gate_adapter.calls == 1
