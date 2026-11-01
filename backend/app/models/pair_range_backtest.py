from datetime import timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.models.pair_spread import PairSpreadLegQuery


class PairRangeBacktestRequest(BaseModel):
    leg1: PairSpreadLegQuery
    leg2: PairSpreadLegQuery
    direction: Literal["long1_short2", "short1_long2"]
    start_at: AwareDatetime
    end_at: AwareDatetime
    notional_usd: float = Field(gt=0, le=10_000_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_range(self) -> "PairRangeBacktestRequest":
        if not timedelta(0) < self.end_at - self.start_at <= timedelta(days=30):
            raise ValueError("end_at must be after start_at and within 30 days")
        return self


class RangePricePnl(BaseModel):
    leg1: float
    leg2: float
    total: float


class RangeEndpoint(BaseModel):
    market: PairSpreadLegQuery
    price: float
    timestamp: AwareDatetime
    target_at: AwareDatetime
    deviation_seconds: float


class RangeEndpoints(BaseModel):
    leg1: RangeEndpoint
    leg2: RangeEndpoint


class RangeSettlement(BaseModel):
    leg: Literal["leg1", "leg2"]
    exchange: str
    symbol: str
    dex: str | None
    funding_time: AwareDatetime
    rate_pct: float
    pnl: float


class RangeCoverage(BaseModel):
    leg1: float | None
    leg2: float | None


class RangeFundingPnl(BaseModel):
    settlements: list[RangeSettlement]
    total: float
    coverage: RangeCoverage


class RangeDataGap(BaseModel):
    leg: Literal["leg1", "leg2"]
    kind: Literal["entry", "exit", "funding"]
    deviation_seconds: float | None = None
    expected: int | None = None
    actual: int | None = None


class RangeDataQuality(BaseModel):
    source: dict[str, str]
    granularity_seconds: dict[str, int]
    gaps: list[RangeDataGap]
    is_estimate: Literal[True] = True


class PairRangeBacktestResult(BaseModel):
    price_pnl: RangePricePnl
    entry: RangeEndpoints
    exit: RangeEndpoints
    funding_pnl: RangeFundingPnl
    fees: float
    spread_cost_est: float | None
    total: float
    total_pct_of_notional: float
    data_quality: RangeDataQuality
    warnings: list[str]
