from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.market import MarketType
from app.models.pair_spread import normalize_pair_spread_symbol
from app.models.second_level_sampling import SUPPORTED_SECOND_LEVEL_EXCHANGES


DEFAULT_NEW_LISTING_EXCHANGES: tuple[str, ...] = (
    "bybit",
    "gate",
    "bitget",
    "okx",
    "binance",
)

NewListingAlertLevel = Literal["none", "normal", "strong", "extreme"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class NewListingWatchItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    enabled: bool = Field(default=True, description="是否启用这个新币极速监控标的。")
    symbol: str = Field(description="要监控的标的，例如 UNITREE 或 UNITREEUSDT。")
    market_type: MarketType = Field(default=MarketType.FUTURE, description="监控现货还是合约，第一版主要用于合约。")
    exchanges: list[str] = Field(
        default_factory=lambda: list(DEFAULT_NEW_LISTING_EXCHANGES),
        description="参与比较的交易所，至少需要两个。暂不支持的交易所会被拒绝保存。",
    )
    interval_seconds: float = Field(default=1.0, ge=1.0, le=60.0, description="采样周期，建议新币使用 1 秒。")
    retention_hours: float = Field(default=72.0, ge=1.0, le=720.0, description="秒级记录保留时长，默认 72 小时。")
    normal_threshold_pct: float = Field(default=3.0, ge=0.0, description="普通提醒净价差阈值。")
    strong_threshold_pct: float = Field(default=8.0, ge=0.0, description="强提醒净价差阈值。")
    extreme_threshold_pct: float = Field(default=15.0, ge=0.0, description="极端提醒净价差阈值。")
    min_executable_notional_usdt: float = Field(default=100.0, ge=0.0, description="已知盘口深度低于该金额时不提醒。")
    depth_validation_notional_usdt: float = Field(default=300.0, ge=0.0, description="用于页面提示的目标验证金额。")
    allow_low_liquidity_alert: bool = Field(default=True, description="新币低流动性仍然提醒，只打风险标签。")
    normal_consecutive_hits: int = Field(default=2, ge=1, le=20, description="普通提醒需要连续命中的次数。")
    strong_consecutive_hits: int = Field(default=1, ge=1, le=20, description="强提醒需要连续命中的次数。")
    extreme_consecutive_hits: int = Field(default=1, ge=1, le=20, description="极端提醒需要连续命中的次数。")
    cooldown_seconds: int = Field(default=60, ge=0, le=86_400, description="同一方向提醒后的冷却时间。")
    buy_fee_pct: float = Field(default=0.05, ge=0.0, le=10.0, description="买入侧手续费百分比。")
    sell_fee_pct: float = Field(default=0.05, ge=0.0, le=10.0, description="卖出侧手续费百分比。")
    slippage_buffer_pct: float = Field(default=0.10, ge=0.0, le=50.0, description="额外滑点缓冲百分比。")
    note: str = Field(default="", description="中文备注，例如上市时间、只做观察等。")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("标的不能为空")
        return normalize_pair_spread_symbol(value)

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
                raise ValueError(f"不支持的交易所: {item}; 可选: {allowed}")
            if exchange in seen:
                continue
            seen.add(exchange)
            normalized.append(exchange)
        if len(normalized) < 2:
            raise ValueError("新币极速监控至少需要两个交易所")
        return normalized

    @model_validator(mode="after")
    def validate_thresholds(self) -> "NewListingWatchItem":
        if self.strong_threshold_pct < self.normal_threshold_pct:
            raise ValueError("强提醒阈值不能低于普通提醒阈值")
        if self.extreme_threshold_pct < self.strong_threshold_pct:
            raise ValueError("极端提醒阈值不能低于强提醒阈值")
        return self


class NewListingSpreadSample(BaseModel):
    id: int | None = None
    watch_id: str
    observed_at: datetime
    symbol: str
    market_type: MarketType
    buy_exchange: str
    sell_exchange: str
    buy_bid: float | None = None
    buy_ask: float | None = None
    buy_bid_size: float | None = None
    buy_ask_size: float | None = None
    sell_bid: float | None = None
    sell_ask: float | None = None
    sell_bid_size: float | None = None
    sell_ask_size: float | None = None
    buy_price: float
    sell_price: float
    raw_spread_pct: float
    net_spread_pct: float
    executable_notional_usdt: float | None = None
    buy_latency_ms: float | None = None
    sell_latency_ms: float | None = None
    alert_level: NewListingAlertLevel = "none"
    alert_triggered: bool = False
    no_alert_reason: str | None = None
    risk_labels: list[str] = Field(default_factory=list)


class NewListingAlertEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    watch_id: str
    symbol: str
    market_type: MarketType
    level: NewListingAlertLevel
    buy_exchange: str
    sell_exchange: str
    net_spread_pct: float
    raw_spread_pct: float
    executable_notional_usdt: float | None = None
    message: str
    created_at: datetime = Field(default_factory=utc_now)


class NewListingMonitorStatus(BaseModel):
    running: bool
    watch_count: int
    enabled_watch_count: int
    sample_count: int
    event_count: int
    latest_error: str | None = None
    watchlist: list[NewListingWatchItem] = Field(default_factory=list)
    latest_samples: list[NewListingSpreadSample] = Field(default_factory=list)
    latest_events: list[NewListingAlertEvent] = Field(default_factory=list)


class NewListingHistoryResult(BaseModel):
    symbol: str | None = None
    watch_id: str | None = None
    start_at: datetime
    end_at: datetime
    sample_count: int
    event_count: int
    max_raw_spread_pct: float | None = None
    max_net_spread_pct: float | None = None
    max_sample: NewListingSpreadSample | None = None
    samples: list[NewListingSpreadSample] = Field(default_factory=list)
    events: list[NewListingAlertEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
