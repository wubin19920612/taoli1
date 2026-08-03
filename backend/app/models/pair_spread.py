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
SUPPORTED_SYMBOL_SPREAD_EXCHANGES: tuple[str, ...] = tuple(
    exchange for exchange in SUPPORTED_PAIR_SPREAD_EXCHANGES if exchange != "binance_alpha"
)
PAIR_SPREAD_MIN_HOURS = 1
PAIR_SPREAD_MAX_HOURS = 720
PAIR_SPREAD_INTERVAL_OPTIONS: tuple[int, ...] = (1, 5, 15)
PAIR_SPREAD_INTERVAL_SECONDS_OPTIONS: tuple[int, ...] = (5, 10, 30, 60, 300, 900)
PAIR_SPREAD_MIN_INTERVAL_SECONDS = 5
PAIR_SPREAD_MAX_INTERVAL_SECONDS = 86_400
PAIR_SPREAD_FUNDING_RECORD_INTERVAL_SECONDS = 60


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


class PairSpreadRealtimeFundingPoint(BaseModel):
    bucket_at: datetime
    left_rate_pct: float | None = None
    right_rate_pct: float | None = None
    net_rate_pct: float | None = None
    source: str = "current"


class PairSpreadFundingRecordRequest(BaseModel):
    leg1: PairSpreadLegQuery
    leg2: PairSpreadLegQuery
    leg2_multiplier: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def require_funding_leg(self) -> "PairSpreadFundingRecordRequest":
        if self.leg1.market_type != MarketType.FUTURE or self.leg2.market_type != MarketType.FUTURE:
            raise ValueError("分钟资金费率记录只支持合约对")
        return self


class PairSpreadFundingWatchItem(BaseModel):
    pair_key: str
    leg1: PairSpreadLegQuery
    leg2: PairSpreadLegQuery
    leg2_multiplier: float = Field(gt=0)
    interval_seconds: int = PAIR_SPREAD_FUNDING_RECORD_INTERVAL_SECONDS
    created_at: datetime
    updated_at: datetime
    sample_count: int = 0
    latest_sample_at: datetime | None = None


class PairSpreadFundingRecordStatus(BaseModel):
    watched: bool
    item: PairSpreadFundingWatchItem | None = None
    samples: list[PairSpreadRealtimeFundingPoint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    interval_seconds: int = 60
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
    realtime_funding: list[PairSpreadRealtimeFundingPoint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PairSpreadFundingHistoryResult(BaseModel):
    leg1: PairSpreadLegQuery
    leg2: PairSpreadLegQuery
    start_at: datetime
    end_at: datetime
    funding_history: list[PairSpreadFundingPoint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SymbolExchangePriceSnapshot(BaseModel):
    exchange: str
    symbol: str
    market_type: MarketType = MarketType.FUTURE
    raw_symbol: str
    price: float
    price_field: PairSpreadPriceField
    funding_rate_pct: float | None = None
    timestamp: datetime


class SymbolSpreadPoint(BaseModel):
    bucket_at: datetime
    base_close: float
    exchange_close: float
    spread_abs: float
    spread_pct: float


class SymbolSpreadSeries(BaseModel):
    exchange: str
    symbol: str
    market_type: MarketType = MarketType.FUTURE
    point_count: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    spread_abs: PairSpreadValueStats
    spread_pct: PairSpreadValueStats
    current: SymbolSpreadPoint | None = None
    points: list[SymbolSpreadPoint]


class SymbolSpreadQueryResult(BaseModel):
    symbol: str
    market_type: MarketType = MarketType.FUTURE
    base_exchange: str
    exchanges: list[str]
    hours: int
    interval_minutes: int = 1
    interval_seconds: int = 60
    observed_at: datetime
    point_count: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    current_prices: list[SymbolExchangePriceSnapshot] = Field(default_factory=list)
    series: list[SymbolSpreadSeries]
    warnings: list[str] = Field(default_factory=list)


class PairSpreadDiagnosticThresholdRun(BaseModel):
    start_at: datetime | None = None
    end_at: datetime | None = None
    point_count: int = 0
    peak_spread_pct: float | None = None
    peak_at: datetime | None = None


class PairSpreadDiagnosticRule(BaseModel):
    id: str
    name: str
    enabled: bool
    matches_pair_scope: bool
    min_open_spread_pct: float
    min_fee_adjusted_open_pct: float
    consecutive_hits: int
    cooldown_seconds: int
    reasons: list[str] = Field(default_factory=list)


class PairSpreadDiagnosticEvent(BaseModel):
    rule_id: str
    status: str
    created_at: datetime
    message: str


class PairSpreadDiagnosticEventSummary(BaseModel):
    total: int = 0
    sent: int = 0
    muted: int = 0
    failed: int = 0
    latest_status: str | None = None
    latest_at: datetime | None = None
    latest_message: str | None = None
    events: list[PairSpreadDiagnosticEvent] = Field(default_factory=list)


class PairSpreadDiagnosticResult(BaseModel):
    leg1: PairSpreadLegQuery
    leg2: PairSpreadLegQuery
    hours: int
    requested_interval_seconds: int
    interval_seconds: int
    observed_at: datetime
    point_count: int
    threshold_pct: float
    peak_at: datetime | None = None
    peak_spread_pct: float | None = None
    peak_spread_abs: float | None = None
    peak_leg1_close: float | None = None
    peak_leg2_close: float | None = None
    points_over_threshold: int = 0
    first_over_threshold_at: datetime | None = None
    last_over_threshold_at: datetime | None = None
    longest_run: PairSpreadDiagnosticThresholdRun = Field(
        default_factory=PairSpreadDiagnosticThresholdRun
    )
    current_spread_pct: float | None = None
    inferred_type: str
    alert_rules: list[PairSpreadDiagnosticRule] = Field(default_factory=list)
    alert_events: PairSpreadDiagnosticEventSummary = Field(
        default_factory=PairSpreadDiagnosticEventSummary
    )
    suppress_when_card_conditions_fail: bool = False
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
