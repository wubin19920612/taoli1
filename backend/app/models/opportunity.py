from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

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
    sell_exchange: str
    sell_market_type: MarketType
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
