from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.market import MarketType


class TradeAvailabilityState(StrEnum):
    AVAILABLE = "available"
    BLOCKED = "blocked"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class TradeEvidenceScope(StrEnum):
    PUBLIC_MARKET = "public_market"
    ACCOUNT = "account"
    ORDER_ERROR = "order_error"
    PLATFORM_CAPABILITY = "platform_capability"
    NONE = "none"


class TradeEvidenceState(StrEnum):
    CONFIRMED = "confirmed"
    NOT_CHECKED = "not_checked"
    NOT_PROVIDED = "not_provided"
    ERROR = "error"


class TransferAvailabilityState(StrEnum):
    ENABLED = "enabled"
    PARTIAL = "partial"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class SpotTransferNetworkStatus(BaseModel):
    network: str
    deposit_enabled: bool | None = None
    withdraw_enabled: bool | None = None


class SpotTransferAvailability(BaseModel):
    asset: str
    deposit_state: TransferAvailabilityState
    withdraw_state: TransferAvailabilityState
    all_enabled: bool | None = None
    publicly_queryable: bool = True
    source: str
    observed_at: datetime | None = None
    networks: list[SpotTransferNetworkStatus] = Field(default_factory=list)
    note: str = ""
    error: str | None = None


class ContractIndexComponent(BaseModel):
    source_exchange: str
    market_type: MarketType | None = None
    raw_symbol: str
    weight: float | None = None
    price: float | None = None


class ContractIndexComposition(BaseModel):
    exchange: str
    market_type: MarketType
    symbol: str
    raw_symbol: str
    dex: str | None = None
    status: Literal["available", "not_returned", "error"]
    source: str
    index_price: float | None = None
    observed_at: datetime | None = None
    weight_total: float | None = None
    components: list[ContractIndexComponent] = Field(default_factory=list)
    note: str = ""
    error: str | None = None


class TradeActionStatus(BaseModel):
    state: TradeAvailabilityState
    reason_code: str
    reason: str
    scope: TradeEvidenceScope = TradeEvidenceScope.PUBLIC_MARKET
    executable_price: float | None = None
    depth_1pct_usdt: float | None = Field(default=None, ge=0)


class TradeDiagnosticEvidence(BaseModel):
    scope: TradeEvidenceScope
    state: TradeEvidenceState
    reason_code: str
    message: str
    source: str | None = None
    raw_error: str | None = None


class MarketTradeAvailability(BaseModel):
    exchange: str
    market_type: MarketType
    symbol: str
    raw_symbol: str
    dex: str | None = None
    coverage_tier: Literal["core", "existing", "evaluated"]
    observed_at: datetime
    market_data_updated_at: datetime
    orderbook_updated_at: datetime | None = None
    orderbook_source: str
    public_status_code: str
    public_status_source: str
    public_restrictions: list[str] = Field(default_factory=list)
    diagnostics: list[TradeDiagnosticEvidence] = Field(default_factory=list)
    best_bid: float | None = None
    best_ask: float | None = None
    bid_depth_01pct_usdt: float | None = Field(default=None, ge=0)
    ask_depth_01pct_usdt: float | None = Field(default=None, ge=0)
    bid_depth_1pct_usdt: float | None = Field(default=None, ge=0)
    ask_depth_1pct_usdt: float | None = Field(default=None, ge=0)
    volume_24h_usdt: float | None = Field(default=None, ge=0)
    funding_rate_pct: float | None = None
    funding_next_rate_pct: float | None = None
    funding_interval_hours: int | None = Field(default=None, gt=0)
    funding_next_time: datetime | None = None
    mark_price: float | None = None
    index_price: float | None = None
    maker_fee_pct: float | None = None
    taker_fee_pct: float | None = None
    fees_included: bool = False
    fee_note: str = "手续费未计入状态与盘口价格；实际费率取决于账户等级和订单类型"
    market_multiplier: float = Field(default=1.0, gt=0)
    contract_size_multiplier: float = Field(default=1.0, gt=0)
    spot_transfer: SpotTransferAvailability | None = None
    buy_open: TradeActionStatus
    sell_open: TradeActionStatus
    buy_reduce_only: TradeActionStatus
    sell_reduce_only: TradeActionStatus


class TradeAvailabilityCoverage(BaseModel):
    exchange: str
    tier: Literal["core", "existing", "evaluated"]
    public_status_supported: bool
    orderbook_supported: bool
    note: str


class TradeAvailabilityResult(BaseModel):
    query: str
    observed_at: datetime
    source: str = "各交易所公开市场元数据、实时订单簿与可匿名取得的现货充提状态；未发送订单"
    markets: list[MarketTradeAvailability] = Field(default_factory=list)
    index_compositions: list[ContractIndexComposition] = Field(default_factory=list)
    coverage: list[TradeAvailabilityCoverage] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class TradeAvailabilityWatchCreate(BaseModel):
    symbol: str
    exchange: str
    market_type: MarketType
    raw_symbol: str
    dex: str | None = None
    monitor_buy: bool = True
    monitor_sell: bool = True


class TradeAvailabilityWatch(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    exchange: str
    market_type: MarketType
    raw_symbol: str
    dex: str | None = None
    monitor_buy: bool = True
    monitor_sell: bool = True
    enabled: bool = True
    last_buy_state: TradeAvailabilityState | None = None
    last_sell_state: TradeAvailabilityState | None = None
    last_buy_reduce_only_state: TradeAvailabilityState | None = None
    last_sell_reduce_only_state: TradeAvailabilityState | None = None
    last_checked_at: datetime | None = None
    last_notified_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class TradeAvailabilityWatchEvent(BaseModel):
    watch_id: str
    recovered_sides: list[Literal["buy", "sell"]]
    recovered_actions: list[
        Literal["buy_open", "sell_open", "buy_reduce_only", "sell_reduce_only"]
    ] = Field(default_factory=list)
    market: MarketTradeAvailability
    spot_transfer: SpotTransferAvailability | None = None
