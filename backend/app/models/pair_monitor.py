from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.market import MarketType


class PairMonitorPriceField(StrEnum):
    AUTO = "auto"
    MID_PRICE = "mid_price"
    MARK_PRICE = "mark_price"
    INDEX_PRICE = "index_price"
    BID = "bid"
    ASK = "ask"


class PairMonitorSampleStatus(StrEnum):
    RECORDED = "recorded"
    SKIPPED = "skipped"


def normalize_pair_monitor_symbol(value: str) -> str:
    normalized = value.strip().upper().replace("_", "-").replace("/", "-")
    if normalized.endswith("-SWAP"):
        normalized = normalized.removesuffix("-SWAP")
    compact = normalized.replace("-", "")
    if not compact:
        raise ValueError("symbol is required")
    return compact if compact.endswith("USDT") else f"{compact}USDT"


class PairMonitorLeg(BaseModel):
    exchange: str
    symbol: str
    market_type: MarketType = MarketType.FUTURE
    price_field: PairMonitorPriceField = PairMonitorPriceField.AUTO

    @field_validator("exchange")
    @classmethod
    def normalize_exchange(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("exchange is required")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_pair_monitor_symbol(value)


class PairMonitorRule(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = ""
    enabled: bool = True
    leg1: PairMonitorLeg
    leg2: PairMonitorLeg
    sample_interval_seconds: int = Field(default=60, ge=60, le=3600)
    retention_days: int = Field(default=7, ge=1, le=30)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def fill_name(self) -> "PairMonitorRule":
        if not self.name.strip():
            self.name = (
                f"{self.leg1.exchange}:{self.leg1.symbol} "
                f"/ {self.leg2.exchange}:{self.leg2.symbol}"
            )
        return self


class PairMonitorPoint(BaseModel):
    rule_id: str
    observed_at: datetime
    bucket_at: datetime
    leg1_price: float
    leg2_price: float
    spread_abs: float
    spread_pct: float
    leg1_funding_rate_pct: float | None = None
    leg2_funding_rate_pct: float | None = None
    leg1_funding_next_rate_pct: float | None = None
    leg2_funding_next_rate_pct: float | None = None
    leg1_funding_next_time: datetime | None = None
    leg2_funding_next_time: datetime | None = None
    leg1_volume_24h_usdt: float | None = None
    leg2_volume_24h_usdt: float | None = None
    leg1_price_field: PairMonitorPriceField
    leg2_price_field: PairMonitorPriceField
    leg1_market_timestamp: datetime | None = None
    leg2_market_timestamp: datetime | None = None


class PairMonitorValueStats(BaseModel):
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    current: float | None = None


class PairMonitorHistory(BaseModel):
    rule: PairMonitorRule
    count: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    latest: PairMonitorPoint | None = None
    spread_pct: PairMonitorValueStats
    leg1_funding_rate_pct: PairMonitorValueStats
    leg2_funding_rate_pct: PairMonitorValueStats
    points: list[PairMonitorPoint]


class PairMonitorSampleResult(BaseModel):
    rule_id: str
    status: PairMonitorSampleStatus
    reason: str | None = None
    point: PairMonitorPoint | None = None
