from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MarketType(StrEnum):
    SPOT = "spot"
    FUTURE = "future"


class MarketSnapshot(BaseModel):
    symbol: str
    base: str
    quote: str = "USDT"
    exchange: str
    market_type: MarketType
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    bid_size: float | None = None
    ask_size: float | None = None
    volume_24h_usdt: float | None = None
    funding_rate_pct: float | None = None
    funding_interval_hours: int | None = None
    funding_next_time: datetime | None = None
    mark_price: float | None = None
    index_price: float | None = None
    timestamp: datetime
    raw_symbol: str
