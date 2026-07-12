from datetime import UTC, datetime

from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity_radar import OpportunityRadarSettings
from app.services.opportunity_radar import build_opportunity_radar_preview


NOW = datetime(2026, 7, 12, 8, 0, tzinfo=UTC)


def market(
    exchange: str,
    *,
    bid: float,
    ask: float,
    mark: float,
    index: float = 100,
    funding: float | None = 0,
    interval: int | None = 8,
    volume: float | None = 5_000_000,
    size: float | None = 200,
    symbol: str = "BTCUSDT",
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        base=symbol.removesuffix("USDT"),
        exchange=exchange,
        market_type=MarketType.FUTURE,
        bid=bid,
        ask=ask,
        bid_size=size,
        ask_size=size,
        volume_24h_usdt=volume,
        funding_next_rate_pct=funding,
        funding_interval_hours=interval,
        mark_price=mark,
        index_price=index,
        timestamp=NOW,
        raw_symbol=symbol,
    )


def test_negative_anchor_premium_builds_long_anchor_trial_candidate() -> None:
    preview = build_opportunity_radar_preview(
        [
            market("bybit", bid=99.95, ask=100.05, mark=98, funding=-0.10, interval=1),
            market("binance", bid=100.25, ask=100.35, mark=100, funding=0.04, interval=4),
            market("okx", bid=100.15, ask=100.25, mark=99.9, funding=0.02, interval=4),
        ],
        OpportunityRadarSettings(min_depth_multiple=1),
        now=NOW,
    )

    assert preview.displayed_candidates == 2
    candidate = next(item for item in preview.candidates if item.peer_exchange == "binance")
    assert candidate.direction == "LONG_ANCHOR_SHORT_PEER"
    assert candidate.long_exchange == "bybit"
    assert candidate.short_exchange == "binance"
    assert candidate.anchor_premium_pct == -2
    assert candidate.relative_premium_gap_pct == 2
    assert 0 < candidate.entry_spread_pct < 0.5
    assert candidate.hourly_funding_edge_pct == 0.11


def test_positive_anchor_premium_reverses_the_trade_direction() -> None:
    preview = build_opportunity_radar_preview(
        [
            market("bybit", bid=100.20, ask=100.30, mark=102, funding=0.10, interval=1),
            market("binance", bid=100.00, ask=100.10, mark=100, funding=0.04, interval=4),
        ],
        OpportunityRadarSettings(min_depth_multiple=1),
        now=NOW,
    )

    assert preview.displayed_candidates == 1
    candidate = preview.candidates[0]
    assert candidate.direction == "LONG_PEER_SHORT_ANCHOR"
    assert candidate.long_exchange == "binance"
    assert candidate.short_exchange == "bybit"
    assert candidate.relative_premium_gap_pct == 2


def test_manual_spread_threshold_filters_candidates() -> None:
    markets = [
        market("bybit", bid=99.95, ask=100.05, mark=98),
        market("binance", bid=100.25, ask=100.35, mark=100),
    ]

    wide = build_opportunity_radar_preview(
        markets,
        OpportunityRadarSettings(max_abs_entry_spread_pct=0.5, min_depth_multiple=1),
        now=NOW,
    )
    narrow = build_opportunity_radar_preview(
        markets,
        OpportunityRadarSettings(max_abs_entry_spread_pct=0.1, min_depth_multiple=1),
        now=NOW,
    )

    assert wide.displayed_candidates == 1
    assert narrow.displayed_candidates == 0


def test_relative_premium_threshold_and_funding_alignment_are_configurable() -> None:
    markets = [
        market("bybit", bid=99.95, ask=100.05, mark=98, funding=0.10, interval=1),
        market("binance", bid=100.15, ask=100.25, mark=99, funding=0.04, interval=4),
    ]

    loose = build_opportunity_radar_preview(
        markets,
        OpportunityRadarSettings(
            min_relative_premium_gap_pct=0.5,
            min_depth_multiple=1,
        ),
        now=NOW,
    )
    strict = build_opportunity_radar_preview(
        markets,
        OpportunityRadarSettings(
            min_relative_premium_gap_pct=1.5,
            min_depth_multiple=1,
        ),
        now=NOW,
    )
    funding_aligned = build_opportunity_radar_preview(
        markets,
        OpportunityRadarSettings(
            min_relative_premium_gap_pct=0.5,
            require_funding_alignment=True,
            min_hourly_funding_edge_pct=0,
            min_depth_multiple=1,
        ),
        now=NOW,
    )

    assert loose.displayed_candidates == 1
    assert strict.displayed_candidates == 0
    assert funding_aligned.displayed_candidates == 0


def test_htx_is_not_considered_as_a_radar_peer() -> None:
    preview = build_opportunity_radar_preview(
        [
            market("bybit", bid=99.95, ask=100.05, mark=98),
            market("htx", bid=100.25, ask=100.35, mark=100),
        ],
        OpportunityRadarSettings(min_depth_multiple=1),
        now=NOW,
    )

    assert preview.displayed_candidates == 0
    assert preview.total_pairs_evaluated == 0
