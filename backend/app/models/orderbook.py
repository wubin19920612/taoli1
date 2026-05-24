from datetime import datetime

from pydantic import BaseModel, Field

from app.models.market import MarketType


class OrderBookLevel(BaseModel):
    price: float = Field(gt=0)
    size: float = Field(gt=0)


class OrderBookSnapshot(BaseModel):
    exchange: str
    market_type: MarketType
    symbol: str
    raw_symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime


class DepthValidationResult(BaseModel):
    passed: bool
    target_notional_usdt: float
    buy_filled_usdt: float
    sell_filled_usdt: float
    buy_vwap: float | None
    sell_vwap: float | None
    quoted_open_pct: float
    executable_open_pct: float | None
    effective_executable_edge_pct: float | None
    slippage_loss_pct: float | None
    blockers: list[str]
    warnings: list[str]
