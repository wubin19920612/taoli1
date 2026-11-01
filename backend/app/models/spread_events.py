from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.market import MarketType


class SpreadEventsParams(BaseModel):
    end_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    start_at: datetime | None = None
    threshold: float = Field(default=0.3, ge=0, allow_inf_nan=False)
    metric: Literal["fee_adjusted", "raw"] = "fee_adjusted"
    min_duration_s: float = Field(default=0, ge=0, allow_inf_nan=False)
    sort: Literal["peak", "duration", "start"] = "peak"
    limit: int = Field(default=100, ge=1, le=500)
    symbol: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("start_at", "end_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper().replace("-", "").replace("_", "")
        if not value:
            raise ValueError("symbol must not be blank")
        return value

    @model_validator(mode="after")
    def validate_window(self):
        if self.start_at is None:
            self.start_at = self.end_at - timedelta(hours=24)
        window = self.end_at - self.start_at
        if window <= timedelta(0) or window > timedelta(days=7):
            raise ValueError("查询时间范围必须大于 0 且不超过 7 天")
        return self


class SpreadEventLeg(BaseModel):
    exchange: str
    market_type: MarketType
    raw_symbol: str | None = None
    dex: str | None = None
    price_multiplier: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    contract_size_multiplier: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class SpreadEvent(BaseModel):
    series_key: str
    symbol: str
    buy_leg: SpreadEventLeg
    sell_leg: SpreadEventLeg
    start_at: datetime
    end_at: datetime
    duration_s: float
    peak: float
    peak_at: datetime
    mean: float
    samples: int
    end_reason: Literal["below", "gap", "ongoing"]


class SpreadEventsResponse(BaseModel):
    events: list[SpreadEvent]
    scanned_rows: int
    series_count: int
    truncated: bool
    params: SpreadEventsParams
