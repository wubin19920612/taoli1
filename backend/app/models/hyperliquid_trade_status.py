from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class HyperliquidActionState(StrEnum):
    AVAILABLE = "available"
    BLOCKED = "blocked"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


class HyperliquidTradeActionStatus(BaseModel):
    state: HyperliquidActionState
    reason_code: str
    reason: str
    executable_price: float | None = None
    depth_1pct_usdt: float | None = Field(default=None, ge=0)


class HyperliquidMarketTradeStatus(BaseModel):
    symbol: str
    dex: str
    raw_symbol: str
    observed_at: datetime
    at_open_interest_cap: bool | None = None
    is_delisted: bool = False
    only_isolated: bool = False
    margin_mode: str | None = None
    max_leverage: int | None = None
    size_decimals: int | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    bid_depth_01pct_usdt: float | None = Field(default=None, ge=0)
    ask_depth_01pct_usdt: float | None = Field(default=None, ge=0)
    bid_depth_1pct_usdt: float | None = Field(default=None, ge=0)
    ask_depth_1pct_usdt: float | None = Field(default=None, ge=0)
    mark_price: float | None = None
    oracle_price: float | None = None
    open_interest: float | None = None
    open_interest_usdt: float | None = None
    volume_24h_usdt: float | None = None
    funding_rate_pct: float | None = None
    funding_interval_hours: int = 1
    market_multiplier: float = 1.0
    fees_included: bool = False
    fee_note: str = "手续费未计入；实际费率取决于账户等级和订单类型"
    buy_open: HyperliquidTradeActionStatus
    sell_open: HyperliquidTradeActionStatus
    buy_reduce_only: HyperliquidTradeActionStatus
    sell_reduce_only: HyperliquidTradeActionStatus


class HyperliquidTradeStatusResult(BaseModel):
    query: str
    observed_at: datetime
    source: str = "Hyperliquid public info API"
    markets: list[HyperliquidMarketTradeStatus] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class HyperliquidTradeStatusWatchCreate(BaseModel):
    symbol: str
    dex: str | None = None
    raw_symbol: str
    monitor_buy: bool = True
    monitor_sell: bool = True


class HyperliquidTradeStatusWatch(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    dex: str = "main"
    raw_symbol: str
    monitor_buy: bool = True
    monitor_sell: bool = True
    enabled: bool = True
    last_buy_state: HyperliquidActionState | None = None
    last_sell_state: HyperliquidActionState | None = None
    last_checked_at: datetime | None = None
    last_notified_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class HyperliquidTradeStatusWatchEvent(BaseModel):
    watch_id: str
    recovered_sides: list[Literal["buy", "sell"]]
    market: HyperliquidMarketTradeStatus
