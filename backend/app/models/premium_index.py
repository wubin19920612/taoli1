from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.pair_spread import normalize_pair_spread_symbol, split_hyperliquid_symbol


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
PREMIUM_INDEX_INTERVAL_OPTIONS: tuple[int, ...] = (1, 5, 15, 60, 240, 1440)


class PremiumIndexMarketQuery(BaseModel):
    exchange: str
    symbol: str
    dex: str | None = None

    @model_validator(mode="before")
    @classmethod
    def extract_hyperliquid_dex(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        exchange = str(value.get("exchange", "")).strip().lower()
        if exchange != "hyperliquid" or "symbol" not in value:
            return value
        payload = dict(value)
        dex, symbol = split_hyperliquid_symbol(
            str(payload.get("symbol", "")),
            payload.get("dex") if isinstance(payload.get("dex"), str) else None,
        )
        payload["dex"] = dex
        payload["symbol"] = symbol
        return payload

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

    @field_validator("dex")
    @classmethod
    def normalize_dex(cls, value: str | None) -> str | None:
        normalized = value.strip().lower() if isinstance(value, str) else None
        return normalized or None

    @model_validator(mode="after")
    def validate_dex(self) -> "PremiumIndexMarketQuery":
        if self.dex is not None and self.exchange != "hyperliquid":
            raise ValueError("dex is only supported for hyperliquid premium-index queries")
        return self


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
    dex: str | None = None
    mark_price: float | None = Field(default=None, gt=0)
    index_price: float | None = Field(default=None, gt=0)
    mid_price: float | None = Field(default=None, gt=0)
    last_price: float | None = Field(default=None, gt=0)
    premium_pct: float | None = None
    mid_premium_pct: float | None = None
    funding_rate_pct: float | None = None
    funding_next_rate_pct: float | None = None
    funding_next_time: datetime | None = None
    funding_interval_hours: float | None = Field(default=None, gt=0)
    funding_rate_upper_pct: float | None = None
    funding_rate_lower_pct: float | None = None
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
    dex: str | None = None
    point_count: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    premium_pct: PremiumIndexValueStats
    current: PremiumIndexCurrentSnapshot | None = None
    points: list[PremiumIndexPoint]
    warnings: list[str] = Field(default_factory=list)
