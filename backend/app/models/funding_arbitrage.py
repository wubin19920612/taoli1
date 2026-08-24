from typing import Literal
from datetime import datetime

from pydantic import BaseModel, Field


FundingArbitrageDecision = Literal["ENTER", "HOLD", "EXIT_SOON", "EXIT_NOW", "BLOCKED"]
FundingSource = Literal["predicted", "fallback_current", "missing"]
AdlRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "BLOCKED"]
FundingOpportunityType = Literal[
    "BASIS_AND_FUNDING_ALIGNED",
    "STRONG_FUNDING_NEAR_SETTLEMENT",
    "INTERVAL_MISMATCH",
    "FORMULA_DIVERGENCE",
    "BASIS_CARRY_CONFLICTED",
    "BASIS_MEAN_REVERSION",
    "PURE_FUNDING_SPREAD",
]


class FundingArbitrageSettings(BaseModel):
    enabled: bool = False
    max_candidates: int = Field(default=50, ge=1, le=500)
    min_entry_edge_pct: float = 0.03
    min_hold_edge_pct: float = 0.0
    min_exit_edge_pct: float = 0.0
    min_funding_edge_pct: float = 0.02
    min_volume_24h_usdt: float = Field(default=1_000_000, ge=0)
    max_mark_index_deviation_pct: float = Field(default=1.0, ge=0)
    max_basis_width_pct: float = Field(default=3.0, ge=0)
    slippage_buffer_pct: float = Field(default=0.05, ge=0)
    basis_risk_weight: float = Field(default=1.0, ge=0)
    confidence_penalty_pct: float = Field(default=0.02, ge=0)
    min_minutes_to_settlement: int = Field(default=5, ge=0)
    max_minutes_to_settlement: int = Field(default=90, ge=1)
    adl_block_score: float = Field(default=80, ge=0)
    leverage: int = Field(default=1, ge=1)
    notional_per_symbol_usdt: float = Field(default=100, gt=0)
    prefer_hyperliquid: bool = True
    strong_funding_pct: float = Field(default=0.6, ge=0)
    near_settlement_minutes: float = Field(default=45, ge=0)
    small_basis_threshold_pct: float = Field(default=0.25, ge=0)
    interval_mismatch_min_hours: float = Field(default=1, ge=0)
    formula_divergence_min_funding_pct: float = Field(default=0.25, ge=0)
    conflicted_basis_min_check_pct: float = Field(default=0.3, ge=0)
    min_conflicted_reward_risk_ratio: float = Field(default=1.0, ge=0)


class FundingArbitrageCandidate(BaseModel):
    id: str
    symbol: str
    type: Literal["SF", "FF"]
    long_exchange: str
    long_market_type: str
    short_exchange: str
    short_market_type: str
    funding_source: FundingSource
    long_current_funding_pct: float | None = None
    short_current_funding_pct: float | None = None
    long_next_funding_pct: float | None = None
    short_next_funding_pct: float | None = None
    current_funding_edge_pct: float | None = None
    next_funding_edge_pct: float | None = None
    long_funding_interval_hours: float | None = None
    short_funding_interval_hours: float | None = None
    funding_comparison_interval_hours: float | None = None
    long_next_settlement_time: datetime | None = None
    short_next_settlement_time: datetime | None = None
    next_settlement_time: datetime | None = None
    minutes_to_settlement: float | None = None
    entry_basis_pct: float
    exit_basis_pct: float
    basis_width_pct: float
    basis_risk_penalty_pct: float
    estimated_open_cost_pct: float
    estimated_close_cost_pct: float
    slippage_buffer_pct: float
    confidence_penalty_pct: float
    adl_risk_penalty_pct: float
    expected_cycle_pnl_pct: float
    adverse_entry_basis_pct: float
    conflicted_reward_risk_ratio: float | None = None
    adl_risk_score: float
    adl_risk_level: AdlRiskLevel
    decision: FundingArbitrageDecision
    decision_reasons: list[str]
    risk_labels: list[str]
    primary_opportunity_type: FundingOpportunityType = "PURE_FUNDING_SPREAD"
    opportunity_types: list[FundingOpportunityType] = Field(default_factory=list)
    opportunity_reasons: list[str] = Field(default_factory=list)
    volume_24h_usdt: float | None = None
    depth_usdt: float | None = None
    uses_gate: bool = False
    uses_hyperliquid: bool


class FundingArbitragePreview(BaseModel):
    settings: FundingArbitrageSettings
    total_pairs_evaluated: int
    displayed_candidates: int
    blocked_missing_funding: int
    blocked_liquidity: int
    blocked_adl_risk: int
    blocked_expected_pnl: int
    enter_count: int
    hold_count: int
    exit_count: int
    blocked_count: int
    candidates: list[FundingArbitrageCandidate]
