from pydantic import BaseModel, Field

DEFAULT_HIDDEN_RISK_LABELS = [
    "LOW_VOLUME",
    "STALE_DATA",
    "HUGE_SPREAD_VERIFY",
    "WIDE_SPREAD",
    "SAME_TICKER_RISK",
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
    excluded_symbols: list[str] = Field(default_factory=list)
    ignored_exchanges: list[str] = Field(default_factory=list)


class AlertMessageTemplateSettings(BaseModel):
    include_trigger_summary: bool = True
    include_rule_details: bool = True
    include_pair: bool = True
    include_spread: bool = True
    include_funding: bool = True
    include_volume: bool = True
    include_risk: bool = True
    include_observations: bool = True
    include_dashboard_link: bool = True
    observation_limit: int = Field(default=5, ge=1, le=20)


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


class HistorySettings(BaseModel):
    enabled: bool = True
    sample_seconds: int = Field(default=120, ge=10)
    retention_days: int = Field(default=3, ge=1)
    keep_top_n: int = Field(default=100, ge=1)
    min_open_spread_pct: float = Field(default=0.5, ge=0)
    min_volume_24h_k: float = Field(default=100, ge=0)
    vacuum_interval_seconds: int = Field(default=86_400, ge=60)

    @property
    def min_volume_24h_usdt(self) -> float:
        return self.min_volume_24h_k * 1000
