"""Signal-only 1-minute basis/premium detector.

The detector watches the sequence:

    basis shock -> basis compression -> low-basis entry -> re-expansion

It intentionally emits signal timestamps and the *next-minute planned
execution time*.  It does not pretend that the signal candle's close is the
actual fill price; a later backtester must use the next candle's open or an
order-book fill model.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

import pandas as pd
import requests


ALPHA_KLINES_URL = "https://www.binance.com/bapi/defi/v1/public/alpha-trade/klines"
FUTURES_KLINES_URL = "https://www.binance.com/fapi/v1/klines"
PREMIUM_KLINES_URL = "https://www.binance.com/fapi/v1/premiumIndexKlines"
MINUTE = pd.Timedelta(minutes=1)
MINUTE_MS = 60_000


@dataclass(frozen=True)
class MarketSpec:
    alpha_symbol: str
    futures_symbol: str


@dataclass(frozen=True)
class SignalConfig:
    shock_basis_bps: float = 120.0
    shock_velocity_5m_bps: float = 80.0
    shock_premium_bps: float = -25.0
    shock_premium_low_5m_bps: float = -70.0
    minimum_peak_bps: float = 120.0
    reset_basis_bps: float = 45.0
    reset_compression_ratio: float = 0.65
    reset_premium_low_15m_bps: float = -50.0
    entry_basis_bps: float = 45.0
    entry_premium_bps: float = -5.0
    entry_premium_low_15m_bps: float = -70.0
    take_profit_bps: float = 350.0
    stop_loss_bps: float = -120.0
    maximum_hold_minutes: int = 180
    shock_expiry_minutes: int = 180
    cooldown_minutes: int = 10


class State(str, Enum):
    IDLE = "idle"
    SHOCK = "shock"
    RESET_READY = "reset_ready"
    HOLD = "hold"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class SignalEvent:
    event_type: str
    state_before: str
    state_after: str
    signal_time_cst: str
    planned_execution_time_cst: str
    reason: str
    signal_basis_bps: float | None
    premium_bps: float | None
    premium_low_5m_bps: float | None
    premium_low_15m_bps: float | None
    basis_peak_60m_bps: float | None
    basis_drawdown_bps: float | None
    compression_ratio: float | None
    signal_entry_basis_bps: float | None = None
    signal_basis_gain_bps: float | None = None


def _ms(value: int | str | pd.Timestamp) -> int:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return int(timestamp.tz_convert("UTC").timestamp() * 1000)


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_klines(raw: list[list[Any]], prefix: str) -> pd.DataFrame:
    columns = ["open_time", "open", "high", "low", "close", "close_time"]
    if not raw:
        return pd.DataFrame(
            columns=["open_time", f"{prefix}_close_time", *[f"{prefix}_{c}" for c in columns[1:5]]]
        )
    rows: list[dict[str, Any]] = []
    for row in raw:
        if len(row) < 7:
            continue
        open_time = _finite(row[0])
        close_time = _finite(row[6])
        values = [_finite(row[index]) for index in range(1, 5)]
        if open_time is None or close_time is None or any(value is None for value in values):
            continue
        rows.append(
            {
                "open_time": int(open_time),
                f"{prefix}_close_time": int(close_time),
                **dict(zip((f"{prefix}_open", f"{prefix}_high", f"{prefix}_low", f"{prefix}_close"), values)),
            }
        )
    return pd.DataFrame(rows)


class BinanceMinuteClient:
    def __init__(self, session: requests.Session | None = None, timeout: int = 30) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if url == ALPHA_KLINES_URL:
            if not isinstance(payload, dict) or payload.get("success") is False:
                raise RuntimeError(f"Binance Alpha response is not successful: {payload!r}")
            payload = payload.get("data")
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected kline response from {url}: {payload!r}")
        return payload

    def fetch_klines(
        self,
        url: str,
        symbol: str,
        start: int | str | pd.Timestamp,
        end: int | str | pd.Timestamp,
    ) -> list[list[Any]]:
        start_ms, end_ms = _ms(start), _ms(end)
        cursor = start_ms
        result: dict[int, list[Any]] = {}
        while cursor <= end_ms:
            page = self._get(
                url,
                {
                    "symbol": symbol,
                    "interval": "1m",
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            if not page:
                break
            for row in page:
                open_time = _finite(row[0]) if row else None
                if open_time is not None and start_ms <= open_time <= end_ms:
                    result[int(open_time)] = row
            page_max = max(int(_finite(row[0])) for row in page if row and _finite(row[0]) is not None)
            next_cursor = page_max + MINUTE_MS
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return [result[key] for key in sorted(result)]

    def fetch_bundle(
        self,
        market: MarketSpec,
        start: int | str | pd.Timestamp,
        end: int | str | pd.Timestamp,
    ) -> pd.DataFrame:
        end_ms = _ms(end)
        spot = _parse_klines(self.fetch_klines(ALPHA_KLINES_URL, market.alpha_symbol, start, end), "spot")
        futures = _parse_klines(self.fetch_klines(FUTURES_KLINES_URL, market.futures_symbol, start, end), "fut")
        premium = _parse_klines(self.fetch_klines(PREMIUM_KLINES_URL, market.futures_symbol, start, end), "premium")
        frame = spot.merge(futures, on="open_time", how="inner", suffixes=("", "_fut"))
        frame = frame.merge(premium, on="open_time", how="inner", suffixes=("", "_premium"))
        if frame.empty:
            return frame
        frame = frame[
            (frame["spot_close_time"] <= end_ms)
            & (frame["fut_close_time"] <= end_ms)
            & (frame["premium_close_time"] <= end_ms)
        ].copy()
        frame["time_utc"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
        frame["time_cst"] = frame["time_utc"].dt.tz_convert("Asia/Shanghai")
        return build_features(frame)


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    result = frame.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True).copy()
    result["gap_before"] = result["open_time"].diff().fillna(MINUTE_MS).ne(MINUTE_MS)
    denominator = result["spot_close"].where(result["spot_close"] > 0)
    open_denominator = result["spot_open"].where(result["spot_open"] > 0)
    result["basis_bps"] = (result["spot_close"] - result["fut_close"]) / denominator * 10000.0
    result["basis_open_bps"] = (result["spot_open"] - result["fut_open"]) / open_denominator * 10000.0
    for column in ("premium_open", "premium_high", "premium_low", "premium_close"):
        result[f"{column}_bps"] = result[column] * 10000.0
    result["premium_bps"] = result["premium_close_bps"]

    time_index = pd.DatetimeIndex(result["time_utc"])
    basis_series = pd.Series(result["basis_bps"].to_numpy(), index=time_index)
    premium_low = pd.Series(result["premium_low_bps"].to_numpy(), index=time_index)
    premium_high = pd.Series(result["premium_high_bps"].to_numpy(), index=time_index)
    five_minutes_ago = time_index - pd.Timedelta(minutes=5)
    result["basis_change_5m_bps"] = (
        basis_series.to_numpy() - basis_series.reindex(five_minutes_ago).to_numpy()
    )
    result["basis_peak_60m_bps"] = basis_series.shift(1).rolling("60min", min_periods=15).max().to_numpy()
    result["premium_low_5m_bps"] = premium_low.rolling("5min", min_periods=1).min().to_numpy()
    result["premium_low_15m_bps"] = premium_low.rolling("15min", min_periods=1).min().to_numpy()
    result["premium_range_15m_bps"] = (
        premium_high.rolling("15min", min_periods=1).max()
        - premium_low.rolling("15min", min_periods=1).min()
    ).to_numpy()
    result["basis_drawdown_bps"] = result["basis_peak_60m_bps"] - result["basis_bps"]
    peak = result["basis_peak_60m_bps"].where(result["basis_peak_60m_bps"] > 0)
    result["compression_ratio"] = result["basis_drawdown_bps"] / peak
    return result.replace([float("inf"), float("-inf")], pd.NA)


class MinuteSignalEngine:
    def __init__(self, config: SignalConfig | None = None) -> None:
        self.config = config or SignalConfig()
        self.state = State.IDLE
        self.shock_time: pd.Timestamp | None = None
        self.entry_time: pd.Timestamp | None = None
        self.entry_basis: float | None = None
        self.cooldown_until: pd.Timestamp | None = None

    @staticmethod
    def _value(row: pd.Series, key: str) -> float | None:
        value = row.get(key)
        return _finite(value)

    def _shock(self, row: pd.Series) -> bool:
        c = self.config
        basis = self._value(row, "basis_bps")
        velocity = self._value(row, "basis_change_5m_bps")
        premium = self._value(row, "premium_bps")
        low = self._value(row, "premium_low_5m_bps")
        return bool(
            basis is not None
            and velocity is not None
            and premium is not None
            and low is not None
            and premium <= c.shock_premium_bps
            and low <= c.shock_premium_low_5m_bps
            and (basis >= c.shock_basis_bps or velocity >= c.shock_velocity_5m_bps)
        )

    def _reset(self, row: pd.Series) -> bool:
        c = self.config
        peak = self._value(row, "basis_peak_60m_bps")
        basis = self._value(row, "basis_bps")
        ratio = self._value(row, "compression_ratio")
        low = self._value(row, "premium_low_15m_bps")
        return bool(
            peak is not None
            and basis is not None
            and ratio is not None
            and low is not None
            and peak >= c.minimum_peak_bps
            and basis <= c.reset_basis_bps
            and ratio >= c.reset_compression_ratio
            and low <= c.reset_premium_low_15m_bps
        )

    def _entry(self, row: pd.Series) -> bool:
        c = self.config
        basis = self._value(row, "basis_bps")
        premium = self._value(row, "premium_bps")
        low = self._value(row, "premium_low_15m_bps")
        return bool(
            self._reset(row)
            and basis is not None
            and premium is not None
            and low is not None
            and basis <= c.entry_basis_bps
            and premium <= c.entry_premium_bps
            and low <= c.entry_premium_low_15m_bps
        )

    def _event(
        self,
        row: pd.Series,
        event_type: str,
        before: State,
        after: State,
        reason: str,
        gain: float | None = None,
    ) -> SignalEvent:
        time = pd.Timestamp(row["time_cst"])
        def rounded(key: str) -> float | None:
            value = self._value(row, key)
            return None if value is None else round(value, 6)
        return SignalEvent(
            event_type=event_type,
            state_before=before.value,
            state_after=after.value,
            signal_time_cst=time.strftime("%Y-%m-%d %H:%M"),
            planned_execution_time_cst=(time + MINUTE).strftime("%Y-%m-%d %H:%M"),
            reason=reason,
            signal_basis_bps=rounded("basis_bps"),
            premium_bps=rounded("premium_bps"),
            premium_low_5m_bps=rounded("premium_low_5m_bps"),
            premium_low_15m_bps=rounded("premium_low_15m_bps"),
            basis_peak_60m_bps=rounded("basis_peak_60m_bps"),
            basis_drawdown_bps=rounded("basis_drawdown_bps"),
            compression_ratio=rounded("compression_ratio"),
            signal_entry_basis_bps=None if self.entry_basis is None else round(self.entry_basis, 6),
            signal_basis_gain_bps=None if gain is None else round(gain, 6),
        )

    def _cooldown(self, execution_time: pd.Timestamp) -> None:
        self.state = State.COOLDOWN
        self.cooldown_until = execution_time + pd.Timedelta(minutes=self.config.cooldown_minutes)

    def _clear_position(self) -> None:
        self.shock_time = None
        self.entry_time = None
        self.entry_basis = None

    def on_bar(self, row: pd.Series) -> list[SignalEvent]:
        now = pd.Timestamp(row["time_cst"])
        events: list[SignalEvent] = []
        if self.state is State.COOLDOWN:
            if self.cooldown_until is not None and now < self.cooldown_until:
                return events
            self.state = State.IDLE
            self.cooldown_until = None

        if self.state is State.IDLE and self._shock(row):
            before = self.state
            self.state = State.SHOCK
            self.shock_time = now
            events.append(self._event(row, "SHOCK_ALERT", before, self.state, "basis_expansion_with_negative_premium"))
            return events

        if self.state is State.SHOCK:
            if self.shock_time is not None and (now - self.shock_time).total_seconds() / 60 > self.config.shock_expiry_minutes:
                self.state = State.IDLE
                self.shock_time = None
                if self._shock(row):
                    before = self.state
                    self.state = State.SHOCK
                    self.shock_time = now
                    events.append(self._event(row, "SHOCK_ALERT", before, self.state, "new_shock_after_expiry"))
                    return events
            if self._entry(row):
                before = self.state
                self.state = State.HOLD
                self.entry_time = now + MINUTE
                self.entry_basis = self._value(row, "basis_bps")
                events.append(self._event(row, "ENTRY", before, self.state, "shock_compressed_and_entry_confirmed"))
                return events
            if self._reset(row):
                self.state = State.RESET_READY

        if self.state is State.RESET_READY:
            if self._entry(row):
                before = self.state
                self.state = State.HOLD
                self.entry_time = now + MINUTE
                self.entry_basis = self._value(row, "basis_bps")
                events.append(self._event(row, "ENTRY", before, self.state, "reset_held_and_entry_confirmed"))
                return events
            if not self._reset(row):
                self.state = State.SHOCK
                self.shock_time = now

        if self.state is State.HOLD and self.entry_time is not None and self.entry_basis is not None:
            basis = self._value(row, "basis_bps")
            if basis is None:
                return events
            gain = basis - self.entry_basis
            held = int((now - self.entry_time).total_seconds() / 60)
            reason = None
            event_type = None
            if gain >= self.config.take_profit_bps:
                reason, event_type = "basis_converged", "TAKE_PROFIT"
            elif gain <= self.config.stop_loss_bps:
                reason, event_type = "basis_reversed", "STOP_LOSS"
            elif held >= self.config.maximum_hold_minutes:
                reason, event_type = "maximum_hold", "TIME_EXIT"
            if event_type is not None:
                before = self.state
                execution = now + MINUTE
                self._cooldown(execution)
                events.append(self._event(row, event_type, before, self.state, reason, gain))
                self._clear_position()
        return events

    def scan(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=[field.name for field in SignalEvent.__dataclass_fields__.values()])
        ordered = frame.sort_values("open_time").reset_index(drop=True)
        if not ordered["open_time"].is_monotonic_increasing:
            raise ValueError("minute frame must be sorted by open_time")
        events: list[dict[str, Any]] = []
        for _, row in ordered.iterrows():
            events.extend(asdict(event) for event in self.on_bar(row))
        return pd.DataFrame(events)


def scan_frame(frame: pd.DataFrame, config: SignalConfig | None = None) -> pd.DataFrame:
    return MinuteSignalEngine(config).scan(frame)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alpha-symbol", default="ALPHA_331USDT")
    parser.add_argument("--futures-symbol", default="AKEUSDT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    frame = BinanceMinuteClient().fetch_bundle(
        MarketSpec(args.alpha_symbol, args.futures_symbol), args.start, args.end
    )
    events = scan_frame(frame)
    records = events.astype(object).where(pd.notna(events), None).to_dict(orient="records")
    print(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
