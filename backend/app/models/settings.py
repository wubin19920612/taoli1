from pydantic import BaseModel, Field

DEFAULT_HIDDEN_RISK_LABELS = [
    "LOW_VOLUME",
    "STALE_DATA",
    "HUGE_SPREAD_VERIFY",
    "WIDE_SPREAD",
    "SAME_TICKER_RISK",
    "MARK_INDEX_DEVIATION",
    "MISSING_FUNDING",
]


class RiskSettings(BaseModel):
    min_volume_24h_usdt: float = Field(default=1_000_000, ge=0)
    stale_after_seconds: int = Field(default=30, ge=5)
    huge_spread_pct: float = Field(default=10.0, ge=0)
    wide_spread_pct: float = Field(default=3.0, ge=0)
    mark_index_deviation_pct: float = Field(default=1.0, ge=0)
    funding_against_pct: float = Field(default=0.01, ge=0)
    ticker_collision_symbols: list[str] = Field(default_factory=lambda: ["AIUSDT", "UPUSDT", "LABUSDT"])


class OpportunityFilterSettings(BaseModel):
    include_risky: bool = False
    hidden_risk_labels: list[str] = Field(default_factory=lambda: DEFAULT_HIDDEN_RISK_LABELS.copy())
    min_volume_24h_k: float = Field(default=0, ge=0)

    @property
    def min_volume_24h_usdt(self) -> float:
        return self.min_volume_24h_k * 1000


class FeeSettings(BaseModel):
    spot_fee_pct: float = 0.1
    future_fee_pct: float = 0.05
    safety_slippage_pct: float = 0.05
