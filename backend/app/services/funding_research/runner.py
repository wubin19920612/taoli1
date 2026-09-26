from datetime import UTC, datetime, timedelta

from app.models.market import MarketSnapshot
from app.services.funding_research.engine import build_candidate, build_funding_research_candidates
from app.services.funding_research.depth import FundingDepthAdapter, orderbook_depth_stats_for_candidate
from app.services.funding_research.models import (
    FundingResearchCandidate,
    FundingResearchPaperTrade,
    FundingResearchSettings,
)
from app.services.funding_research.paper import (
    open_paper_trades_for_candidates,
    reconcile_open_paper_trades,
)
from app.services.funding_research.repository import FundingResearchRepository


class FundingResearchRunResult:
    def __init__(
        self,
        *,
        observed_at: datetime,
        market_snapshot_count: int,
        candidate_snapshot_count: int,
        pruned_snapshot_count: int,
        candidates: list[FundingResearchCandidate],
        opened_paper_trades: list[FundingResearchPaperTrade] | None = None,
        closed_paper_trades: list[FundingResearchPaperTrade] | None = None,
    ):
        self.observed_at = observed_at
        self.market_snapshot_count = market_snapshot_count
        self.candidate_snapshot_count = candidate_snapshot_count
        self.pruned_snapshot_count = pruned_snapshot_count
        self.candidates = candidates
        self.opened_paper_trades = opened_paper_trades or []
        self.closed_paper_trades = closed_paper_trades or []


async def record_funding_research_run(
    *,
    markets: list[MarketSnapshot],
    repo: FundingResearchRepository,
    settings: FundingResearchSettings | None = None,
    observed_at: datetime | None = None,
    manage_paper_trades: bool = False,
    depth_adapters: list[FundingDepthAdapter] | None = None,
    orderbook_depth_levels: int = 20,
) -> FundingResearchRunResult:
    current = observed_at or datetime.now(UTC)
    resolved_settings = settings or FundingResearchSettings()
    candidates = build_funding_research_candidates(markets, settings=resolved_settings, now=current)
    if depth_adapters:
        candidates = await _enrich_candidates_with_orderbook_depth(
            candidates,
            markets=markets,
            settings=resolved_settings,
            observed_at=current,
            depth_adapters=depth_adapters,
            levels=orderbook_depth_levels,
        )
    market_count = await repo.create_market_snapshots(markets)
    candidate_count = await repo.create_candidate_snapshots(candidates, observed_at=current)
    pruned_count = 0
    if resolved_settings.snapshot_retention_hours > 0:
        cutoff = current - timedelta(hours=resolved_settings.snapshot_retention_hours)
        pruned_count = await repo.prune_snapshots_before(cutoff)
    closed: list[FundingResearchPaperTrade] = []
    opened: list[FundingResearchPaperTrade] = []
    if manage_paper_trades:
        closed = await reconcile_open_paper_trades(
            candidates=candidates,
            repo=repo,
            observed_at=current,
            settings=resolved_settings,
        )
        opened = await open_paper_trades_for_candidates(
            candidates=candidates,
            repo=repo,
            opened_at=current,
        )
    return FundingResearchRunResult(
        observed_at=current,
        market_snapshot_count=market_count,
        candidate_snapshot_count=candidate_count,
        pruned_snapshot_count=pruned_count,
        candidates=candidates,
        opened_paper_trades=opened,
        closed_paper_trades=closed,
    )


async def _enrich_candidates_with_orderbook_depth(
    candidates: list[FundingResearchCandidate],
    *,
    markets: list[MarketSnapshot],
    settings: FundingResearchSettings,
    observed_at: datetime,
    depth_adapters: list[FundingDepthAdapter],
    levels: int,
) -> list[FundingResearchCandidate]:
    enriched: list[FundingResearchCandidate] = []
    market_by_key = {(market.symbol, market.exchange): market for market in markets}
    book_cache = {}
    for candidate in candidates:
        depth_stats = await orderbook_depth_stats_for_candidate(
            candidate,
            markets,
            depth_adapters,
            target_notional_usdt=settings.notional_per_symbol_usdt,
            levels=levels,
            book_cache=book_cache,
        )
        if depth_stats is None:
            enriched.append(candidate)
            continue
        long_leg = market_by_key.get((candidate.symbol, candidate.long_exchange))
        short_leg = market_by_key.get((candidate.symbol, candidate.short_exchange))
        if long_leg is None or short_leg is None:
            enriched.append(candidate.model_copy(update={"depth_stats": depth_stats}))
            continue
        rebuilt = build_candidate(
            long_leg,
            short_leg,
            settings=settings,
            now=observed_at,
            depth_stats=depth_stats,
        )
        enriched.append(rebuilt)
    return sorted(
        enriched,
        key=lambda item: (
            item.decision == "TRADE",
            item.decision == "SMALL_TRADE",
            item.decision == "WATCH",
            item.ev_pct if item.ev_pct is not None else -999,
            item.score,
        ),
        reverse=True,
    )
