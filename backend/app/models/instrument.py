from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import OpportunityType

INSTRUMENT_LOOKUP_EXCHANGES: tuple[str, ...] = (
    "binance",
    "okx",
    "bybit",
    "gate",
    "bitget",
    "aster",
    "hyperliquid",
    "lighter",
)


class InstrumentExchangeSnapshot(BaseModel):
    exchange: str
    spot: MarketSnapshot | None = None
    future: MarketSnapshot | None = None
    error: str | None = None


class InstrumentMarketCandidate(MarketSnapshot):
    data_status: Literal["live", "stale"]
    age_seconds: float = Field(ge=0)
    stale_after_seconds: int = Field(ge=1)
    error: str | None = None


class InstrumentRouteStatus(BaseModel):
    card_id: str | None = None
    card_name: str
    side: Literal["buy", "sell"]
    route: str
    exchange: str | None = None
    market_type: MarketType
    astro_raw_symbol: str
    canonical_symbol: str
    dex: str | None = None
    counterparty_route: str
    status: Literal["live_market", "market_missing", "route_only"]
    live_data_supported: bool = False
    matched_raw_symbol: str | None = None
    source: str = "Astro card response (route discovery only)"
    reason: str


class InstrumentSpreadComparison(BaseModel):
    id: str
    buy_exchange: str
    buy_market_type: MarketType
    buy_raw_symbol: str
    buy_dex: str | None = None
    buy_price_multiplier: float = Field(default=1.0, gt=0)
    buy_contract_size_multiplier: float | None = Field(default=None, gt=0)
    buy_ask: float = Field(gt=0)
    buy_volume_24h_usdt: float | None = Field(default=None, ge=0)
    buy_funding_rate_pct: float | None = None
    buy_funding_interval_hours: int | None = Field(default=None, gt=0)
    buy_timestamp: datetime
    buy_data_source: str | None = None
    buy_is_estimated: bool = False
    sell_exchange: str
    sell_market_type: MarketType
    sell_raw_symbol: str
    sell_dex: str | None = None
    sell_price_multiplier: float = Field(default=1.0, gt=0)
    sell_contract_size_multiplier: float | None = Field(default=None, gt=0)
    sell_bid: float = Field(gt=0)
    sell_volume_24h_usdt: float | None = Field(default=None, ge=0)
    sell_funding_rate_pct: float | None = None
    sell_funding_interval_hours: int | None = Field(default=None, gt=0)
    sell_timestamp: datetime
    sell_data_source: str | None = None
    sell_is_estimated: bool = False
    price_difference: float
    executable_spread_pct: float
    mid_spread_pct: float
    opportunity_type: OpportunityType | None = None
    astro_supported: bool = False
    astro_blocker: str | None = None


class InstrumentLookupResult(BaseModel):
    query: str
    symbol: str
    base: str
    quote: str = "USDT"
    observed_at: datetime | None = None
    exchange_count: int = Field(default=0, ge=0)
    market_count: int = Field(default=0, ge=0)
    markets: list[InstrumentMarketCandidate] = Field(default_factory=list)
    astro_routes: list[InstrumentRouteStatus] = Field(default_factory=list)
    route_errors: dict[str, str] = Field(default_factory=dict)
    exchanges: list[InstrumentExchangeSnapshot] = Field(default_factory=list)
    spreads: list[InstrumentSpreadComparison] = Field(default_factory=list)
