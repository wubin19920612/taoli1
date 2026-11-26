from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator, model_validator

from app.models.funding_arbitrage import (
    AdlRiskLevel,
    FundingArbitrageDecision,
    FundingOpportunityType,
    FundingSource,
)
from app.models.market import MarketType
from app.models.opportunity import OpportunityType
from app.models.pair_range_backtest import PairRangeBacktestResult
from app.models.pair_spread import (
    PairSpreadCurrentSnapshot,
    PairSpreadLegQuery,
    PairSpreadPriceField,
)


EQN_NEWS_CHANNELS = {
    "main": "方程式新闻", "tradfi": "方程式财经", "major": "加密重大行情",
    "tfmoves": "美股重大行情", "hl": "Hyperliquid", "earn": "理财提醒",
    "cz": "CZ&何一", "elon": "马斯克重要推文", "pricechange": "价格异动", "oi": "OI价格异动",
}


class EqnNewsSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    channels: list[str]

    @field_validator("channels")
    @classmethod
    def validate_channels(cls, channels: list[str]) -> list[str]:
        if any(channel not in EQN_NEWS_CHANNELS for channel in channels):
            raise ValueError("未知的方程式新闻频道")
        return list(dict.fromkeys(channels))


class PushKinds(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    near_open: bool
    near_close: bool
    fill: bool
    leg_alert: bool
    big_spread: bool
    stale: bool
    announcement: bool = False
    eqn_news: bool = True
    price: bool = False
    price_watch: bool = True
    funding: bool = False
    auth: bool = False
    trade_status: bool = False
    index_component: bool = False
    premium: bool = False
    spread: bool = False
    general: bool = False
    app_update: bool = True


MAX_MOBILE_APK_SIZE = 60 * 1024 * 1024


class MobileAppRelease(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    version_code: int = Field(gt=0)
    version_name: str = Field(min_length=1, max_length=32)
    size: int = Field(gt=0, le=MAX_MOBILE_APK_SIZE)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    notes: str = Field(max_length=2000)
    published_at: AwareDatetime

    @field_validator("published_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("发布时间必须使用 UTC")
        return value


class MobileAppManifest(MobileAppRelease):
    file: str

    @model_validator(mode="after")
    def validate_filename(self):
        if self.file != f"astro-mobile-{self.version_code}.apk":
            raise ValueError("APK 文件名与版本号不匹配")
        return self


WatchSymbol = Annotated[str, StringConstraints(to_upper=True, pattern=r"^[a-zA-Z0-9]{1,20}$", strict=True)]


class PriceWatchAlertInput(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: WatchSymbol
    kind: Literal["above", "below", "change"]
    price: float | None = Field(default=None, gt=0, strict=True, allow_inf_nan=False)
    change_pct: float | None = Field(default=None, ge=0.1, le=50, strict=True, allow_inf_nan=False)
    window_minutes: int | None = Field(default=None, ge=1, le=60, strict=True)
    direction: Literal["up", "down", "both"] = "both"
    rearm: bool = Field(default=False, strict=True)
    enabled: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def validate_condition(self):
        if self.kind == "change":
            if self.change_pct is None or self.window_minutes is None:
                raise ValueError("涨跌幅提醒须填写幅度和时长")
        elif self.price is None:
            raise ValueError("到价提醒须填写目标价")
        return self

    def condition(self):
        return self.symbol, self.kind, self.price, self.change_pct, self.window_minutes, self.direction


class PriceWatchAlert(PriceWatchAlertInput):
    state: Literal["armed", "triggered", "cooldown", "rearm_wait"] = "armed"
    last_triggered_at: AwareDatetime | None = None
    last_trigger_price: float | None = None
    cooldown_until: AwareDatetime | None = None


class PriceWatchSymbols(BaseModel):
    symbols: list[WatchSymbol]

    @field_validator("symbols")
    @classmethod
    def unique_symbols(cls, symbols):
        unique = list(dict.fromkeys(symbols))
        if len(unique) > 20:
            raise ValueError("最多关注 20 个币")
        return unique


class PriceWatchUpdate(PriceWatchSymbols):
    revision: int = Field(ge=0, strict=True)
    alerts: list[PriceWatchAlertInput] = Field(max_length=50)

    @model_validator(mode="after")
    def validate_alerts(self):
        if len({alert.id for alert in self.alerts}) != len(self.alerts):
            raise ValueError("提醒编号重复")
        if any(alert.symbol not in self.symbols for alert in self.alerts):
            raise ValueError("提醒币种须在关注列表中")
        return self


class PriceWatchConfig(PriceWatchUpdate):
    # Settings saved before revisions were introduced start at zero.
    revision: int = Field(default=0, ge=0, strict=True)
    alerts: list[PriceWatchAlert] = Field(max_length=50)


class PriceWatchQuote(BaseModel):
    symbol: str
    exchange: str | None = None
    market_type: Literal["spot"] | None = None
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    change_5m_pct: float | None = None
    change_15m_pct: float | None = None
    change_1h_pct: float | None = None
    age_seconds: float | None = None
    stale: bool = True
    error: str | None = None


class PriceWatchQuotes(BaseModel):
    observed_at: AwareDatetime
    quotes: list[PriceWatchQuote]


class MobileCardControlSwitchUpdate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    enabled: bool


class PushToken(BaseModel):
    token: SecretStr = Field(min_length=1, max_length=4096)


class PushRegistration(PushToken):
    kinds: PushKinds
    threshold_pp: float = Field(ge=0.01, le=5, strict=True)


CardId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$", strict=True)]


class CardPinUpdate(BaseModel):
    card_id: CardId
    pinned: bool = Field(strict=True)


class MobileRangeBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: CardId
    start_at: AwareDatetime
    end_at: AwareDatetime
    notional_usd: float | None = Field(default=None, strict=True, gt=0, le=10_000_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_range(self):
        duration = self.end_at - self.start_at
        if not timedelta(minutes=1) <= duration <= timedelta(hours=72) or self.end_at > datetime.now(UTC):
            raise ValueError("区间无效")
        return self


class MobileRangeBacktestResult(PairRangeBacktestResult):
    notional_usd: float
    direction: Literal["long1_short2"]
    leg1_label: str
    leg2_label: str


class MobilePairSpreadPoint(BaseModel):
    t: datetime
    a: float
    b: float
    pct: float


class MobilePairSpreadStats(BaseModel):
    min: float | None
    max: float | None
    mean: float | None
    last: float | None


class MobilePairSpreadFundingPoint(BaseModel):
    funding_time: datetime
    leg: Literal["leg1", "leg2"]
    funding_rate_pct: float


class MobilePairSpreadFunding(BaseModel):
    leg1_rate_pct: float | None
    leg2_rate_pct: float | None
    funding_history: list[MobilePairSpreadFundingPoint]


class MobilePairSpreadResult(BaseModel):
    leg1: PairSpreadLegQuery
    leg2: PairSpreadLegQuery
    leg2_multiplier: float
    hours: int
    interval_seconds: int
    observed_at: datetime
    complete: bool | None
    warnings: list[str]
    spread_pct: MobilePairSpreadStats
    spread_abs: MobilePairSpreadStats
    current: PairSpreadCurrentSnapshot | None
    points: list[MobilePairSpreadPoint]
    funding: MobilePairSpreadFunding
    raw_point_count: int


class MobilePairSpreadCurrentLeg(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    exchange: str
    symbol: str
    market_type: MarketType
    dex: str | None
    raw_symbol: str
    price: float
    price_field: PairSpreadPriceField
    is_estimated: bool
    timestamp: datetime


class MobilePairSpreadCurrent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    observed_at: datetime
    leg1: MobilePairSpreadCurrentLeg
    leg2: MobilePairSpreadCurrentLeg
    spread_abs: float
    spread_pct: float


class MobilePremiumIndexPoint(BaseModel):
    t: datetime
    pct: float
    mark: float | None
    index: float | None


class MobilePremiumIndexStats(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    min: float | None
    max: float | None
    mean: float | None
    current: float | None


class MobilePremiumIndexCurrent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mark_price: float | None
    index_price: float | None
    premium_pct: float | None
    funding_rate_pct: float | None
    funding_next_rate_pct: float | None
    funding_next_time: datetime | None
    funding_interval_hours: float | None


class MobilePremiumIndexResult(BaseModel):
    exchange: str
    symbol: str
    dex: str | None
    hours: int
    interval_minutes: int
    observed_at: datetime
    raw_point_count: int
    premium_pct: MobilePremiumIndexStats
    current: MobilePremiumIndexCurrent | None
    points: list[MobilePremiumIndexPoint]
    warnings: list[str]


class MobileOpportunityItem(BaseModel):
    id: str
    type: OpportunityType
    symbol: str
    buy_exchange: str
    buy_market_type: MarketType
    buy_raw_symbol: str | None
    buy_dex: str | None
    sell_exchange: str
    sell_market_type: MarketType
    sell_raw_symbol: str | None
    sell_dex: str | None
    open_spread_pct: float
    close_spread_pct: float
    fee_adjusted_open_pct: float
    min_volume_24h_usdt: float | None
    net_funding_hourly_pct: float | None
    net_funding_next_hourly_pct: float | None
    risk_labels: list[str]
    max_age_ms: float | None
    executable: bool
    fake_flag: bool


class MobileOpportunityList(BaseModel):
    observed_at: datetime
    total: int
    items: list[MobileOpportunityItem]


class MobileFundingSettings(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    enabled: bool
    max_candidates: int
    min_entry_edge_pct: float
    min_funding_edge_pct: float
    min_volume_24h_usdt: float
    min_minutes_to_settlement: int
    max_minutes_to_settlement: int
    notional_per_symbol_usdt: float
    leverage: int


class MobileFundingCandidate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    id: str
    symbol: str
    type: Literal["SF", "FF"]
    long_exchange: str
    long_market_type: str
    long_raw_symbol: str | None
    short_exchange: str
    short_market_type: str
    short_raw_symbol: str | None
    funding_source: FundingSource
    long_current_funding_pct: float | None
    short_current_funding_pct: float | None
    long_next_funding_pct: float | None
    short_next_funding_pct: float | None
    current_funding_edge_pct: float | None
    next_funding_edge_pct: float | None
    long_funding_interval_hours: float | None
    short_funding_interval_hours: float | None
    funding_comparison_interval_hours: float | None
    next_settlement_time: datetime | None
    minutes_to_settlement: float | None
    entry_basis_pct: float
    exit_basis_pct: float
    basis_width_pct: float
    expected_cycle_pnl_pct: float
    estimated_open_cost_pct: float
    estimated_close_cost_pct: float
    adl_risk_score: float
    adl_risk_level: AdlRiskLevel
    decision: FundingArbitrageDecision
    decision_reasons: list[str]
    risk_labels: list[str]
    primary_opportunity_type: FundingOpportunityType
    opportunity_types: list[FundingOpportunityType]
    opportunity_reasons: list[str]
    volume_24h_usdt: float | None
    depth_usdt: float | None
    uses_hyperliquid: bool


class MobileFundingPreview(BaseModel):
    observed_at: datetime
    settings: MobileFundingSettings
    total_pairs_evaluated: int
    displayed_candidates: int
    blocked_missing_funding: int
    blocked_liquidity: int
    blocked_adl_risk: int
    blocked_expected_pnl: int
    enter_count: int
    hold_count: int
    exit_count: int
    blocked_count: int
    filtered_total: int
    candidates: list[MobileFundingCandidate]


class MobileInstrumentAssetGroup(BaseModel):
    asset_id: str | None
    display_name: str
    market_count: int


class MobileInstrumentMarket(BaseModel):
    market_id: str | None
    exchange: str
    market_type: MarketType
    raw_symbol: str
    dex: str | None
    bid: float
    ask: float
    volume_24h_usdt: float | None
    funding_rate_pct: float | None
    funding_next_rate_pct: float | None
    funding_interval_hours: int | None
    funding_next_time: datetime | None
    mark_price: float | None
    index_price: float | None
    data_status: Literal["live", "stale"]
    age_seconds: float
    error: str | None
    is_estimated: bool
    asset_name: str | None


class MobileInstrumentSpread(BaseModel):
    id: str
    opportunity_type: OpportunityType | None
    buy_exchange: str
    buy_market_type: MarketType
    buy_raw_symbol: str
    buy_dex: str | None
    sell_exchange: str
    sell_market_type: MarketType
    sell_raw_symbol: str
    sell_dex: str | None
    buy_ask: float
    sell_bid: float
    executable_spread_pct: float
    close_spread_pct: float
    mid_spread_pct: float
    buy_volume_24h_usdt: float | None
    sell_volume_24h_usdt: float | None
    buy_funding_rate_pct: float | None
    sell_funding_rate_pct: float | None
    buy_funding_interval_hours: int | None
    sell_funding_interval_hours: int | None
    astro_supported: bool
    astro_blocker: str | None
    reference_only: bool
    validation_reasons: list[str]


class MobileInstrumentLookup(BaseModel):
    query: str
    symbol: str
    base: str
    observed_at: datetime | None
    exchange_count: int
    market_count: int
    identity_ambiguous: bool
    asset_groups: list[MobileInstrumentAssetGroup]
    markets: list[MobileInstrumentMarket]
    spreads: list[MobileInstrumentSpread]
    spread_total: int


class _OpsInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class UnlockCode(_OpsInput):
    code: SecretStr = Field(max_length=64)


class DeviceBinding(UnlockCode):
    public_key: str = Field(max_length=4096)
    label: str = Field(max_length=40)


class OpsDevice(_OpsInput):
    device_id: str = Field(max_length=64)


class DeviceProof(OpsDevice):
    challenge_id: str = Field(max_length=64)
    signature: SecretStr


class UnlockSettings(_OpsInput):
    unlock_minutes: int = Field(ge=1, le=30)


class CardControlUpdate(_OpsInput):
    card_id: CardId
    field: Literal["status", "disable_open", "disable_close"]
    value: bool
    expected: bool


class MobileCard(BaseModel):
    id: str | None = None
    pinned_at: float | None = None
    name: str
    type: str
    buy_ex: str
    sell_ex: str
    symbol_a: str | None
    symbol_b: str | None
    raw_symbol_a: str | None
    raw_symbol_b: str | None
    dex_a: str | None
    dex_b: str | None
    enabled: bool
    disable_open: bool
    disable_close: bool
    state: Literal["running", "paused", "open_disabled", "close_disabled"]
    holding: bool
    open_spread_now: float | None = None
    close_spread_now: float | None = None
    open_condition: float | None
    close_condition: float | None
    open_auto: bool
    close_auto: bool
    gap_to_open: float | None = None
    gap_to_close: float | None = None
    qty_a: float
    qty_b: float
    avg_open_a: float | None
    avg_open_b: float | None
    leg_a_notional: float | None
    leg_b_notional: float | None
    leg_imbalance: bool
    notional_usdt: float
    unrealized_usdt: float | None = None
    astro_profit: float | None
    realized_profit: float | None
    funding_a: float | None
    funding_b: float | None
    funding_diff: float | None
    funding_interval_h_a: float | None
    funding_interval_h_b: float | None
    next_funding_at_a: datetime | None
    next_funding_at_b: datetime | None
    metric_error: str
    quote_age_s: float | None


class MobileSummary(BaseModel):
    updated_at: datetime
    astro_cache_age_s: float | None
    stale: bool
    cards: list[MobileCard]


class HistoryPoint(BaseModel):
    t: datetime
    open_spread: float
    close_spread: float


class CardHistory(BaseModel):
    open_condition: float | None
    close_condition: float | None
    points: list[HistoryPoint]
    price_source: Literal["candle_close_1m"] = "candle_close_1m"


class MobileAlert(BaseModel):
    id: int
    created_at: float
    kind: str
    title: str
    body: str
    level: Literal["normal", "urgent"]
    symbol: str | None
    card_name: str | None
