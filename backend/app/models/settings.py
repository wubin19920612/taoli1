from pydantic import BaseModel, Field

DEFAULT_HIDDEN_RISK_LABELS = [
    "LOW_VOLUME",
    "STALE_DATA",
    "HUGE_SPREAD_VERIFY",
    "WIDE_SPREAD",
    "SAME_TICKER_RISK",
    "MISSING_FUNDING",
    "THIN_ORDER_BOOK",
    "EDGE_AFTER_SLIPPAGE_TOO_SMALL",
    "TRANSIENT_SIGNAL",
]


class RiskSettings(BaseModel):
    min_volume_24h_usdt: float = Field(default=1_000_000, ge=0)
    stale_after_seconds: int = Field(default=30, ge=5)
    huge_spread_pct: float = Field(default=10.0, ge=0)
    wide_spread_pct: float = Field(default=3.0, ge=0)
    mark_index_deviation_pct: float = Field(default=1.0, ge=0)
    funding_against_pct: float = Field(default=0.01, ge=0)
    signal_slippage_buffer_pct: float = Field(default=0.05, ge=0)
    min_effective_open_pct: float = Field(default=0.05, ge=0)
    max_open_spread_decay_pct: float = Field(default=60.0, ge=0, le=100)
    signal_validation_notional_usdt: float = Field(default=1000, ge=0)
    orderbook_depth_safety_multiple: float = Field(default=2, ge=0)
    min_top_of_book_depth_usdt: float = Field(default=0, ge=0)
    signal_strategy_notes: str = ""
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
    suppress_when_card_conditions_fail: bool = True
    observation_limit: int = Field(default=5, ge=1, le=20)


class AstroCardSettings(BaseModel):
    max_trade_usdt: float = Field(default=10, gt=0)
    leverage: int = Field(default=1, ge=1)
    min_notional: float = Field(default=10, ge=0)
    max_notional: float = Field(default=10, gt=0)
    close_position_buffer_pct: float = Field(default=0.1, ge=0)
    unfavorable_funding_weight: float = Field(default=1, ge=0)
    close_position_floor_pct: float = Field(default=0, ge=0)


class LivePilotSettings(BaseModel):
    enabled: bool = False
    max_symbols: int = Field(default=10, ge=1, le=100)
    notional_per_symbol_usdt: float = Field(default=100, gt=0)
    min_next_funding_edge_pct: float = Field(default=-0.05)
    prefer_hyperliquid: bool = True
    exclude_ss: bool = True
    create_cards_enabled: bool = True


class LivePilotPreviewItem(BaseModel):
    opportunity_id: str
    symbol: str
    type: str
    route: str
    buy_exchange: str
    sell_exchange: str
    uses_hyperliquid: bool
    open_spread_pct: float
    fee_adjusted_open_pct: float
    next_funding_edge_pct: float
    combined_open_edge_pct: float
    volume_24h_usdt: float | None
    notional_usdt: float
    risk_labels: list[str]


class LivePilotPreview(BaseModel):
    settings: LivePilotSettings
    total_opportunities: int
    eligible_symbols: int
    selected_symbols: int
    skipped_negative_funding: int
    skipped_type: int = 0
    skipped_risk: int = 0
    budget_usdt: float
    items: list[LivePilotPreviewItem]


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
