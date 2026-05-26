from datetime import UTC, datetime, timedelta

from app.models.funding_arbitrage import FundingArbitrageSettings
from app.models.market import MarketSnapshot, MarketType
from app.services.funding_arbitrage import build_funding_arbitrage_preview


def market(
    exchange: str,
    market_type: MarketType,
    *,
    bid: float,
    ask: float,
    funding_next_rate_pct: float | None = None,
    funding_rate_pct: float | None = None,
    mark_price: float | None = None,
    index_price: float | None = None,
    volume_24h_usdt: float | None = 5_000_000,
    bid_size: float = 200,
    ask_size: float = 200,
    next_time: datetime | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        exchange=exchange,
        market_type=market_type,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        volume_24h_usdt=volume_24h_usdt,
        funding_next_rate_pct=funding_next_rate_pct,
        funding_rate_pct=funding_rate_pct,
        funding_interval_hours=8 if market_type == MarketType.FUTURE else None,
        funding_next_time=next_time,
        mark_price=mark_price,
        index_price=index_price,
        timestamp=datetime(2026, 5, 26, 8, 0, tzinfo=UTC),
        raw_symbol="BTCUSDT",
    )


def test_sf_positive_funding_can_enter_when_basis_and_adl_are_low() -> None:
    now = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
    preview = build_funding_arbitrage_preview(
        [
            market("binance", MarketType.SPOT, bid=99.9, ask=100.0),
            market(
                "okx",
                MarketType.FUTURE,
                bid=100.25,
                ask=100.35,
                funding_next_rate_pct=0.12,
                mark_price=100.3,
                index_price=100.0,
                next_time=now + timedelta(minutes=30),
            ),
        ],
        FundingArbitrageSettings(min_entry_edge_pct=0.01, min_funding_edge_pct=0.01),
        now=now,
    )

    assert preview.displayed_candidates == 1
    candidate = preview.candidates[0]
    assert candidate.type == "SF"
    assert candidate.decision == "ENTER"
    assert candidate.long_market_type == "spot"
    assert candidate.short_market_type == "future"
    assert candidate.next_funding_edge_pct == 0.12
    assert candidate.expected_cycle_pnl_pct > 0


def test_ff_orients_higher_funding_leg_as_short() -> None:
    now = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
    preview = build_funding_arbitrage_preview(
        [
            market(
                "binance",
                MarketType.FUTURE,
                bid=100.0,
                ask=100.1,
                funding_next_rate_pct=0.01,
                mark_price=100.0,
                index_price=100.0,
                next_time=now + timedelta(minutes=20),
            ),
            market(
                "okx",
                MarketType.FUTURE,
                bid=100.2,
                ask=100.3,
                funding_next_rate_pct=0.18,
                mark_price=100.2,
                index_price=100.0,
                next_time=now + timedelta(minutes=20),
            ),
        ],
        FundingArbitrageSettings(min_entry_edge_pct=0.01, min_funding_edge_pct=0.01),
        now=now,
    )

    candidate = preview.candidates[0]
    assert candidate.type == "FF"
    assert candidate.long_exchange == "binance"
    assert candidate.short_exchange == "okx"
    assert candidate.next_funding_edge_pct == 0.17


def test_missing_funding_blocks_futures_candidate() -> None:
    now = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
    preview = build_funding_arbitrage_preview(
        [
            market("binance", MarketType.SPOT, bid=99.9, ask=100.0),
            market(
                "okx",
                MarketType.FUTURE,
                bid=100.25,
                ask=100.35,
                funding_next_rate_pct=None,
                funding_rate_pct=None,
                next_time=now + timedelta(minutes=30),
            ),
        ],
        FundingArbitrageSettings(min_entry_edge_pct=0.01, min_funding_edge_pct=0.01),
        now=now,
    )

    assert preview.displayed_candidates == 1
    assert preview.candidates[0].decision == "BLOCKED"
    assert "missing funding" in " ".join(preview.candidates[0].decision_reasons).lower()
    assert preview.blocked_missing_funding == 1


def test_current_funding_fallback_adds_confidence_penalty() -> None:
    now = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
    preview = build_funding_arbitrage_preview(
        [
            market("binance", MarketType.SPOT, bid=99.9, ask=100.0),
            market(
                "okx",
                MarketType.FUTURE,
                bid=100.25,
                ask=100.35,
                funding_next_rate_pct=None,
                funding_rate_pct=0.12,
                next_time=now + timedelta(minutes=30),
            ),
        ],
        FundingArbitrageSettings(
            min_entry_edge_pct=0.01,
            min_funding_edge_pct=0.01,
            confidence_penalty_pct=0.02,
        ),
        now=now,
    )

    candidate = preview.candidates[0]
    assert candidate.funding_source == "fallback_current"
    assert candidate.confidence_penalty_pct == 0.02
    assert candidate.next_funding_edge_pct == 0.12


def test_ss_routes_are_not_built() -> None:
    now = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
    preview = build_funding_arbitrage_preview(
        [
            market("binance", MarketType.SPOT, bid=99.9, ask=100.0),
            market("okx", MarketType.SPOT, bid=100.2, ask=100.3),
        ],
        FundingArbitrageSettings(),
        now=now,
    )

    assert preview.total_pairs_evaluated == 0
    assert preview.candidates == []


def test_high_adl_proxy_blocks_entry() -> None:
    now = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
    preview = build_funding_arbitrage_preview(
        [
            market("binance", MarketType.SPOT, bid=99.9, ask=100.0),
            market(
                "okx",
                MarketType.FUTURE,
                bid=105.0,
                ask=105.2,
                funding_next_rate_pct=0.25,
                mark_price=105.0,
                index_price=100.0,
                next_time=now + timedelta(minutes=30),
            ),
        ],
        FundingArbitrageSettings(
            min_entry_edge_pct=0.01,
            min_funding_edge_pct=0.01,
            max_mark_index_deviation_pct=1,
            adl_block_score=50,
        ),
        now=now,
    )

    candidate = preview.candidates[0]
    assert candidate.adl_risk_level == "BLOCKED"
    assert candidate.decision == "BLOCKED"
    assert preview.blocked_adl_risk == 1


def test_settlement_window_blocks_premature_funding_entry() -> None:
    now = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
    preview = build_funding_arbitrage_preview(
        [
            market("binance", MarketType.SPOT, bid=99.9, ask=100.0),
            market(
                "okx",
                MarketType.FUTURE,
                bid=100.25,
                ask=100.35,
                funding_next_rate_pct=0.12,
                mark_price=100.3,
                index_price=100.0,
                next_time=now + timedelta(minutes=180),
            ),
        ],
        FundingArbitrageSettings(
            min_entry_edge_pct=0.01,
            min_funding_edge_pct=0.01,
            max_minutes_to_settlement=90,
        ),
        now=now,
    )

    candidate = preview.candidates[0]
    assert candidate.decision == "BLOCKED"
    assert "settlement window" in " ".join(candidate.decision_reasons).lower()


def test_missing_volume_blocks_funding_entry() -> None:
    now = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
    preview = build_funding_arbitrage_preview(
        [
            market("binance", MarketType.SPOT, bid=99.9, ask=100.0, volume_24h_usdt=None),
            market(
                "okx",
                MarketType.FUTURE,
                bid=100.25,
                ask=100.35,
                funding_next_rate_pct=0.12,
                mark_price=100.3,
                index_price=100.0,
                volume_24h_usdt=5_000_000,
                next_time=now + timedelta(minutes=30),
            ),
        ],
        FundingArbitrageSettings(min_entry_edge_pct=0.01, min_funding_edge_pct=0.01),
        now=now,
    )

    candidate = preview.candidates[0]
    assert candidate.decision == "BLOCKED"
    assert "LOW_VOLUME" in candidate.risk_labels
    assert preview.blocked_liquidity == 1


def test_known_thin_depth_blocks_funding_entry() -> None:
    now = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
    preview = build_funding_arbitrage_preview(
        [
            market("binance", MarketType.SPOT, bid=99.9, ask=100.0, ask_size=0.2),
            market(
                "okx",
                MarketType.FUTURE,
                bid=100.25,
                ask=100.35,
                bid_size=0.2,
                funding_next_rate_pct=0.12,
                mark_price=100.3,
                index_price=100.0,
                next_time=now + timedelta(minutes=30),
            ),
        ],
        FundingArbitrageSettings(
            min_entry_edge_pct=0.01,
            min_funding_edge_pct=0.01,
            notional_per_symbol_usdt=100,
        ),
        now=now,
    )

    candidate = preview.candidates[0]
    assert candidate.decision == "BLOCKED"
    assert "THIN_DEPTH" in candidate.risk_labels
    assert preview.blocked_liquidity == 1
