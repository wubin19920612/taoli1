from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.pair_spread import normalize_pair_spread_symbol


SUPPORTED_SECOND_LEVEL_EXCHANGES: tuple[str, ...] = (
    "binance",
    "okx",
    "bybit",
    "gate",
    "bitget",
    "aster",
    "hyperliquid",
)
DEFAULT_SECOND_LEVEL_EXCHANGES: tuple[str, ...] = ("bybit", "bitget")
DEFAULT_SECOND_LEVEL_SYMBOLS: tuple[str, ...] = ("DEXEUSDT",)

SecondLevelSampleStatus = Literal["ok", "partial", "error"]


class SecondLevelSamplingConfig(BaseModel):
    enabled: bool = False
    interval_seconds: float = Field(default=1.0, ge=1.0, le=60.0)
    retention_hours: float = Field(default=48.0, ge=1.0, le=720.0)
    exchanges: list[str] = Field(default_factory=lambda: list(DEFAULT_SECOND_LEVEL_EXCHANGES))
    symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_SECOND_LEVEL_SYMBOLS))
    max_concurrent_requests: int = Field(default=8, ge=1, le=32)

    @field_validator("exchanges")
    @classmethod
    def normalize_exchanges(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            exchange = item.strip().lower()
            if not exchange:
                continue
            if exchange not in SUPPORTED_SECOND_LEVEL_EXCHANGES:
                allowed = ", ".join(SUPPORTED_SECOND_LEVEL_EXCHANGES)
                raise ValueError(f"unsupported exchange: {item}; allowed: {allowed}")
            if exchange in seen:
                continue
            seen.add(exchange)
            normalized.append(exchange)
        return normalized

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            if not item or not item.strip():
                continue
            symbol = normalize_pair_spread_symbol(item)
            if symbol in seen:
                continue
            seen.add(symbol)
            normalized.append(symbol)
        return normalized

    @model_validator(mode="after")
    def require_targets_when_enabled(self) -> "SecondLevelSamplingConfig":
        if self.enabled and (not self.exchanges or not self.symbols):
            raise ValueError("enabled sampling requires at least one exchange and one symbol")
        return self


class SecondLevelMarketSample(BaseModel):
    id: int | None = None
    observed_at: datetime
    exchange: str
    symbol: str
    status: SecondLevelSampleStatus
    spot_bid: float | None = None
    spot_ask: float | None = None
    spot_mid: float | None = None
    spot_last: float | None = None
    future_bid: float | None = None
    future_ask: float | None = None
    future_mid: float | None = None
    future_last: float | None = None
    mark_price: float | None = None
    index_price: float | None = None
    mark_premium_pct: float | None = None
    mid_premium_pct: float | None = None
    funding_rate_pct: float | None = None
    raw_spot_symbol: str | None = None
    raw_future_symbol: str | None = None
    latency_ms: float | None = None
    error: str | None = None


class SecondLevelPairSpreadSnapshot(BaseModel):
    symbol: str
    left_exchange: str
    right_exchange: str
    observed_at: datetime
    left_future_mid: float | None = None
    right_future_mid: float | None = None
    future_spread_pct: float | None = None
    left_mark_premium_pct: float | None = None
    right_mark_premium_pct: float | None = None
    premium_gap_pct: float | None = None


class SecondLevelSamplingStatus(BaseModel):
    running: bool
    config: SecondLevelSamplingConfig
    sample_count: int
    latest_observed_at: datetime | None = None
    latest_error: str | None = None
    latest_samples: list[SecondLevelMarketSample] = Field(default_factory=list)
    latest_spreads: list[SecondLevelPairSpreadSnapshot] = Field(default_factory=list)
