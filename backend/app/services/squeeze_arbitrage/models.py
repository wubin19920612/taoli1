from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

RULE_VERSION = "squeeze-watch-s1-v1"
HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class HourCandle:
    market_key: str
    event_time: datetime  # End of a completed UTC hour.
    close: float
    quote_volume: float
    received_at: datetime
    available_at: datetime
    source: str = "binance_fapi_klines_1h"


@dataclass(frozen=True)
class PositionSample:
    market_key: str
    event_time: datetime
    raw_open_interest: float
    raw_unit: str
    open_interest_usdt: float | None
    account_ratio: float | None
    received_at: datetime
    available_at: datetime
    source: str = "binance_openInterestHist_1h"
    sampling_period_seconds: int = 3600
    account_ratio_event_time: datetime | None = None


@dataclass(frozen=True)
class WatchFeatures:
    market_key: str
    bucket_at: datetime
    calculated_at: datetime
    status: Literal["ready", "insufficient_data", "stale_data"]
    reasons: tuple[str, ...] = ()
    return_4h: float | None = None
    return_24h: float | None = None
    volume_ratio: float | None = None
    account_ratio: float | None = None
    account_ratio_change: float | None = None
    oi_current_growth: float | None = None
    oi_peak_growth: float | None = None
    oi_drawdown: float | None = None
    oi_age_seconds: float | None = None
    account_age_seconds: float | None = None

    @property
    def qualifies(self) -> bool:
        return (
            self.status == "ready"
            and (self.return_4h >= 0.15 or self.return_24h >= 0.30)
            and self.volume_ratio >= 5
            and self.account_ratio <= 0.85
            and self.oi_peak_growth >= 0.08
        )

    @property
    def stage(self) -> str:
        if self.status != "ready":
            return "insufficient_data"
        if self.oi_drawdown <= -0.10 and (self.return_4h > 0 or self.return_24h > 0):
            return "squeeze_pending"
        if self.return_4h <= 0:
            return "tail_risk"
        return "building"


def market_key(raw_symbol: str, *, exchange: str = "binance", dex: str = "") -> str:
    return f"{exchange}|future|{raw_symbol}|{dex}"


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
