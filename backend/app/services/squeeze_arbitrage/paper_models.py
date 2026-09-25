from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

D = Decimal
PAPER_RULE_VERSION = "squeeze-paper-s3-v1"


class PaperSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_version: str = PAPER_RULE_VERSION
    initial_balance_per_exchange: Decimal = D("10000")
    entry_notional: Decimal = D("100")
    delay_ms: int = 500
    limit_buffer_rate: Decimal = D("0.001")
    capture_ratio: Decimal = D("0.65")
    no_improvement_seconds: int = 1800
    routine_exit_seconds: int = 7200
    max_holding_seconds: int = 21600
    recovery_seconds: int = 30
    max_recovery_attempts: int = 6
    max_simultaneous_positions: int = 3
    max_loss_fraction: Decimal = D("0.0025")


class PaperRun(BaseModel):
    started_at: datetime
    settings: PaperSettings
    last_processed_at: datetime | None = None
    last_event_at: datetime
    last_event_id: str = ""
    last_success_at: datetime | None = None
    last_error: str | None = None


class PaperAccount(BaseModel):
    exchange: str
    initial_balance: Decimal
    cash_balance: Decimal
    reserved_margin: Decimal = D(0)
    fees_paid: Decimal = D(0)
    funding_cashflow: Decimal = D(0)
    price_pnl: Decimal = D(0)
    updated_at: datetime

    @property
    def free_balance(self) -> Decimal:
        return self.cash_balance - self.reserved_margin


class PaperOrder(BaseModel):
    id: str
    trade_id: str
    role: Literal["entry", "recovery", "exit"]
    leg_key: str
    side: Literal["buy", "sell"]
    target_quantity: Decimal
    limit_price: Decimal
    submitted_at: datetime
    eligible_at: datetime
    submitted_sequence: int | None = None
    status: Literal["pending", "filled", "partial", "unfilled"] = "pending"
    filled_quantity: Decimal = D(0)
    completed_at: datetime | None = None


class PaperFill(BaseModel):
    id: str
    order_id: str
    trade_id: str
    leg_key: str
    role: Literal["entry", "recovery", "exit"]
    side: Literal["buy", "sell"]
    quantity: Decimal
    quote_notional: Decimal
    unit_price: Decimal
    fee: Decimal
    filled_at: datetime
    source_at: datetime | None
    received_at: datetime
    sequence: int | None
    book_levels: list[tuple[str, str]]


class PaperCashflow(BaseModel):
    id: str
    trade_id: str
    leg_key: str
    exchange: str
    kind: Literal["fee", "price_pnl", "funding", "borrow"]
    amount: Decimal
    occurred_at: datetime
    source: str
    rate: Decimal | None = None
    mark_price: Decimal | None = None
    mark_kind: Literal["actual", "one_minute_proxy", "not_applicable"] = "not_applicable"


class PaperTrade(BaseModel):
    id: str
    event_id: str
    route_id: str
    asset_id: str
    quote_asset: str
    expensive_key: str
    cheap_key: str
    status: Literal[
        "pending_entry", "recovering", "open", "pending_exit", "unresolved",
        "closed", "unfilled", "blocked",
    ]
    signal_at: datetime
    eligible_at: datetime
    target_quantity: Decimal
    signal_open_difference: Decimal
    target_residual: Decimal
    target_close_difference: Decimal
    signal_expensive_sequence: int | None
    signal_cheap_sequence: int | None
    signal_capacities: list[dict[str, Any]] = Field(default_factory=list)
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    last_observed_at: datetime | None = None
    expensive_entry_price: Decimal | None = None
    cheap_entry_price: Decimal | None = None
    expensive_open_quantity: Decimal = D(0)
    cheap_open_quantity: Decimal = D(0)
    entry_fees: Decimal = D(0)
    exit_fees: Decimal = D(0)
    funding_total: Decimal = D(0)
    borrow_total: Decimal = D(0)
    price_pnl: Decimal = D(0)
    max_adverse_net: Decimal | None = None
    best_close_difference: Decimal | None = None
    worst_close_difference: Decimal | None = None
    minimum_expensive_free_balance: Decimal | None = None
    minimum_cheap_free_balance: Decimal | None = None
    next_expensive_funding_at: datetime | None = None
    next_cheap_funding_at: datetime | None = None
    expensive_funding_interval_hours: int | None = None
    cheap_funding_interval_hours: int | None = None
    funding_retry_at: datetime | None = None
    funding_gap_at: datetime | None = None
    recovery_started_at: datetime | None = None
    recovery_attempts: int = 0
    exit_reason: str | None = None
    last_error: str | None = None
    risk_labels: list[str] = Field(default_factory=lambda: ["collateral_model_incomplete"])
    orders: list[PaperOrder] = Field(default_factory=list)
    fills: list[PaperFill] = Field(default_factory=list)
    cashflows: list[PaperCashflow] = Field(default_factory=list)

    @property
    def net_realized(self) -> Decimal:
        return self.price_pnl - self.entry_fees - self.exit_fees + self.funding_total - self.borrow_total


class FundingSettlement(BaseModel):
    exchange: str
    market_key: str
    settled_at: datetime
    rate: Decimal
    mark_price: Decimal
    mark_kind: Literal["actual", "one_minute_proxy"]
    source: str
