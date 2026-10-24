from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


FundingSide = Literal["long", "short"]
FundingResearchDecision = Literal["TRADE", "SMALL_TRADE", "WATCH", "NO_TRADE"]
BasisAlignment = Literal["aligned", "neutral", "conflicted"]
FormulaConfidence = Literal["formula", "predicted", "fallback_current", "missing", "uncertain"]
PaperTradeStatus = Literal["OPEN", "CLOSED"]
FundingOpportunityType = Literal[
    "BASIS_AND_FUNDING_ALIGNED",
    "STRONG_FUNDING_NEAR_SETTLEMENT",
    "INTERVAL_MISMATCH",
    "FORMULA_DIVERGENCE",
    "BASIS_CARRY_CONFLICTED",
    "BASIS_MEAN_REVERSION",
    "PURE_FUNDING_SPREAD",
]


class FundingResearchSettings(BaseModel):
    target_holding_hours: float = Field(default=2.0, gt=0)
    snapshot_retention_hours: float = Field(default=72.0, ge=0)
    min_trade_ev_pct: float = 0.8
    min_small_trade_ev_pct: float = 0.4
    min_watch_ev_pct: float = 0.1
    min_trade_score: float = 75
    min_small_trade_score: float = 60
    min_watch_score: float = 45
    min_thin_depth_watch_ev_pct: float = 0.8
    min_thin_depth_watch_funding_pct: float = 1.0
    min_thin_depth_watch_score: float = 20
    min_volume_24h_usdt: float = Field(default=100_000_000, ge=0)
    notional_per_symbol_usdt: float = Field(default=1_000, gt=0)
    min_depth_multiple: float = Field(default=10, gt=0)
    open_fee_pct: float = Field(default=0.05, ge=0)
    close_fee_pct: float = Field(default=0.05, ge=0)
    base_slippage_pct: float = Field(default=0.2, ge=0)
    aligned_basis_capture_ratio: float = Field(default=0.15, ge=0, le=1)
    conflicted_basis_penalty_ratio: float = Field(default=0.20, ge=0, le=1)
    max_expected_basis_pct: float = Field(default=0.8, ge=0)
    basis_neutral_threshold_pct: float = Field(default=0.25, ge=0)
    basis_volatility_weight: float = Field(default=0.20, ge=0)
    low_liquidity_penalty_pct: float = Field(default=0.35, ge=0)
    thin_depth_penalty_pct: float = Field(default=0.5, ge=0)
    formula_uncertainty_penalty_pct: float = Field(default=0.5, ge=0)
    settlement_crowding_penalty_pct: float = Field(default=0.15, ge=0)
    settlement_crowding_minutes: float = Field(default=15, ge=0)
    min_minutes_to_settlement: float = Field(default=1, ge=0)
    max_minutes_to_settlement: float = Field(default=240, gt=0)
    strong_funding_pct: float = Field(default=0.6, ge=0)
    near_settlement_minutes: float = Field(default=45, ge=0)
    small_basis_threshold_pct: float = Field(default=0.25, ge=0)
    interval_mismatch_min_hours: float = Field(default=1, ge=0)
    formula_divergence_min_funding_pct: float = Field(default=0.25, ge=0)
    conflicted_basis_min_check_pct: float = Field(default=0.3, ge=0)
    min_conflicted_reward_risk_ratio: float = Field(default=1.0, ge=0)


class FundingFormulaEstimate(BaseModel):
    funding_rate_pct: float | None
    source: FormulaConfidence
    formula_version: str | None = None
    interval_hours: float | None = None
    next_time: datetime | None = None
    reason: str | None = None


class FundingResearchDepthStats(BaseModel):
    source: str = "ticker_top_of_book"
    levels: int = 1
    long_entry_depth_usdt: float | None = None
    short_entry_depth_usdt: float | None = None
    min_entry_depth_usdt: float | None = None
    target_notional_usdt: float
    long_entry_vwap: float | None = None
    short_entry_vwap: float | None = None
    executable_basis_diff_pct: float | None = None
    slippage_loss_pct: float | None = None


class FundingResearchCandidate(BaseModel):
    id: str = ""
    symbol: str
    long_exchange: str
    short_exchange: str
    long_formula_family: str = "unknown"
    short_formula_family: str = "unknown"
    long_funding_pct: float | None
    short_funding_pct: float | None
    long_funding_interval_hours: float | None = None
    short_funding_interval_hours: float | None = None
    long_next_settlement_time: datetime | None = None
    short_next_settlement_time: datetime | None = None
    expected_net_funding_pct: float | None
    expected_basis_change_pct: float
    estimated_cost_pct: float
    risk_buffer_pct: float
    ev_pct: float | None
    adverse_basis_pct: float = 0.0
    conflicted_reward_risk_ratio: float | None = None
    score: float
    decision: FundingResearchDecision
    basis_alignment: BasisAlignment
    basis_diff_pct: float | None
    long_basis_pct: float | None
    short_basis_pct: float | None
    funding_window_hours: float
    next_settlement_time: datetime | None
    minutes_to_settlement: float | None
    funding_source: FormulaConfidence
    primary_opportunity_type: FundingOpportunityType = "PURE_FUNDING_SPREAD"
    opportunity_types: list[FundingOpportunityType] = Field(default_factory=list)
    opportunity_reasons: list[str] = Field(default_factory=list)
    uses_gate: bool = False
    uses_hyperliquid: bool = False
    depth_stats: FundingResearchDepthStats | None = None
    risk_labels: list[str]
    reasons: list[str]


class FundingResearchCandidateSnapshot(BaseModel):
    observed_at: datetime
    candidate: FundingResearchCandidate


class FundingResearchPaperTrade(BaseModel):
    id: str
    status: PaperTradeStatus
    symbol: str
    long_exchange: str
    short_exchange: str
    primary_opportunity_type: FundingOpportunityType = "PURE_FUNDING_SPREAD"
    opportunity_types: list[FundingOpportunityType] = Field(default_factory=list)
    opened_at: datetime
    closed_at: datetime | None = None
    last_observed_at: datetime | None = None
    open_long_basis_pct: float | None = None
    open_short_basis_pct: float | None = None
    open_basis_diff_pct: float | None = None
    close_long_basis_pct: float | None = None
    close_short_basis_pct: float | None = None
    close_basis_diff_pct: float | None = None
    unrealized_basis_change_pct: float | None = None
    unrealized_pnl_pct: float | None = None
    expected_net_funding_pct: float | None = None
    expected_basis_change_pct: float = 0.0
    expected_ev_pct: float | None = None
    score: float = 0.0
    decision: FundingResearchDecision = "WATCH"
    realized_funding_pct: float = 0.0
    realized_basis_change_pct: float = 0.0
    estimated_cost_pct: float = 0.0
    realized_pnl_pct: float | None = None
    max_adverse_ev_pct: float | None = None
    exit_reason: str | None = None
    source_candidate: FundingResearchCandidate


class FundingResearchOpportunityTypeSummary(BaseModel):
    opportunity_type: FundingOpportunityType
    total_trades: int
    closed_trades: int
    winners: int
    losers: int
    win_rate_pct: float | None = None
    total_realized_pnl_pct: float
    average_realized_pnl_pct: float | None = None


class FundingResearchPaperTradeSummary(BaseModel):
    total_trades: int
    open_trades: int
    closed_trades: int
    winners: int
    losers: int
    win_rate_pct: float | None = None
    total_realized_pnl_pct: float
    average_realized_pnl_pct: float | None = None
    average_expected_ev_pct: float | None = None
    average_realized_funding_pct: float | None = None
    average_realized_basis_change_pct: float | None = None
    max_win_pct: float | None = None
    max_loss_pct: float | None = None
    average_score: float | None = None
    by_opportunity_type: list[FundingResearchOpportunityTypeSummary] = Field(default_factory=list)


class FundingResearchLegacyBacktestSummary(BaseModel):
    rows_seen: int
    trades: int
    winners: int
    losers: int
    win_rate_pct: float | None = None
    total_pnl_pct: float
    average_pnl_pct: float | None = None
    average_entry_edge_pct: float | None = None
    max_win_pct: float | None = None
    max_loss_pct: float | None = None
    notes: list[str]
