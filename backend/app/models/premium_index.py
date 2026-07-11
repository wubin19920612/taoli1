from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.pair_spread import normalize_pair_spread_symbol


SUPPORTED_PREMIUM_INDEX_EXCHANGES: tuple[str, ...] = (
    "binance",
    "okx",
    "bybit",
    "gate",
    "bitget",
    "aster",
    "hyperliquid",
)
PREMIUM_INDEX_MIN_HOURS = 1
PREMIUM_INDEX_MAX_HOURS = 720
PREMIUM_INDEX_INTERVAL_OPTIONS: tuple[int, ...] = (1, 5, 15)


class PremiumIndexMarketQuery(BaseModel):
    exchange: str
    symbol: str

    @field_validator("exchange")
    @classmethod
    def normalize_exchange(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_PREMIUM_INDEX_EXCHANGES:
            allowed = ", ".join(SUPPORTED_PREMIUM_INDEX_EXCHANGES)
            raise ValueError(f"unsupported exchange: {value}; allowed: {allowed}")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_pair_spread_symbol(value)


class PremiumIndexPoint(BaseModel):
    bucket_at: datetime
    premium_pct: float
    mark_price: float | None = Field(default=None, gt=0)
    index_price: float | None = Field(default=None, gt=0)
    source: str


class PremiumIndexCurrentSnapshot(BaseModel):
    observed_at: datetime
    exchange: str
    symbol: str
    raw_symbol: str
    mark_price: float | None = Field(default=None, gt=0)
    index_price: float | None = Field(default=None, gt=0)
    mid_price: float | None = Field(default=None, gt=0)
    last_price: float | None = Field(default=None, gt=0)
    premium_pct: float | None = None
    mid_premium_pct: float | None = None
    funding_rate_pct: float | None = None
    funding_next_rate_pct: float | None = None
    funding_next_time: datetime | None = None
    source: str


class PremiumIndexValueStats(BaseModel):
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    current: float | None = None


class PremiumIndexQueryResult(BaseModel):
    exchange: str
    symbol: str
    hours: int
    interval_minutes: int = 1
    observed_at: datetime
    point_count: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    premium_pct: PremiumIndexValueStats
    current: PremiumIndexCurrentSnapshot | None = None
    points: list[PremiumIndexPoint]
    warnings: list[str] = Field(default_factory=list)
