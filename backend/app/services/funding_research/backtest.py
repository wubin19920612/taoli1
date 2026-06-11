from dataclasses import dataclass
from datetime import datetime

from app.models.market import MarketSnapshot
from app.services.funding_research.models import (
    FundingResearchPaperTrade,
    FundingResearchPaperTradeSummary,
    FundingResearchSettings,
)
from app.services.funding_research.paper import summarize_paper_trades
from app.services.funding_research.repository import FundingResearchRepository
from app.services.funding_research.runner import FundingResearchRunResult, record_funding_research_run


@dataclass(frozen=True)
class FundingResearchReplayBatch:
    observed_at: datetime
    markets: list[MarketSnapshot]


@dataclass(frozen=True)
class FundingResearchReplayResult:
    runs: list[FundingResearchRunResult]
    trades: list[FundingResearchPaperTrade]
    summary: FundingResearchPaperTradeSummary


async def replay_funding_research_batches(
    *,
    batches: list[FundingResearchReplayBatch],
    repo: FundingResearchRepository,
    settings: FundingResearchSettings | None = None,
) -> FundingResearchReplayResult:
    runs: list[FundingResearchRunResult] = []
    for batch in sorted(batches, key=lambda item: item.observed_at):
        runs.append(
            await record_funding_research_run(
                markets=batch.markets,
                repo=repo,
                settings=settings,
                observed_at=batch.observed_at,
                manage_paper_trades=True,
            )
        )
    trades = await repo.list_paper_trades(limit=10_000)
    return FundingResearchReplayResult(
        runs=runs,
        trades=trades,
        summary=summarize_paper_trades(trades),
    )
