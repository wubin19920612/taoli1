from datetime import datetime

from pydantic import BaseModel

from app.models.market import MarketType
from app.models.opportunity import OpportunityType


class OpportunityHistoryRow(BaseModel):
    observed_at: datetime
    opportunity_id: str
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
    buy_volume_24h_usdt: float | None = None
    sell_volume_24h_usdt: float | None = None
    risk_labels: list[str] = []


class OpportunitySpreadStats(BaseModel):
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    p05: float | None = None
    p95: float | None = None
    current: float | None = None
    z_score: float | None = None


class OpportunityHistoryPoint(BaseModel):
    observed_at: datetime
    open_spread_pct: float
    close_spread_pct: float
    fee_adjusted_open_pct: float
    net_funding_pct: float | None = None
    net_funding_next_pct: float | None = None


class OpportunityHistoryStats(BaseModel):
    symbol: str | None = None
    opportunity_id: str | None = None
    type: OpportunityType | None = None
    count: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    latest: OpportunityHistoryRow | None = None
    open_spread_pct: OpportunitySpreadStats
    close_spread_pct: OpportunitySpreadStats
    fee_adjusted_open_pct: OpportunitySpreadStats
    net_funding_pct: OpportunitySpreadStats
    net_funding_next_pct: OpportunitySpreadStats
    points: list[OpportunityHistoryPoint]
