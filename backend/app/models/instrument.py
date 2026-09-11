from datetime import datetime

from pydantic import BaseModel, Field

from app.models.market import MarketSnapshot


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


class InstrumentLookupResult(BaseModel):
    query: str
    symbol: str
    base: str
    quote: str = "USDT"
    observed_at: datetime | None = None
    exchange_count: int = Field(default=0, ge=0)
    market_count: int = Field(default=0, ge=0)
    exchanges: list[InstrumentExchangeSnapshot] = Field(default_factory=list)
