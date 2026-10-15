from fastapi import APIRouter, HTTPException, Query, Request

from app.models.settings import RiskSettings
from app.services.data_filters import filter_markets
from app.services.funding_research import (
    FundingResearchCandidate,
    FundingResearchCandidateSnapshot,
    FundingResearchLegacyBacktestSummary,
    FundingResearchPaperTrade,
    FundingResearchPaperTradeSummary,
    FundingResearchRepository,
    FundingResearchSettings,
    LegacyBacktestSettings,
    backtest_legacy_opportunity_history,
    close_paper_trade,
    create_paper_trade_from_candidate,
    record_funding_research_run,
    summarize_paper_trades,
)

router = APIRouter(prefix="/funding-research")


def _repo(request: Request) -> FundingResearchRepository:
    repo = getattr(request.app.state, "funding_research_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Funding research repository is not ready")
    return repo


async def _risk_settings(request: Request) -> RiskSettings:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        return RiskSettings()
    get_settings = getattr(repo, "get_risk_settings", None)
    if get_settings is None:
        return RiskSettings()
    return await get_settings()


@router.get("/candidates", response_model=list[FundingResearchCandidate])
async def list_funding_research_candidates(
    request: Request,
    symbol: str | None = Query(default=None),
    opportunity_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[FundingResearchCandidate]:
    normalized_symbol = symbol.upper().replace("-", "").replace("_", "") if symbol else None
    candidates = await _repo(request).list_recent_candidates(symbol=normalized_symbol, limit=limit)
    if opportunity_type is None:
        return candidates
    normalized_type = opportunity_type.strip().upper()
    return [
        candidate
        for candidate in candidates
        if normalized_type in candidate.opportunity_types
    ]


@router.get("/candidate-snapshots", response_model=list[FundingResearchCandidateSnapshot])
async def list_funding_research_candidate_snapshots(
    request: Request,
    symbol: str | None = Query(default=None),
    long_exchange: str | None = Query(default=None),
    short_exchange: str | None = Query(default=None),
    limit: int = Query(default=240, ge=1, le=2000),
) -> list[FundingResearchCandidateSnapshot]:
    normalized_symbol = symbol.upper().replace("-", "").replace("_", "") if symbol else None
    return await _repo(request).list_candidate_snapshots(
        symbol=normalized_symbol,
        long_exchange=long_exchange.strip().lower() if long_exchange else None,
        short_exchange=short_exchange.strip().lower() if short_exchange else None,
        limit=limit,
    )


@router.get("/paper-trades", response_model=list[FundingResearchPaperTrade])
async def list_funding_research_paper_trades(
    request: Request,
    status: str | None = Query(default=None),
    opportunity_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[FundingResearchPaperTrade]:
    normalized_status = status.upper() if status else None
    trades = await _repo(request).list_paper_trades(status=normalized_status, limit=limit)
    if opportunity_type is None:
        return trades
    normalized_type = opportunity_type.strip().upper()
    return [
        trade
        for trade in trades
        if normalized_type in trade.opportunity_types
    ]


@router.get("/paper-trades/summary", response_model=FundingResearchPaperTradeSummary)
async def get_funding_research_paper_trade_summary(
    request: Request,
    opportunity_type: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10_000),
) -> FundingResearchPaperTradeSummary:
    trades = await _repo(request).list_paper_trades(limit=limit)
    if opportunity_type is not None:
        normalized_type = opportunity_type.strip().upper()
        trades = [
            trade
            for trade in trades
            if normalized_type in trade.opportunity_types
        ]
    return summarize_paper_trades(trades)


@router.post("/paper-trades/open/{candidate_id}", response_model=FundingResearchPaperTrade)
async def open_funding_research_paper_trade(
    candidate_id: str,
    request: Request,
) -> FundingResearchPaperTrade:
    repo = _repo(request)
    candidates = await repo.list_recent_candidates(limit=1_000)
    candidate = next((item for item in candidates if item.id == candidate_id), None)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    trade = create_paper_trade_from_candidate(candidate)
    return await repo.upsert_paper_trade(trade)


@router.post("/paper-trades/{trade_id}/close", response_model=FundingResearchPaperTrade)
async def close_funding_research_paper_trade(
    trade_id: str,
    request: Request,
    exit_reason: str = Query(default="manual"),
) -> FundingResearchPaperTrade:
    repo = _repo(request)
    trades = await repo.list_paper_trades(status="OPEN", limit=1_000)
    trade = next((item for item in trades if item.id == trade_id), None)
    if trade is None:
        raise HTTPException(status_code=404, detail="Open paper trade not found")
    candidates = await repo.list_recent_candidates(symbol=trade.symbol, limit=1_000)
    candidate = next(
        (
            item
            for item in candidates
            if item.long_exchange == trade.long_exchange
            and item.short_exchange == trade.short_exchange
        ),
        None,
    )
    closed = close_paper_trade(trade, candidate, exit_reason=exit_reason)
    return await repo.upsert_paper_trade(closed)


@router.post("/run")
async def run_funding_research_once(
    request: Request,
    manage_paper_trades: bool = Query(default=True),
    snapshot_retention_hours: float | None = Query(default=None, ge=0),
) -> dict:
    risk_settings = await _risk_settings(request)
    markets = filter_markets(
        request.app.state.snapshot_store.get_markets(),
        risk_settings,
    )
    app_settings = getattr(request.app.state, "settings", None)
    resolved_retention_hours = (
        snapshot_retention_hours
        if snapshot_retention_hours is not None
        else getattr(app_settings, "funding_research_snapshot_retention_hours", 72.0)
    )
    result = await record_funding_research_run(
        markets=markets,
        repo=_repo(request),
        settings=FundingResearchSettings(
            snapshot_retention_hours=resolved_retention_hours,
        ),
        manage_paper_trades=manage_paper_trades,
        depth_adapters=getattr(getattr(request.app.state, "market_collector", None), "adapters", None),
        orderbook_depth_levels=20,
    )
    return {
        "observed_at": result.observed_at.isoformat(),
        "market_snapshot_count": result.market_snapshot_count,
        "candidate_snapshot_count": result.candidate_snapshot_count,
        "pruned_snapshot_count": result.pruned_snapshot_count,
        "candidate_count": len(result.candidates),
        "opened_paper_trade_count": len(result.opened_paper_trades),
        "closed_paper_trade_count": len(result.closed_paper_trades),
        "top_candidates": [
            item.model_dump(mode="json")
            for item in result.candidates[:10]
        ],
    }


@router.get("/legacy-backtest", response_model=FundingResearchLegacyBacktestSummary)
async def run_legacy_opportunity_history_backtest(
    request: Request,
    symbol: str | None = Query(default=None),
    hours: float = Query(default=24 * 7, gt=0, le=24 * 90),
    limit: int = Query(default=10_000, ge=1, le=100_000),
    min_entry_edge_pct: float = Query(default=0.4),
    min_next_funding_pct: float = Query(default=0.2),
    cost_pct: float = Query(default=0.3, ge=0),
    max_hold_observations: int = Query(default=3, ge=1, le=100),
) -> FundingResearchLegacyBacktestSummary:
    repo = getattr(request.app.state, "history_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Opportunity history repository is not ready")
    from datetime import UTC, datetime, timedelta

    normalized_symbol = symbol.upper().replace("-", "").replace("_", "") if symbol else None
    rows = await repo.list(
        symbol=normalized_symbol,
        since=datetime.now(UTC) - timedelta(hours=hours),
        limit=limit,
    )
    return backtest_legacy_opportunity_history(
        rows,
        settings=LegacyBacktestSettings(
            min_entry_edge_pct=min_entry_edge_pct,
            min_next_funding_pct=min_next_funding_pct,
            cost_pct=cost_pct,
            max_hold_observations=max_hold_observations,
        ),
    )
