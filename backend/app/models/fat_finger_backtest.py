from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.pair_spread import normalize_pair_spread_symbol


FatFingerMarketMode = Literal["SF", "FF"]
FatFingerMakerSide = Literal["buy", "sell"]
FatFingerExitReason = Literal["target", "timeout"]


class FatFingerBacktestRequest(BaseModel):
    symbol: str
    market_mode: FatFingerMarketMode = "SF"
    hours: float = Field(default=6.0, gt=0, le=168)
    sample_limit: int = Field(
        default=150_000,
        ge=1_000,
        le=300_000,
        description="回测读取的最大原始样本数，超出时只回放最近部分。",
    )
    entry_spread_pct: float = Field(
        default=1.0,
        gt=0,
        le=100,
        description="第一档 maker 挂单相对对冲腿的目标价差。",
    )
    ladder_levels: int = Field(default=3, ge=1, le=10)
    ladder_step_pct: float = Field(default=0.5, ge=0, le=50)
    order_notional_usdt: float = Field(default=100.0, gt=0, le=1_000_000)
    maker_fill_assumption_pct: float = Field(
        default=25.0,
        gt=0,
        le=100,
        description="只有盘口时无法得知排队位置，此比例是触价后假设成交的订单比例。",
    )
    maker_fee_pct: float = Field(default=0.02, ge=0, le=10)
    taker_fee_pct: float = Field(default=0.06, ge=0, le=10)
    taker_slippage_pct: float = Field(default=0.05, ge=0, le=10)
    hedge_delay_seconds: float = Field(default=1.0, ge=0, le=60)
    order_expiry_seconds: float = Field(default=30.0, gt=0, le=3600)
    take_profit_pct: float = Field(default=0.15, ge=0, le=100)
    max_hold_seconds: float = Field(default=120.0, gt=0, le=86_400)
    min_hedge_depth_usdt: float = Field(default=100.0, ge=0, le=1_000_000)
    max_quote_age_seconds: float = Field(default=2.0, gt=0, le=60)
    require_known_hedge_depth: bool = True
    cooldown_seconds: float = Field(default=10.0, ge=0, le=3600)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_pair_spread_symbol(value)


class FatFingerBacktestTrade(BaseModel):
    id: str
    symbol: str
    market_mode: FatFingerMarketMode
    maker_exchange: str
    maker_market_type: Literal["spot", "future"]
    hedge_exchange: str
    hedge_market_type: Literal["spot", "future"]
    maker_side: FatFingerMakerSide
    tier: int
    entry_target_spread_pct: float
    order_placed_at: datetime
    maker_filled_at: datetime
    hedge_filled_at: datetime
    closed_at: datetime
    exit_reason: FatFingerExitReason
    maker_entry_price: float
    hedge_entry_price: float
    maker_exit_price: float
    hedge_exit_price: float
    notional_usdt: float
    hedge_depth_usdt: float | None = None
    entry_hedge_edge_pct: float
    gross_pnl_usdt: float
    net_pnl_usdt: float
    net_pnl_pct: float
    max_favorable_pnl_pct: float
    max_adverse_pnl_pct: float
    hedge_delay_seconds: float
    hold_seconds: float


class FatFingerBacktestRouteSummary(BaseModel):
    maker_exchange: str
    maker_market_type: Literal["spot", "future"]
    hedge_exchange: str
    hedge_market_type: Literal["spot", "future"]
    maker_side: FatFingerMakerSide
    touch_count: int = 0
    hedge_count: int = 0
    unhedged_count: int = 0
    closed_trade_count: int = 0
    win_count: int = 0
    total_notional_usdt: float = 0
    total_net_pnl_usdt: float = 0
    average_net_pnl_pct: float | None = None
    median_net_pnl_pct: float | None = None
    worst_net_pnl_pct: float | None = None
    average_hold_seconds: float | None = None


class FatFingerBacktestResult(BaseModel):
    request: FatFingerBacktestRequest
    start_at: datetime
    end_at: datetime
    raw_sample_count: int
    samples_truncated: bool = False
    frame_count: int
    exchange_count: int
    order_placed_count: int
    order_expired_count: int
    order_skipped_depth_count: int
    exit_skipped_depth_count: int
    quote_touch_count: int
    hedge_completed_count: int
    unhedged_touch_count: int
    open_position_count: int
    closed_trade_count: int
    target_exit_count: int
    timeout_exit_count: int
    win_count: int
    loss_count: int
    win_rate_pct: float | None = None
    hedge_success_rate_pct: float | None = None
    total_notional_usdt: float = 0
    total_net_pnl_usdt: float = 0
    average_net_pnl_pct: float | None = None
    median_net_pnl_pct: float | None = None
    worst_net_pnl_pct: float | None = None
    average_hold_seconds: float | None = None
    average_hedge_delay_seconds: float | None = None
    route_summaries: list[FatFingerBacktestRouteSummary] = Field(default_factory=list)
    trades: list[FatFingerBacktestTrade] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
