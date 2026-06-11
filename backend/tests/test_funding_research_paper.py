from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.models.market import MarketSnapshot, MarketType
from app.services.funding_research.engine import build_candidate
from app.services.funding_research.paper import (
    close_paper_trade,
    create_paper_trade_from_candidate,
    open_paper_trades_for_candidates,
    reconcile_open_paper_trades,
    summarize_paper_trades,
)
from app.services.funding_research.repository import FundingResearchRepository


def future(exchange: str, funding: float, mark: float, index: float) -> MarketSnapshot:
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    return MarketSnapshot(
        symbol="LABUSDT",
        base="LAB",
        exchange=exchange,
        market_type=MarketType.FUTURE,
        bid=mark - 0.01,
        ask=mark + 0.01,
        bid_size=2_000,
        ask_size=2_000,
        volume_24h_usdt=500_000_000,
        funding_rate_pct=funding,
        funding_next_rate_pct=funding,
        funding_interval_hours=2,
        funding_next_time=now + timedelta(minutes=45),
        mark_price=mark,
        index_price=index,
        timestamp=now,
        raw_symbol="LABUSDT",
    )


def trade_candidate():
    now = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    return build_candidate(
        future("binance", -1.6, 8.8, 9.5),
        future("okx", -0.1, 9.4, 9.5),
        now=now,
    )


def test_create_and_close_paper_trade_from_candidate() -> None:
    candidate = trade_candidate()
    trade = create_paper_trade_from_candidate(candidate)
    closing_candidate = candidate.model_copy(update={"basis_diff_pct": candidate.basis_diff_pct - 0.5})

    closed = close_paper_trade(trade, closing_candidate, exit_reason="settlement")

    assert trade.status == "OPEN"
    assert closed.status == "CLOSED"
    assert closed.realized_basis_change_pct == pytest.approx(0.5)
    assert closed.realized_pnl_pct is not None


@pytest.mark.asyncio
async def test_open_paper_trades_dedupes_open_candidates() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = FundingResearchRepository(db)
        candidate = trade_candidate()
        first = await open_paper_trades_for_candidates(candidates=[candidate], repo=repo)
        second = await open_paper_trades_for_candidates(candidates=[candidate], repo=repo)
        loaded = await repo.list_paper_trades(status="OPEN")

        assert len(first) == 1
        assert second == []
        assert len(loaded) == 1
        assert loaded[0].symbol == "LABUSDT"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_reconcile_closes_trade_at_settlement() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = FundingResearchRepository(db)
        candidate = trade_candidate()
        opened = await open_paper_trades_for_candidates(
            candidates=[candidate],
            repo=repo,
            opened_at=datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
        )

        closing_candidate = candidate.model_copy(
            update={"basis_diff_pct": candidate.basis_diff_pct - 0.4}
        )
        closed = await reconcile_open_paper_trades(
            candidates=[closing_candidate],
            repo=repo,
            observed_at=candidate.next_settlement_time or datetime(2026, 6, 1, 8, 45, tzinfo=UTC),
        )
        loaded_closed = await repo.list_paper_trades(status="CLOSED")

        assert len(opened) == 1
        assert len(closed) == 1
        assert len(loaded_closed) == 1
        assert loaded_closed[0].exit_reason == "settlement_reached"
        assert loaded_closed[0].realized_funding_pct == pytest.approx(
            candidate.expected_net_funding_pct
        )
        assert loaded_closed[0].realized_basis_change_pct == pytest.approx(0.4)
    finally:
        await db.close()


def test_summarize_paper_trades() -> None:
    candidate = trade_candidate()
    trade = create_paper_trade_from_candidate(
        candidate,
        opened_at=datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
    )
    closing_candidate = candidate.model_copy(update={"basis_diff_pct": candidate.basis_diff_pct - 0.2})
    closed = close_paper_trade(
        trade,
        closing_candidate,
        exit_reason="settlement",
        closed_at=candidate.next_settlement_time,
    )

    summary = summarize_paper_trades([closed])

    assert summary.total_trades == 1
    assert summary.closed_trades == 1
    assert summary.open_trades == 0
    assert summary.winners == 1
    assert summary.win_rate_pct == 100
    assert summary.average_realized_pnl_pct == pytest.approx(closed.realized_pnl_pct)
