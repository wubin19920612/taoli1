from datetime import datetime

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
)


class InstrumentExchangeSnapshot(BaseModel):
    exchange: str
    spot: MarketSnapshot | None = None
    future: MarketSnapshot | None = None
    error: str | None = None


class InstrumentSpreadComparison(BaseModel):
    id: str
    buy_exchange: str
    buy_market_type: MarketType
    buy_ask: float = Field(gt=0)
    sell_exchange: str
    sell_market_type: MarketType
    sell_bid: float = Field(gt=0)
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
    exchanges: list[InstrumentExchangeSnapshot] = Field(default_factory=list)
    spreads: list[InstrumentSpreadComparison] = Field(default_factory=list)
