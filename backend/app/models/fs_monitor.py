from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FsSettings(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    enabled: bool = True
    astro_auto_create: bool = False
    exchanges: list[Literal["bybit", "binance", "okx", "bitget", "gate"]] = Field(
        default_factory=lambda: ["bybit", "binance", "okx", "bitget", "gate"],
        min_length=1,
        max_length=5,
    )
    account_id: str | None = Field(default=None, max_length=100)
    target_usdt: float = Field(default=1000, ge=10, le=100_000)
    holding_hours: float = Field(default=8, ge=0.1, le=72)
    scan_interval_seconds: int = Field(default=60, ge=30, le=600)
    max_candidates: int = Field(default=15, ge=1, le=30)
    min_volume_usdt: float = Field(default=100_000, ge=0, le=1e12)
    min_net_pct: float = Field(default=0.1, ge=0, le=100)
    spot_fee_pct: float = Field(default=0.1, ge=0, le=5)
    future_fee_pct: float = Field(default=0.055, ge=0, le=5)
    execution_buffer_pct: float = Field(default=0.05, ge=0, le=10)
    basis_stress_pct: float = Field(default=0.5, ge=0, le=50)


class FsScenario(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    label: str
    hours: float
    settlements: int
    budget_interest_hours: int
    funding_usdt: float
    borrow_usdt: float
    fees_usdt: float
    round_trip_price_pnl_usdt: float
    buffer_usdt: float
    net_usdt: float
    net_pct: float
    stressed_net_usdt: float


class FsPublicBorrowReference(BaseModel):
    """Public product limits, never account capacity or available inventory."""

    model_config = ConfigDict(allow_inf_nan=False)
    source_url: str
    scope: Literal[
        "bybit_no_vip", "okx_basic", "okx_lv1", "bitget_cross", "gate_isolated", "binance_margin"
    ]
    status: Literal["available", "not_listed", "query_failed"]
    observed_at: datetime | None = None
    reference_limit: float | None = Field(default=None, ge=0)
    minimum_borrow: float | None = Field(default=None, ge=0)
    margin_supported: bool | None = None


class FsCandidate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    symbol: str
    base: str
    exchange: str = "bybit"
    spot_raw_symbol: str | None = None
    future_raw_symbol: str | None = None
    contract_multiplier: float = 1
    quote: str = "USDT"
    multiplier: float = 1
    observed_at: datetime
    valid_until: datetime | None = None
    funding_pct: float | None = None
    interval_hours: float | None = None
    next_funding_at: datetime | None = None
    hourly_borrow_pct: float | None = None
    borrow_rate_source: str = "unknown"
    product_borrowable: bool | None = None
    public_vip_limit: float | None = None
    public_borrow: FsPublicBorrowReference | None = None
    account_max_loan: float | None = None
    available_inventory: float | None = None
    inventory_at: datetime | None = None
    account_state: str = "not_configured"
    quantity: float | None = None
    borrow_required: float | None = None
    capacity_usdt: float | None = None
    spot_volume_usdt: float | None = None
    future_volume_usdt: float | None = None
    prices: dict[str, float] = Field(default_factory=dict)
    source_times: dict[str, datetime] = Field(default_factory=dict)
    fee_source: str = "configured_assumption"
    spot_fee_pct: float | None = None
    future_fee_pct: float | None = None
    identity_state: str = "unverified"
    identity_details: list[dict[str, str | int | None]] = Field(default_factory=list)
    asset_id: str | None = None
    signal_ready: bool = False
    signal_blockers: list[str] = Field(default_factory=list)
    astro_state: str = "not_requested"
    stage: Literal["discovered", "quota_sufficient", "cost_pass", "blocked", "stale"] = "discovered"
    blockers: list[str] = Field(default_factory=list)
    scenarios: list[FsScenario] = Field(default_factory=list)


class FsReport(BaseModel):
    exchange_status: dict[str, str] = Field(default_factory=dict)
    scan_duration_seconds: float | None = None
    next_refresh_at: datetime | None = None
    observed_at: datetime | None = None
    scanning: bool = False
    stale: bool = True
    error: str | None = None
    negative_count: int = 0
    settings: FsSettings = Field(default_factory=FsSettings)
    candidates: list[FsCandidate] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
