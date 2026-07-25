from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from app.models.market import MarketType


SUPPORTED_PAIR_SPREAD_EXCHANGES: tuple[str, ...] = (
    "binance",
    "binance_alpha",
    "okx",
    "bybit",
    "gate",
    "bitget",
    "aster",
    "hyperliquid",
)
PAIR_SPREAD_MIN_HOURS = 1
PAIR_SPREAD_MAX_HOURS = 720
PAIR_SPREAD_INTERVAL_OPTIONS: tuple[int, ...] = (1, 5, 15)


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


def normalize_binance_alpha_symbol(value: str) -> str:
    normalized = value.strip().upper().replace("/", "").replace("-", "_")
    if normalized.endswith("_USDT"):
        normalized = f"{normalized.removesuffix('_USDT')}USDT"
    if normalized.isdigit():
        normalized = f"ALPHA_{normalized}"
    if normalized.startswith("ALPHA") and not normalized.startswith("ALPHA_"):
        normalized = f"ALPHA_{normalized.removeprefix('ALPHA')}"
    if not normalized:
        raise ValueError("symbol is required")
    return normalized if normalized.endswith("USDT") else f"{normalized}USDT"


class PairSpreadLegQuery(BaseModel):
    exchange: str
    symbol: str
    market_type: MarketType = MarketType.FUTURE

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
    def normalize_symbol(cls, value: str, info: ValidationInfo) -> str:
        exchange = info.data.get("exchange") if isinstance(info.data, dict) else None
        if exchange == "binance_alpha":
            return normalize_binance_alpha_symbol(value)
        return normalize_pair_spread_symbol(value)

    @model_validator(mode="after")
    def validate_market_type(self) -> "PairSpreadLegQuery":
        if self.exchange == "binance_alpha" and self.market_type != MarketType.SPOT:
            raise ValueError("binance_alpha only supports spot pair-spread queries")
        return self


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
    market_type: MarketType = MarketType.FUTURE
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
    funding_interval_hours: float | None = Field(default=None, gt=0)
    funding_rate_upper_pct: float | None = None
    funding_rate_lower_pct: float | None = None
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
    interval_minutes: int = 1
    leg2_multiplier: float = 1.0
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
