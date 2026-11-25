from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class PushKinds(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    near_open: bool
    near_close: bool
    fill: bool
    leg_alert: bool
    big_spread: bool
    stale: bool


class PushToken(BaseModel):
    token: SecretStr = Field(min_length=1, max_length=4096)


class PushRegistration(PushToken):
    kinds: PushKinds
    threshold_pp: float = Field(ge=0.01, le=5, strict=True)


class CardPinUpdate(BaseModel):
    card_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$", strict=True)
    pinned: bool = Field(strict=True)


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
