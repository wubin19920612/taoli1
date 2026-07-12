from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


SUPPORTED_RADAR_EXCHANGES: tuple[str, ...] = (
    "binance",
    "okx",
    "bybit",
    "gate",
    "bitget",
    "aster",
    "hyperliquid",
)

PremiumDirection = Literal["negative", "positive", "both"]
RadarDirection = Literal["LONG_ANCHOR_SHORT_PEER", "LONG_PEER_SHORT_ANCHOR"]
RadarSignalLevel = Literal["HIGH", "MEDIUM", "WATCH"]


class OpportunityRadarSettings(BaseModel):
    enabled: bool = True
    anchor_exchange: str = "bybit"
    premium_direction: PremiumDirection = "both"
    min_abs_premium_pct: float = Field(default=1.5, ge=0)
    min_relative_premium_gap_pct: float = Field(default=0.5, ge=0)
    max_abs_entry_spread_pct: float = Field(default=0.5, ge=0)
    require_funding_alignment: bool = False
    min_hourly_funding_edge_pct: float = 0.0
    min_volume_24h_usdt: float = Field(default=1_000_000, ge=0)
    notional_per_symbol_usdt: float = Field(default=100, gt=0)
    min_depth_multiple: float = Field(default=5, ge=0)
    max_data_age_seconds: int = Field(default=120, ge=10, le=3600)
    max_candidates: int = Field(default=50, ge=1, le=500)

    @field_validator("anchor_exchange")
    @classmethod
    def normalize_anchor_exchange(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_RADAR_EXCHANGES:
            allowed = ", ".join(SUPPORTED_RADAR_EXCHANGES)
            raise ValueError(f"unsupported anchor exchange: {value}; allowed: {allowed}")
        return normalized


class OpportunityRadarCandidate(BaseModel):
    id: str
    symbol: str
    signal_level: RadarSignalLevel
    score: float
    direction: RadarDirection
    long_exchange: str
    short_exchange: str
    anchor_exchange: str
    peer_exchange: str
    anchor_premium_pct: float
    peer_premium_pct: float
    peer_median_premium_pct: float
    relative_premium_gap_pct: float
    entry_spread_pct: float
    long_entry_price: float
    short_entry_price: float
    long_funding_pct: float | None = None
    short_funding_pct: float | None = None
    long_funding_interval_hours: int | None = None
    short_funding_interval_hours: int | None = None
    hourly_funding_edge_pct: float | None = None
    volume_24h_usdt: float | None = None
    depth_usdt: float | None = None
    data_age_seconds: float
    reasons: list[str] = Field(default_factory=list)
    risk_labels: list[str] = Field(default_factory=list)


class OpportunityRadarPreview(BaseModel):
    observed_at: datetime
    settings: OpportunityRadarSettings
    anchor_markets: int
    total_pairs_evaluated: int
    displayed_candidates: int
    high_count: int
    medium_count: int
    watch_count: int
    candidates: list[OpportunityRadarCandidate]
