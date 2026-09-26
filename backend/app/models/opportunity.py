from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.market import MarketType


class OpportunityType(StrEnum):
    SF = "SF"
    FF = "FF"
    SS = "SS"


class Opportunity(BaseModel):
    id: str
    type: OpportunityType
    symbol: str
    buy_exchange: str
    buy_market_type: MarketType
    buy_raw_symbol: str | None = None
    buy_dex: str | None = None
    buy_price_multiplier: float = 1.0
    buy_contract_size_multiplier: float | None = None
    buy_timestamp: datetime | None = None
    buy_data_source: str | None = None
    buy_is_estimated: bool = False
    buy_estimated_fields: list[str] = Field(default_factory=list)
    sell_exchange: str
    sell_market_type: MarketType
    sell_raw_symbol: str | None = None
    sell_dex: str | None = None
    sell_price_multiplier: float = 1.0
    sell_contract_size_multiplier: float | None = None
    sell_timestamp: datetime | None = None
    sell_data_source: str | None = None
    sell_is_estimated: bool = False
    sell_estimated_fields: list[str] = Field(default_factory=list)
    buy_fee_pct: float = 0.0
    sell_fee_pct: float = 0.0
    safety_slippage_pct: float = 0.0
    fees_are_estimated: bool = True
    open_spread_pct: float
    close_spread_pct: float
    fee_adjusted_open_pct: float
    spread_width_pct: float
    buy_bid: float
    buy_ask: float
    sell_bid: float
    sell_ask: float
    buy_bid_depth_usdt: float | None = None
    buy_ask_depth_usdt: float | None = None
    sell_bid_depth_usdt: float | None = None
    sell_ask_depth_usdt: float | None = None
    min_open_depth_usdt: float | None = None
    buy_volume_24h_usdt: float | None
    sell_volume_24h_usdt: float | None
    funding_rate_buy_pct: float | None = None
    funding_rate_sell_pct: float | None = None
    funding_next_rate_buy_pct: float | None = None
    funding_next_rate_sell_pct: float | None = None
    funding_next_time_buy: datetime | None = None
    funding_next_time_sell: datetime | None = None
    net_funding_pct: float | None = None
    net_funding_next_pct: float | None = None
    buy_funding_interval_hours: int | None = None
    sell_funding_interval_hours: int | None = None
    net_funding_hourly_pct: float | None = None
    net_funding_daily_pct: float | None = None
    net_funding_next_hourly_pct: float | None = None
    net_funding_next_daily_pct: float | None = None
    mark_index_diff_buy_pct: float | None = None
    mark_index_diff_sell_pct: float | None = None
    risk_labels: list[str]
    last_seen_at: datetime
