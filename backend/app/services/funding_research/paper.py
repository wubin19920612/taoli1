from datetime import UTC, datetime
from hashlib import sha1

from app.services.funding_research.models import (
    FundingResearchCandidate,
    FundingResearchDecision,
    FundingResearchPaperTrade,
    FundingResearchPaperTradeSummary,
    FundingResearchSettings,
)
from app.services.funding_research.repository import FundingResearchRepository

OPEN_DECISIONS: set[FundingResearchDecision] = {"TRADE", "SMALL_TRADE"}


def paper_trade_id(candidate: FundingResearchCandidate) -> str:
    raw = (
        f"{candidate.symbol}:{candidate.long_exchange}:"
        f"{candidate.short_exchange}:{candidate.next_settlement_time}"
    )
    return sha1(raw.encode("utf-8")).hexdigest()[:20]


def create_paper_trade_from_candidate(
    candidate: FundingResearchCandidate,
    *,
    opened_at: datetime | None = None,
) -> FundingResearchPaperTrade:
    settlement_time = candidate.next_settlement_time
    if settlement_time is None:
        raise ValueError("candidate must have next_settlement_time to open a paper trade")
    resolved_opened_at = opened_at or datetime.now(UTC)
    return FundingResearchPaperTrade(
        id=paper_trade_id(candidate),
        status="OPEN",
        symbol=candidate.symbol,
        long_exchange=candidate.long_exchange,
        short_exchange=candidate.short_exchange,
        opened_at=resolved_opened_at,
        open_long_basis_pct=candidate.long_basis_pct,
        open_short_basis_pct=candidate.short_basis_pct,
        open_basis_diff_pct=candidate.basis_diff_pct,
        expected_net_funding_pct=candidate.expected_net_funding_pct,
        expected_basis_change_pct=candidate.expected_basis_change_pct,
        expected_ev_pct=candidate.ev_pct,
        score=candidate.score,
        decision=candidate.decision,
        estimated_cost_pct=candidate.estimated_cost_pct,
        source_candidate=candidate,
    )


def close_paper_trade(
    trade: FundingResearchPaperTrade,
    candidate: FundingResearchCandidate | None,
    *,
    exit_reason: str,
    closed_at: datetime | None = None,
) -> FundingResearchPaperTrade:
    resolved_closed_at = closed_at or datetime.now(UTC)
    realized_basis = 0.0
    if candidate is not None and trade.open_basis_diff_pct is not None and candidate.basis_diff_pct is not None:
        realized_basis = trade.open_basis_diff_pct - candidate.basis_diff_pct
    settlement_time = trade.source_candidate.next_settlement_time
    realized_funding = (
        trade.expected_net_funding_pct or 0.0
        if settlement_time is not None and resolved_closed_at >= settlement_time
        else 0.0
    )
    realized_pnl = realized_funding + realized_basis - trade.estimated_cost_pct
    close_long_basis = candidate.long_basis_pct if candidate is not None else trade.close_long_basis_pct
    close_short_basis = candidate.short_basis_pct if candidate is not None else trade.close_short_basis_pct
    close_basis_diff = candidate.basis_diff_pct if candidate is not None else trade.close_basis_diff_pct
    return trade.model_copy(
        update={
            "status": "CLOSED",
            "closed_at": resolved_closed_at,
            "close_long_basis_pct": close_long_basis,
            "close_short_basis_pct": close_short_basis,
            "close_basis_diff_pct": close_basis_diff,
            "realized_funding_pct": realized_funding,
            "realized_basis_change_pct": realized_basis,
            "realized_pnl_pct": realized_pnl,
            "exit_reason": exit_reason,
        }
    )


async def open_paper_trades_for_candidates(
    *,
    candidates: list[FundingResearchCandidate],
    repo: FundingResearchRepository,
    opened_at: datetime | None = None,
) -> list[FundingResearchPaperTrade]:
    existing = await repo.list_paper_trades(status="OPEN", limit=1_000)
    existing_ids = {item.id for item in existing}
    opened: list[FundingResearchPaperTrade] = []
    for candidate in candidates:
        if candidate.decision not in OPEN_DECISIONS:
            continue
        if candidate.next_settlement_time is None:
            continue
        trade = create_paper_trade_from_candidate(candidate, opened_at=opened_at)
        if trade.id in existing_ids:
            continue
        opened.append(await repo.upsert_paper_trade(trade))
    return opened


def _pair_key_from_candidate(candidate: FundingResearchCandidate) -> tuple[str, str, str]:
    return (candidate.symbol, candidate.long_exchange, candidate.short_exchange)


def _pair_key_from_trade(trade: FundingResearchPaperTrade) -> tuple[str, str, str]:
    return (trade.symbol, trade.long_exchange, trade.short_exchange)


def _close_reason(
    trade: FundingResearchPaperTrade,
    candidate: FundingResearchCandidate | None,
    *,
    observed_at: datetime,
    settings: FundingResearchSettings,
) -> str | None:
    settlement_time = trade.source_candidate.next_settlement_time
    if settlement_time is not None and observed_at >= settlement_time:
        return "settlement_reached"
    if candidate is None:
        return "candidate_missing"
    if candidate.expected_net_funding_pct is not None and candidate.expected_net_funding_pct <= 0:
        return "funding_edge_gone"
    if candidate.ev_pct is None or candidate.ev_pct < settings.min_watch_ev_pct:
        return "ev_deteriorated"
    if candidate.basis_alignment == "conflicted":
        return "basis_conflicted"
    return None


async def reconcile_open_paper_trades(
    *,
    candidates: list[FundingResearchCandidate],
    repo: FundingResearchRepository,
    observed_at: datetime,
    settings: FundingResearchSettings | None = None,
) -> list[FundingResearchPaperTrade]:
    resolved = settings or FundingResearchSettings()
    latest_by_pair = {
        _pair_key_from_candidate(candidate): candidate
        for candidate in candidates
    }
    closed: list[FundingResearchPaperTrade] = []
    for trade in await repo.list_paper_trades(status="OPEN", limit=1_000):
        candidate = latest_by_pair.get(_pair_key_from_trade(trade))
        reason = _close_reason(trade, candidate, observed_at=observed_at, settings=resolved)
        if reason is None:
            continue
        closed_trade = close_paper_trade(
            trade,
            candidate,
            exit_reason=reason,
            closed_at=observed_at,
        )
        closed.append(await repo.upsert_paper_trade(closed_trade))
    return closed


def summarize_paper_trades(
    trades: list[FundingResearchPaperTrade],
) -> FundingResearchPaperTradeSummary:
    closed = [item for item in trades if item.status == "CLOSED"]
    open_trades = [item for item in trades if item.status == "OPEN"]
    realized = [
        item.realized_pnl_pct
        for item in closed
        if item.realized_pnl_pct is not None
    ]
    winners = sum(value > 0 for value in realized)
    losers = sum(value < 0 for value in realized)

    def average(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    expected_evs = [
        item.expected_ev_pct
        for item in closed
        if item.expected_ev_pct is not None
    ]
    funding = [item.realized_funding_pct for item in closed]
    basis = [item.realized_basis_change_pct for item in closed]
    scores = [item.score for item in closed]
    return FundingResearchPaperTradeSummary(
        total_trades=len(trades),
        open_trades=len(open_trades),
        closed_trades=len(closed),
        winners=winners,
        losers=losers,
        win_rate_pct=(winners / len(realized) * 100 if realized else None),
        total_realized_pnl_pct=sum(realized),
        average_realized_pnl_pct=average(realized),
        average_expected_ev_pct=average(expected_evs),
        average_realized_funding_pct=average(funding),
        average_realized_basis_change_pct=average(basis),
        max_win_pct=max(realized) if realized else None,
        max_loss_pct=min(realized) if realized else None,
        average_score=average(scores),
    )
