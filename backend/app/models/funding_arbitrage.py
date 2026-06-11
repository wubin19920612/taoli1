from typing import Literal
from datetime import datetime

from pydantic import BaseModel, Field


FundingArbitrageDecision = Literal["ENTER", "HOLD", "EXIT_SOON", "EXIT_NOW", "BLOCKED"]
FundingSource = Literal["predicted", "fallback_current", "missing"]
AdlRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "BLOCKED"]


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
    adl_risk_score: float
    adl_risk_level: AdlRiskLevel
    decision: FundingArbitrageDecision
    decision_reasons: list[str]
    risk_labels: list[str]
    volume_24h_usdt: float | None = None
    depth_usdt: float | None = None
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
