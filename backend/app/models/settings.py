from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.market import MarketType

DEFAULT_HIDDEN_RISK_LABELS = [
    "LOW_VOLUME",
    "STALE_DATA",
    "SAME_TICKER_RISK",
    "MISSING_FUNDING",
    "THIN_ORDER_BOOK",
    "EDGE_AFTER_SLIPPAGE_TOO_SMALL",
    "TRANSIENT_SIGNAL",
]

MAX_FLOATING_WATCH_SYMBOLS = 12
MAX_FLOATING_WATCH_PAIRS = 12


def _normalize_alias_symbol(value: str) -> str:
    normalized = value.strip().upper().replace("_", "-").replace("/", "-")
    if normalized.endswith("-SWAP"):
        normalized = normalized.removesuffix("-SWAP")
    compact = normalized.replace("-", "")
    if not compact:
        raise ValueError("symbol is required")
    return compact if compact.endswith("USDT") else f"{compact}USDT"


class FloatingWatchSettings(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=MAX_FLOATING_WATCH_SYMBOLS)
    pair_ids: list[str] = Field(default_factory=list, max_length=MAX_FLOATING_WATCH_PAIRS)

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                continue
            symbol = _normalize_alias_symbol(item)
            if symbol not in normalized:
                normalized.append(symbol)
        return normalized

    @field_validator("pair_ids", mode="before")
    @classmethod
    def normalize_pair_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            pair_id = item.strip() if isinstance(item, str) else ""
            if len(pair_id) > 512:
                raise ValueError("pair id must not exceed 512 characters")
            if pair_id and pair_id not in normalized:
                normalized.append(pair_id)
        return normalized


class FloatingWatchMutation(BaseModel):
    action: Literal["add", "remove"]
    item_type: Literal["symbol", "pair"]
    value: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def normalize_value(self) -> "FloatingWatchMutation":
        normalized = (
            _normalize_alias_symbol(self.value)
            if self.item_type == "symbol"
            else self.value.strip()
        )
        if not normalized:
            raise ValueError("watch item value is required")
        if self.item_type == "symbol" and len(normalized) > 128:
            raise ValueError("symbol must not exceed 128 characters")
        self.value = normalized
        return self


class SymbolAlias(BaseModel):
    exchange: str
    symbol: str
    canonical_symbol: str
    market_type: MarketType | None = None
    dex: str | None = None
    price_multiplier: float = Field(default=1.0, gt=0)

    @field_validator("exchange")
    @classmethod
    def normalize_exchange(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("exchange is required")
        return normalized

    @field_validator("symbol", "canonical_symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return _normalize_alias_symbol(value)

    @field_validator("dex")
    @classmethod
    def normalize_dex(cls, value: str | None) -> str | None:
        normalized = value.strip().lower() if isinstance(value, str) else None
        return normalized or None

    @model_validator(mode="after")
    def validate_dex_scope(self) -> "SymbolAlias":
        if self.dex is not None and self.exchange != "hyperliquid":
            raise ValueError("dex is only supported for hyperliquid symbol aliases")
        if self.dex is not None and self.market_type not in {None, MarketType.FUTURE}:
            raise ValueError("hyperliquid DEX aliases only support contract markets")
        return self


def default_symbol_aliases() -> list[SymbolAlias]:
    return [
        SymbolAlias(
            exchange="gate",
            symbol="EDGEXUSDT",
            canonical_symbol="EDGEUSDT",
            price_multiplier=1,
        )
    ]


class RiskSettings(BaseModel):
    min_volume_24h_usdt: float = Field(default=1_000_000, ge=0)
    stale_after_seconds: int = Field(default=30, ge=5)
    huge_spread_pct: float = Field(default=10.0, ge=0)
    wide_spread_pct: float = Field(default=3.0, ge=0)
    mark_index_deviation_pct: float = Field(default=1.0, ge=0)
    funding_against_pct: float = Field(default=0.01, ge=0)
    signal_slippage_buffer_pct: float = Field(default=0.05, ge=0)
    min_effective_open_pct: float = 0.05
    max_open_spread_decay_pct: float = Field(default=60.0, ge=0, le=100)
    signal_validation_notional_usdt: float = Field(default=1000, ge=0)
    orderbook_depth_safety_multiple: float = Field(default=2, ge=0)
    orderbook_depth_band_pct: float = Field(default=0.1, ge=0)
    min_top_of_book_depth_usdt: float = Field(default=0, ge=0)
    signal_strategy_notes: str = ""
    ticker_collision_symbols: list[str] = Field(default_factory=lambda: ["AIUSDT", "UPUSDT", "LABUSDT"])
    excluded_symbols: list[str] = Field(default_factory=list)
    ignored_exchanges: list[str] = Field(default_factory=list)
    symbol_aliases: list[SymbolAlias] = Field(default_factory=default_symbol_aliases)


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
    suppress_when_card_conditions_fail: bool = False
    observation_limit: int = Field(default=5, ge=1, le=20)


class AstroCardSettings(BaseModel):
    max_trade_usdt: float = Field(default=10, gt=0)
    leverage: int = Field(default=1, ge=1)
    min_notional: float = Field(default=10, ge=0)
    max_notional: float = Field(default=10, gt=0)
    open_enabled: bool = False
    close_position_buffer_pct: float = Field(default=0.1, ge=0)
    unfavorable_funding_weight: float = Field(default=1, ge=0)
    close_position_floor_pct: float = Field(default=0, ge=0)


class AstroAutomationSettings(BaseModel):
    alert_auto_create: bool = False
    allow_same_name_variants: bool = False


class LivePilotSettings(BaseModel):
    enabled: bool = False
    max_symbols: int = Field(default=10, ge=1, le=100)
    notional_per_symbol_usdt: float = Field(default=100, gt=0)
    min_next_funding_edge_pct: float = Field(default=-0.05)
    prefer_hyperliquid: bool = True
    exclude_ss: bool = True
    create_cards_enabled: bool = True


class MinuteSignalSettings(BaseModel):
    hours: int = Field(default=4, ge=1, le=24)
    max_symbols: int = Field(default=30, ge=5, le=100)
    min_volume_24h_usdt: float = Field(default=100_000, ge=0)
    alert_cooldown_minutes: int = Field(default=60, ge=1, le=10_080)
    # 入场时不希望 Alpha 现货明显贵于合约；45 bps 约等于 0.45%，接近平价。
    max_entry_basis_bps: float = Field(default=45.0, ge=-10_000, le=10_000)
    # 如果现货仍高于合约，要求合约相对指数有负溢价，否则多半只是持久基差。
    require_negative_premium_when_spot_above: bool = True
    max_premium_when_spot_above_bps: float = Field(default=-5.0, le=0)


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
