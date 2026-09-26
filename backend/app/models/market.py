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
    funding_next_rate_pct: float | None = None
    funding_interval_hours: int | None = None
    funding_next_time: datetime | None = None
    mark_price: float | None = None
    index_price: float | None = None
    timestamp: datetime
    raw_symbol: str
    dex: str | None = None
    contract_size_multiplier: float | None = Field(default=None, gt=0)
    data_source: str | None = None
    upstream_timestamp: datetime | None = None
    is_estimated: bool = False
    estimated_fields: list[str] = Field(default_factory=list)
    symbol_alias_original_symbol: str | None = None
    symbol_alias_price_multiplier: float = Field(default=1.0, gt=0)
