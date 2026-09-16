from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PREADD_EXCHANGES = ("bitget", "binance", "bybit", "gate", "okx", "hyperliquid", "lighter")


class AstroPreaddSettings(BaseModel):
    enabled: bool = False
    exchanges: list[str] = Field(default_factory=lambda: ["bitget", "binance"])
    funding_threshold_pct: float = Field(default=0.6, gt=0, le=100)
    premium_threshold_pct: float = Field(default=1.0, gt=0, le=100)
    open_spread_threshold_pct: float = Field(default=0.9, gt=0, le=100)
    min_volume_24h_usdt: float = Field(default=0, ge=0, le=1_000_000_000_000)
    scan_interval_seconds: int = Field(default=60, ge=30, le=3600)
    max_routes_per_run: int = Field(default=5, ge=1, le=20)
    stale_after_seconds: int = Field(default=30, ge=5, le=300)

    @field_validator("exchanges")
    @classmethod
    def valid_exchanges(cls, exchanges: list[str]) -> list[str]:
        selected = list(dict.fromkeys(item.strip().lower() for item in exchanges))
        if len(selected) < 2 or any(item not in PREADD_EXCHANGES for item in selected):
            raise ValueError("至少选择两个支持的预建交易所")
        return selected


class AstroPreaddLegSnapshot(BaseModel):
    exchange: str
    premium_index_pct: float | None = None
    funding_rate_pct: float | None = None
    funding_interval_hours: int | None = None
    volume_24h_usdt: float | None = None


class AstroPreaddCandidate(BaseModel):
    id: str
    symbol: str
    buy_exchange: str
    sell_exchange: str
    signal_exchange: str
    signal_type: Literal["funding", "premium_proxy"]
    signal_value_pct: float
    funding_source: Literal["predicted", "current", "missing"]
    funding_interval_hours: int | None = None
    entry_mode: Literal["convergence", "bybit_funding_reverse"] = "convergence"
    card_open_spread_pct: float
    buy_leg: AstroPreaddLegSnapshot
    sell_leg: AstroPreaddLegSnapshot
    live_spread_pct: float
    observed_at: datetime


class AstroPreaddPreview(BaseModel):
    items: list[AstroPreaddCandidate]
    warnings: list[str] = Field(default_factory=list)
    total_matches: int = 0


class AstroPreaddRunRequest(BaseModel):
    candidate_ids: list[str] | None = None


class AstroPreaddRunResult(BaseModel):
    attempted: int = 0
    created: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
