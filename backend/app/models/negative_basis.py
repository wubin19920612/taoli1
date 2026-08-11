from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.market import MarketType
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadLegQuery,
    PairSpreadValueStats,
    SUPPORTED_PAIR_SPREAD_EXCHANGES,
    normalize_pair_spread_symbol,
)


NEGATIVE_BASIS_SPOT_EXCHANGES: tuple[str, ...] = tuple(
    exchange for exchange in SUPPORTED_PAIR_SPREAD_EXCHANGES if exchange != "hyperliquid"
)
NEGATIVE_BASIS_FUTURE_EXCHANGES: tuple[str, ...] = tuple(
    exchange for exchange in SUPPORTED_PAIR_SPREAD_EXCHANGES if exchange != "binance_alpha"
)

NegativeBasisSignalLevel = Literal[
    "none",
    "watch",
    "building",
    "confirmed",
    "strong",
    "extreme",
]

NEGATIVE_BASIS_LEVEL_ORDER: dict[str, int] = {
    "none": 0,
    "watch": 1,
    "building": 2,
    "confirmed": 3,
    "strong": 4,
    "extreme": 5,
}


def utc_now() -> datetime:
    return datetime.now(UTC)


class NegativeBasisWatchItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    auto_managed: bool = Field(default=False)
    enabled: bool = Field(default=True)
    symbol: str = Field(default="PROMUSDT")
    spot_exchange: str = Field(default="binance")
    future_exchange: str = Field(default="gate")
    spot_symbol: str | None = Field(default=None)
    future_symbol: str | None = Field(default=None)
    future_multiplier: float = Field(default=1.0, gt=0)
    interval_seconds: int = Field(default=60, ge=30, le=3600)
    lookback_hours: int = Field(default=4, ge=1, le=720)
    retention_hours: int = Field(default=720, ge=1, le=2160)
    watch_threshold_pct: float = Field(default=0.5, ge=0)
    building_threshold_pct: float = Field(default=1.0, ge=0)
    confirmed_threshold_pct: float = Field(default=2.0, ge=0)
    strong_threshold_pct: float = Field(default=3.0, ge=0)
    extreme_threshold_pct: float = Field(default=10.0, ge=0)
    watch_consecutive_hits: int = Field(default=3, ge=1, le=60)
    building_consecutive_hits: int = Field(default=3, ge=1, le=60)
    confirmed_consecutive_hits: int = Field(default=3, ge=1, le=60)
    strong_consecutive_hits: int = Field(default=2, ge=1, le=60)
    extreme_consecutive_hits: int = Field(default=1, ge=1, le=60)
    spot_volume_growth_threshold: float = Field(default=3.0, ge=0)
    oi_confirmed_growth_pct: float = Field(default=20.0, ge=0)
    oi_strong_growth_pct: float = Field(default=30.0, ge=0)
    min_spot_hourly_volume_usdt: float = Field(default=0.0, ge=0)
    alert_min_level: NegativeBasisSignalLevel = Field(default="watch")
    cooldown_seconds: int = Field(default=900, ge=0, le=86_400)
    note: str = Field(default="")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("symbol", "spot_symbol", "future_symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            return None
        return normalize_pair_spread_symbol(value)

    @field_validator("spot_exchange")
    @classmethod
    def normalize_spot_exchange(cls, value: str) -> str:
        exchange = value.strip().lower()
        if exchange not in NEGATIVE_BASIS_SPOT_EXCHANGES:
            allowed = ", ".join(NEGATIVE_BASIS_SPOT_EXCHANGES)
            raise ValueError(f"unsupported spot exchange: {value}; allowed: {allowed}")
        return exchange

    @field_validator("future_exchange")
    @classmethod
    def normalize_future_exchange(cls, value: str) -> str:
        exchange = value.strip().lower()
        if exchange not in NEGATIVE_BASIS_FUTURE_EXCHANGES:
            allowed = ", ".join(NEGATIVE_BASIS_FUTURE_EXCHANGES)
            raise ValueError(f"unsupported future exchange: {value}; allowed: {allowed}")
        return exchange

    @model_validator(mode="after")
    def validate_thresholds(self) -> "NegativeBasisWatchItem":
        thresholds = [
            self.watch_threshold_pct,
            self.building_threshold_pct,
            self.confirmed_threshold_pct,
            self.strong_threshold_pct,
            self.extreme_threshold_pct,
        ]
        if thresholds != sorted(thresholds):
            raise ValueError("negative basis thresholds must be ascending")
        if self.spot_symbol is None:
            self.spot_symbol = self.symbol
        if self.future_symbol is None:
            self.future_symbol = self.symbol
        return self

    def spot_leg(self) -> PairSpreadLegQuery:
        return PairSpreadLegQuery(
            exchange=self.spot_exchange,
            symbol=self.spot_symbol or self.symbol,
            market_type=MarketType.SPOT,
        )

    def future_leg(self) -> PairSpreadLegQuery:
        return PairSpreadLegQuery(
            exchange=self.future_exchange,
            symbol=self.future_symbol or self.symbol,
            market_type=MarketType.FUTURE,
        )


class NegativeBasisPoint(BaseModel):
    bucket_at: datetime
    spot_close: float
    future_close: float
    spot_premium_abs: float
    spot_premium_pct: float


class NegativeBasisHourlyStatPoint(BaseModel):
    bucket_at: datetime
    spot_premium_mean_pct: float | None = None
    spot_premium_max_pct: float | None = None
    spot_premium_last_pct: float | None = None
    spot_volume_usdt: float | None = Field(default=None, ge=0)
    future_volume_usdt: float | None = Field(default=None, ge=0)
    spot_volume_growth: float | None = Field(default=None, ge=0)
    future_volume_ratio: float | None = Field(default=None, ge=0)
    open_interest_open_usdt: float | None = Field(default=None, ge=0)
    open_interest_close_usdt: float | None = Field(default=None, ge=0)
    open_interest_change_pct: float | None = None
    long_account_pct: float | None = Field(default=None, ge=0)
    short_account_pct: float | None = Field(default=None, ge=0)
    long_account_count: float | None = Field(default=None, ge=0)
    short_account_count: float | None = Field(default=None, ge=0)
    long_short_ratio: float | None = Field(default=None, ge=0)
    funding_rate_pct: float | None = None


class NegativeBasisThresholdState(BaseModel):
    name: NegativeBasisSignalLevel
    threshold_pct: float
    required_hits: int
    first_seen_at: datetime | None = None
    first_consecutive_at: datetime | None = None
    current_consecutive_hits: int = 0
    max_consecutive_hits: int = 0
    currently_active: bool = False


class NegativeBasisCurrentSnapshot(BaseModel):
    observed_at: datetime
    spot_leg: PairSpreadCurrentLeg
    future_leg: PairSpreadCurrentLeg
    spot_premium_abs: float
    spot_premium_pct: float


class NegativeBasisAnalysisResult(BaseModel):
    item: NegativeBasisWatchItem
    observed_at: datetime
    signal_level: NegativeBasisSignalLevel
    score: float
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    current: NegativeBasisCurrentSnapshot | None = None
    spot_premium: PairSpreadValueStats
    thresholds: list[NegativeBasisThresholdState] = Field(default_factory=list)
    points: list[NegativeBasisPoint] = Field(default_factory=list)
    hourly_stats: list[NegativeBasisHourlyStatPoint] = Field(default_factory=list)


class NegativeBasisSignalSample(BaseModel):
    id: int | None = None
    watch_id: str
    observed_at: datetime
    symbol: str
    spot_exchange: str
    future_exchange: str
    signal_level: NegativeBasisSignalLevel
    score: float
    spot_premium_pct: float | None = None
    spot_price: float | None = None
    future_price: float | None = None
    spot_volume_24h_usdt: float | None = Field(default=None, ge=0)
    future_volume_24h_usdt: float | None = Field(default=None, ge=0)
    open_interest_usdt: float | None = Field(default=None, ge=0)
    open_interest_change_pct: float | None = None
    long_account_pct: float | None = Field(default=None, ge=0)
    short_account_pct: float | None = Field(default=None, ge=0)
    long_short_ratio: float | None = Field(default=None, ge=0)
    funding_rate_pct: float | None = None
    reasons: list[str] = Field(default_factory=list)


class NegativeBasisAlertEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    watch_id: str
    symbol: str
    spot_exchange: str
    future_exchange: str
    signal_level: NegativeBasisSignalLevel
    score: float
    spot_premium_pct: float | None = None
    message: str
    created_at: datetime = Field(default_factory=utc_now)


class NegativeBasisAutoCandidate(BaseModel):
    id: str
    symbol: str
    spot_exchange: str
    future_exchange: str
    signal_level: NegativeBasisSignalLevel = "none"
    spot_premium_pct: float
    spot_price: float
    future_price: float
    spot_volume_24h_usdt: float | None = Field(default=None, ge=0)
    future_volume_24h_usdt: float | None = Field(default=None, ge=0)
    observed_at: datetime


class NegativeBasisMonitorStatus(BaseModel):
    running: bool
    auto_scan_enabled: bool = True
    auto_scan_last_at: datetime | None = None
    auto_scan_error: str | None = None
    auto_candidate_count: int = 0
    auto_candidates: list[NegativeBasisAutoCandidate] = Field(default_factory=list)
    watch_count: int
    enabled_watch_count: int
    sample_count: int
    event_count: int
    latest_error: str | None = None
    watchlist: list[NegativeBasisWatchItem] = Field(default_factory=list)
    latest_samples: list[NegativeBasisSignalSample] = Field(default_factory=list)
    latest_events: list[NegativeBasisAlertEvent] = Field(default_factory=list)
