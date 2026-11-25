from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.models.pair_range_backtest import PairRangeBacktestResult


class PushKinds(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    near_open: bool
    near_close: bool
    fill: bool
    leg_alert: bool
    big_spread: bool
    stale: bool


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
