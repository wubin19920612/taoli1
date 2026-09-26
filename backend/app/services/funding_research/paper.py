from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha1

from app.services.funding_research.models import (
    FundingResearchCandidate,
    FundingResearchDecision,
    FundingResearchOpportunityTypeSummary,
    FundingResearchPaperTrade,
    FundingResearchPaperTradeSummary,
    FundingResearchSettings,
)
from app.services.funding_research.repository import FundingResearchRepository

OPEN_DECISIONS: set[FundingResearchDecision] = {"TRADE", "SMALL_TRADE"}


def paper_trade_id(candidate: FundingResearchCandidate) -> str:
    raw = candidate.id or (
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
        primary_opportunity_type=candidate.primary_opportunity_type,
        opportunity_types=list(candidate.opportunity_types),
        opened_at=resolved_opened_at,
        last_observed_at=resolved_opened_at,
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


def _basis_change_since_open(
    trade: FundingResearchPaperTrade,
    candidate: FundingResearchCandidate | None,
) -> float:
    if candidate is None or trade.open_basis_diff_pct is None or candidate.basis_diff_pct is None:
        return 0.0
    return trade.open_basis_diff_pct - candidate.basis_diff_pct


def _funding_realized_by(
    trade: FundingResearchPaperTrade,
    observed_at: datetime,
) -> float:
    settlement_time = trade.source_candidate.next_settlement_time
    if settlement_time is not None and observed_at >= settlement_time:
        return trade.expected_net_funding_pct or 0.0
    return 0.0


def mark_to_market_paper_trade(
    trade: FundingResearchPaperTrade,
    candidate: FundingResearchCandidate | None,
    *,
    observed_at: datetime | None = None,
) -> FundingResearchPaperTrade:
    if trade.status != "OPEN":
        return trade
    resolved_observed_at = observed_at or datetime.now(UTC)
    basis_change = _basis_change_since_open(trade, candidate)
    realized_or_projected_funding = _funding_realized_by(trade, resolved_observed_at)
    unrealized_pnl = realized_or_projected_funding + basis_change - trade.estimated_cost_pct
    current_adverse = trade.max_adverse_ev_pct
    max_adverse = (
        unrealized_pnl
        if current_adverse is None
        else min(current_adverse, unrealized_pnl)
    )
    return trade.model_copy(
        update={
            "last_observed_at": resolved_observed_at,
            "unrealized_basis_change_pct": basis_change,
            "unrealized_pnl_pct": unrealized_pnl,
            "max_adverse_ev_pct": max_adverse,
            "close_long_basis_pct": (
                candidate.long_basis_pct
                if candidate is not None
                else trade.close_long_basis_pct
            ),
            "close_short_basis_pct": (
                candidate.short_basis_pct
                if candidate is not None
                else trade.close_short_basis_pct
            ),
            "close_basis_diff_pct": (
                candidate.basis_diff_pct
                if candidate is not None
                else trade.close_basis_diff_pct
            ),
        }
    )


def close_paper_trade(
    trade: FundingResearchPaperTrade,
    candidate: FundingResearchCandidate | None,
    *,
    exit_reason: str,
    closed_at: datetime | None = None,
) -> FundingResearchPaperTrade:
    resolved_closed_at = closed_at or datetime.now(UTC)
    marked = mark_to_market_paper_trade(trade, candidate, observed_at=resolved_closed_at)
    realized_basis = marked.unrealized_basis_change_pct or 0.0
    realized_funding = _funding_realized_by(trade, resolved_closed_at)
    realized_pnl = realized_funding + realized_basis - trade.estimated_cost_pct
    close_long_basis = candidate.long_basis_pct if candidate is not None else trade.close_long_basis_pct
    close_short_basis = candidate.short_basis_pct if candidate is not None else trade.close_short_basis_pct
    close_basis_diff = candidate.basis_diff_pct if candidate is not None else trade.close_basis_diff_pct
    return marked.model_copy(
        update={
            "status": "CLOSED",
            "closed_at": resolved_closed_at,
            "last_observed_at": resolved_closed_at,
            "close_long_basis_pct": close_long_basis,
            "close_short_basis_pct": close_short_basis,
            "close_basis_diff_pct": close_basis_diff,
            "unrealized_basis_change_pct": realized_basis,
            "unrealized_pnl_pct": realized_pnl,
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
            marked_trade = mark_to_market_paper_trade(
                trade,
                candidate,
                observed_at=observed_at,
            )
            if marked_trade != trade:
                await repo.upsert_paper_trade(marked_trade)
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
    def average(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    closed_trades = 0
    open_trades = 0
    realized: list[float] = []
    expected_evs: list[float] = []
    funding: list[float] = []
    basis: list[float] = []
    scores: list[float] = []
    type_totals: dict[str, int] = defaultdict(int)
    type_closed: dict[str, int] = defaultdict(int)
    type_realized: dict[str, list[float]] = defaultdict(list)

    for item in trades:
        is_closed = item.status == "CLOSED"
        if is_closed:
            closed_trades += 1
            if item.expected_ev_pct is not None:
                expected_evs.append(item.expected_ev_pct)
            funding.append(item.realized_funding_pct)
            basis.append(item.realized_basis_change_pct)
            scores.append(item.score)
            if item.realized_pnl_pct is not None:
                realized.append(item.realized_pnl_pct)
        elif item.status == "OPEN":
            open_trades += 1

        for opportunity_type in item.opportunity_types:
            type_totals[opportunity_type] += 1
            if is_closed:
                type_closed[opportunity_type] += 1
                if item.realized_pnl_pct is not None:
                    type_realized[opportunity_type].append(item.realized_pnl_pct)

    winners = sum(value > 0 for value in realized)
    losers = sum(value < 0 for value in realized)
    type_summaries = []
    for opportunity_type in sorted(type_totals):
        typed_realized = type_realized[opportunity_type]
        typed_winners = sum(value > 0 for value in typed_realized)
        typed_losers = sum(value < 0 for value in typed_realized)
        type_summaries.append(
            FundingResearchOpportunityTypeSummary(
                opportunity_type=opportunity_type,
                total_trades=type_totals[opportunity_type],
                closed_trades=type_closed[opportunity_type],
                winners=typed_winners,
                losers=typed_losers,
                win_rate_pct=(
                    typed_winners / len(typed_realized) * 100
                    if typed_realized
                    else None
                ),
                total_realized_pnl_pct=sum(typed_realized),
                average_realized_pnl_pct=average(typed_realized),
            )
        )
    return FundingResearchPaperTradeSummary(
        total_trades=len(trades),
        open_trades=open_trades,
        closed_trades=closed_trades,
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
        by_opportunity_type=type_summaries,
    )
