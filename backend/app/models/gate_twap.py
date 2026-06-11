from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


GateTwapSide = Literal["sell", "buy"]
GateTwapSliceMode = Literal["initial", "remaining"]
GateTwapActionMode = Literal["ACK", "RESULT", "FULL"]
GateTwapJobState = Literal["queued", "running", "completed", "failed", "cancelled"]


class GateTwapRequest(BaseModel):
    contract: str = Field(default="SKHYNIX_USDT", min_length=3)
    settle: str = Field(default="usdt", min_length=2)
    side: GateTwapSide = "sell"
    start_at: datetime | None = None
    interval_seconds: float = Field(default=10.0, gt=0, le=3600)
    duration_seconds: float = Field(default=1000.0, gt=0, le=86_400)
    percent: float = Field(default=1.0, gt=0, le=100)
    slice_mode: GateTwapSliceMode = "initial"
    initial_size: float | None = Field(default=None, gt=0)
    last_order_all: bool = True
    slip_ratio: float | None = Field(default=None, ge=0, le=0.2)
    client_prefix: str = Field(default="t-twap", min_length=1, max_length=20)
    action_mode: GateTwapActionMode = "ACK"


class GateTwapRunRequest(GateTwapRequest):
    live: bool = False
    confirm_live: bool = False


class GateTwapContractRules(BaseModel):
    order_size_min: float = 1
    order_size_step: float = 1
    enable_decimal: bool = False
    market_order_slip_ratio: float | None = None
    market_order_size_max: float | None = None
    status: str | None = None


class GateTwapPlanSlice(BaseModel):
    index: int
    scheduled_at: datetime
    raw_size: float
    order_size: float
    signed_order_size: float
    remaining_after: float
    skipped_reason: str | None = None


class GateTwapPlan(BaseModel):
    request: GateTwapRequest
    contract: str
    settle: str
    side: GateTwapSide
    order_count: int
    initial_size: float | None
    signed_position_size: float | None = None
    has_credentials: bool
    rules: GateTwapContractRules
    total_planned_size: float
    slices: list[GateTwapPlanSlice]
    warnings: list[str] = Field(default_factory=list)


class GateTickerBook(BaseModel):
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    mid: float | None = None
    last: float | None = None
    volume_24h_usdt: float | None = None


class GateTwapMarketSnapshot(BaseModel):
    contract: str
    spot_pair: str
    observed_at: datetime
    spot_available: bool
    spot: GateTickerBook | None = None
    future: GateTickerBook | None = None
    mark_price: float | None = None
    index_price: float | None = None
    mark_index_premium_pct: float | None = None
    future_index_premium_pct: float | None = None
    future_spot_premium_pct: float | None = None
    funding_rate_pct: float | None = None
    funding_next_rate_pct: float | None = None
    funding_interval_hours: float | None = None
    funding_next_time: datetime | None = None
    contract_status: str | None = None
    order_size_min: float | None = None
    order_size_step: float | None = None
    market_order_slip_ratio: float | None = None


class GateTwapJobEvent(BaseModel):
    at: datetime
    level: Literal["info", "warning", "error"] = "info"
    message: str
    order: dict | None = None
    response: dict | None = None


class GateTwapJobStatus(BaseModel):
    job_id: str
    state: GateTwapJobState
    live: bool
    request: GateTwapRequest
    plan: GateTwapPlan | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    completed_orders: int = 0
    skipped_orders: int = 0
    total_order_size: float = 0
    last_error: str | None = None
    events: list[GateTwapJobEvent] = Field(default_factory=list)
