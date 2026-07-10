from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


SUPPORTED_PAIR_SPREAD_EXCHANGES: tuple[str, ...] = (
    "binance",
    "okx",
    "bybit",
    "gate",
    "bitget",
    "aster",
    "hyperliquid",
)
PAIR_SPREAD_HOUR_OPTIONS: tuple[int, ...] = (24, 72, 168)


class PairSpreadPriceField(StrEnum):
    MARK_PRICE = "mark_price"
    MID_PRICE = "mid_price"
    INDEX_PRICE = "index_price"
    LAST_PRICE = "last_price"


def normalize_pair_spread_symbol(value: str) -> str:
    normalized = value.strip().upper().replace("_", "-").replace("/", "-")
    if normalized.endswith("-SWAP"):
        normalized = normalized.removesuffix("-SWAP")
    compact = normalized.replace("-", "")
    if not compact:
        raise ValueError("symbol is required")
    return compact if compact.endswith("USDT") else f"{compact}USDT"


class PairSpreadLegQuery(BaseModel):
    exchange: str
    symbol: str

    @field_validator("exchange")
    @classmethod
    def normalize_exchange(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_PAIR_SPREAD_EXCHANGES:
            allowed = ", ".join(SUPPORTED_PAIR_SPREAD_EXCHANGES)
            raise ValueError(f"unsupported exchange: {value}; allowed: {allowed}")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_pair_spread_symbol(value)


class PairSpreadKlinePoint(BaseModel):
    bucket_at: datetime
    close: float = Field(gt=0)


class PairSpreadPoint(BaseModel):
    bucket_at: datetime
    leg1_close: float
    leg2_close: float
    spread_abs: float
    spread_pct: float


class PairSpreadFundingPoint(BaseModel):
    exchange: str
    symbol: str
    funding_time: datetime
    funding_rate_pct: float


class PairSpreadCurrentLeg(BaseModel):
    exchange: str
    symbol: str
    raw_symbol: str
    price: float
    price_field: PairSpreadPriceField
    mark_price: float | None = None
    index_price: float | None = None
    mid_price: float | None = None
    last_price: float | None = None
    funding_rate_pct: float | None = None
    funding_next_rate_pct: float | None = None
    funding_next_time: datetime | None = None
    timestamp: datetime


class PairSpreadCurrentSnapshot(BaseModel):
    observed_at: datetime
    leg1: PairSpreadCurrentLeg
    leg2: PairSpreadCurrentLeg
    spread_abs: float
    spread_pct: float


class PairSpreadValueStats(BaseModel):
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    current: float | None = None


class PairSpreadQueryResult(BaseModel):
    leg1: PairSpreadLegQuery
    leg2: PairSpreadLegQuery
    hours: int
    observed_at: datetime
    point_count: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    spread_abs: PairSpreadValueStats
    spread_pct: PairSpreadValueStats
    current: PairSpreadCurrentSnapshot | None = None
    points: list[PairSpreadPoint]
    funding_history: list[PairSpreadFundingPoint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
