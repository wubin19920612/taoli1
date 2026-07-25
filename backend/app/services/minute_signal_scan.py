from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

import httpx


ALPHA_KLINES_URL = "https://www.binance.com/bapi/defi/v1/public/alpha-trade/klines"
FUTURES_KLINES_URL = "https://www.binance.com/fapi/v1/klines"
PREMIUM_KLINES_URL = "https://www.binance.com/fapi/v1/premiumIndexKlines"
MINUTE_MS = 60_000
SHANGHAI = ZoneInfo("Asia/Shanghai")


class State(str, Enum):
    IDLE = "idle"
    SHOCK = "shock"
    RESET_READY = "reset_ready"
    HOLD = "hold"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class MinuteSignalConfig:
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


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _parse_kline(row: list[Any]) -> dict[str, Any] | None:
    if len(row) < 7:
        return None
    open_time = _finite(row[0])
    close_time = _finite(row[6])
    values = [_finite(row[index]) for index in range(1, 5)]
    if open_time is None or close_time is None or any(value is None for value in values):
        return None
    return {
        "open_time": int(open_time),
        "close_time": int(close_time),
        "open": values[0],
        "high": values[1],
        "low": values[2],
        "close": values[3],
    }


class MinuteSignalScanService:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
        config: MinuteSignalConfig | None = None,
    ) -> None:
        self._client = client
        self._owned_client = client is None
        self._timeout_seconds = timeout_seconds
        self.config = config or MinuteSignalConfig()

    async def _get(self, url: str, params: dict[str, Any]) -> list[list[Any]]:
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout_seconds, follow_redirects=True)
            self._client = client
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        if url == ALPHA_KLINES_URL:
            if not isinstance(payload, dict) or payload.get("success") is False:
                raise RuntimeError(f"Binance Alpha response is not successful: {payload!r}")
            payload = payload.get("data")
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected kline response from {url}")
        return payload

    async def _fetch_klines(
        self,
        url: str,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        cursor = start_ms
        rows: dict[int, dict[str, Any]] = {}
        while cursor <= end_ms:
            page = await self._get(
                url,
                {
                    "symbol": symbol,
                    "interval": "1m",
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            parsed = [item for row in page if (item := _parse_kline(row)) is not None]
            if not parsed:
                break
            for item in parsed:
                if start_ms <= item["open_time"] <= end_ms:
                    rows[item["open_time"]] = item
            page_max = max(item["open_time"] for item in parsed)
            next_cursor = page_max + MINUTE_MS
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return [rows[key] for key in sorted(rows)]

    async def _fetch_rows(
        self,
        alpha_symbol: str,
        futures_symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        spot, futures, premium = await asyncio.gather(
            self._fetch_klines(ALPHA_KLINES_URL, alpha_symbol, start_ms, end_ms),
            self._fetch_klines(FUTURES_KLINES_URL, futures_symbol, start_ms, end_ms),
            self._fetch_klines(PREMIUM_KLINES_URL, futures_symbol, start_ms, end_ms),
        )
        spot_by_time = {item["open_time"]: item for item in spot}
        futures_by_time = {item["open_time"]: item for item in futures}
        premium_by_time = {item["open_time"]: item for item in premium}
        rows: list[dict[str, Any]] = []
        for open_time in sorted(set(spot_by_time) & set(futures_by_time) & set(premium_by_time)):
            s = spot_by_time[open_time]
            f = futures_by_time[open_time]
            p = premium_by_time[open_time]
            if any(item["close_time"] > end_ms for item in (s, f, p)):
                continue
            rows.append(
                {
                    "open_time": open_time,
                    "time_cst": datetime.fromtimestamp(open_time / 1000, UTC)
                    .astimezone(SHANGHAI)
                    .isoformat(timespec="minutes"),
                    "spot_close": s["close"],
                    "fut_close": f["close"],
                    "spot_open": s["open"],
                    "fut_open": f["open"],
                    "premium_low": p["low"],
                    "premium_high": p["high"],
                    "premium_close": p["close"],
                }
            )
        return rows

    @staticmethod
    def _window(
        rows: list[dict[str, Any]],
        now: int,
        minutes: int,
        *,
        include_current: bool,
    ) -> list[dict[str, Any]]:
        start = now - minutes * MINUTE_MS
        return [
            row
            for row in rows
            if start <= row["open_time"] < now or (include_current and row["open_time"] == now)
        ]

    def _features(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        features: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            spot = row["spot_close"]
            spot_open = row["spot_open"]
            basis = (spot - row["fut_close"]) / spot * 10000 if spot > 0 else None
            basis_open = (
                (spot_open - row["fut_open"]) / spot_open * 10000 if spot_open > 0 else None
            )
            previous = rows[index - 1] if index else None
            gap_before = (
                previous is not None
                and row["open_time"] - previous["open_time"] != MINUTE_MS
            )
            previous_5m = next(
                (
                    item
                    for item in reversed(rows[:index])
                    if item["open_time"] == row["open_time"] - 5 * MINUTE_MS
                ),
                None,
            )
            previous_5m_basis = None
            if previous_5m is not None:
                previous_spot = previous_5m["spot_close"]
                previous_5m_basis = (
                    (previous_spot - previous_5m["fut_close"]) / previous_spot * 10000
                    if previous_spot > 0
                    else None
                )
            prior_window = self._window(rows[:index], row["open_time"], 60, include_current=False)
            premium_window_5 = self._window(
                rows[: index + 1],
                row["open_time"],
                5,
                include_current=True,
            )
            premium_window_15 = self._window(
                rows[: index + 1],
                row["open_time"],
                15,
                include_current=True,
            )
            peak = max(
                (
                    (item["spot_close"] - item["fut_close"]) / item["spot_close"] * 10000
                    for item in prior_window
                    if item["spot_close"] > 0
                ),
                default=None,
            )
            low5 = min((item["premium_low"] * 10000 for item in premium_window_5), default=None)
            low15 = min((item["premium_low"] * 10000 for item in premium_window_15), default=None)
            premium = row["premium_close"] * 10000
            drawdown = peak - basis if peak is not None and basis is not None else None
            compression = drawdown / peak if peak and drawdown is not None else None
            features.append(
                {
                    **row,
                    "gap_before": gap_before,
                    "basis_bps": basis,
                    "basis_open_bps": basis_open,
                    "basis_change_5m_bps": (
                        basis - previous_5m_basis
                        if basis is not None and previous_5m_basis is not None
                        else None
                    ),
                    "premium_bps": premium,
                    "premium_low_5m_bps": low5,
                    "premium_low_15m_bps": low15,
                    "basis_peak_60m_bps": peak,
                    "basis_drawdown_bps": drawdown,
                    "compression_ratio": compression,
                }
            )
        return features

    def _event(
        self,
        row: dict[str, Any],
        event_type: str,
        before: str,
        after: str,
        reason: str,
        entry_basis: float | None,
        gain: float | None,
    ) -> dict[str, Any]:
        signal_time = datetime.fromtimestamp(row["open_time"] / 1000, UTC)
        return {
            "event_type": event_type,
            "state_before": before,
            "state_after": after,
            "signal_time_cst": row["time_cst"],
            "planned_execution_time_cst": (
                signal_time + timedelta(minutes=1)
            ).astimezone(SHANGHAI).isoformat(timespec="minutes"),
            "reason": reason,
            "signal_basis_bps": row["basis_bps"],
            "premium_bps": row["premium_bps"],
            "premium_low_5m_bps": row["premium_low_5m_bps"],
            "premium_low_15m_bps": row["premium_low_15m_bps"],
            "basis_peak_60m_bps": row["basis_peak_60m_bps"],
            "basis_drawdown_bps": row["basis_drawdown_bps"],
            "compression_ratio": row["compression_ratio"],
            "signal_entry_basis_bps": entry_basis,
            "signal_basis_gain_bps": gain,
        }

    def _shock(self, row: dict[str, Any]) -> bool:
        c = self.config
        return bool(
            row["basis_bps"] is not None
            and row["basis_change_5m_bps"] is not None
            and row["premium_bps"] <= c.shock_premium_bps
            and row["premium_low_5m_bps"] <= c.shock_premium_low_5m_bps
            and (
                row["basis_bps"] >= c.shock_basis_bps
                or row["basis_change_5m_bps"] >= c.shock_velocity_5m_bps
            )
        )

    def _reset(self, row: dict[str, Any]) -> bool:
        c = self.config
        return bool(
            row["basis_peak_60m_bps"] is not None
            and row["basis_bps"] is not None
            and row["compression_ratio"] is not None
            and row["basis_peak_60m_bps"] >= c.minimum_peak_bps
            and row["basis_bps"] <= c.reset_basis_bps
            and row["compression_ratio"] >= c.reset_compression_ratio
            and row["premium_low_15m_bps"] <= c.reset_premium_low_15m_bps
        )

    def _entry(self, row: dict[str, Any]) -> bool:
        c = self.config
        return bool(
            self._reset(row)
            and row["basis_bps"] <= c.entry_basis_bps
            and row["premium_bps"] <= c.entry_premium_bps
            and row["premium_low_15m_bps"] <= c.entry_premium_low_15m_bps
        )

    def scan(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        state = State.IDLE.value
        shock_time: int | None = None
        entry_time: int | None = None
        entry_basis: float | None = None
        cooldown_until: int | None = None
        events: list[dict[str, Any]] = []
        for row in self._features(rows):
            now = row["open_time"]
            if state == State.COOLDOWN.value:
                if cooldown_until is not None and now < cooldown_until:
                    continue
                state, cooldown_until = State.IDLE.value, None
            if state == State.IDLE.value and self._shock(row):
                before = state
                state, shock_time = State.SHOCK.value, now
                events.append(
                    self._event(
                        row,
                        "SHOCK_ALERT",
                        before,
                        state,
                        "basis_expansion_with_negative_premium",
                        None,
                        None,
                    )
                )
                continue
            if state == State.SHOCK.value:
                if (
                    shock_time is not None
                    and now - shock_time > self.config.shock_expiry_minutes * MINUTE_MS
                ):
                    state, shock_time = State.IDLE.value, None
                    if self._shock(row):
                        before = state
                        state, shock_time = State.SHOCK.value, now
                        events.append(
                            self._event(
                                row,
                                "SHOCK_ALERT",
                                before,
                                state,
                                "new_shock_after_expiry",
                                None,
                                None,
                            )
                        )
                        continue
                if self._entry(row):
                    before = state
                    state, entry_time, entry_basis = (
                        State.HOLD.value,
                        now + MINUTE_MS,
                        row["basis_bps"],
                    )
                    events.append(
                        self._event(
                            row,
                            "ENTRY",
                            before,
                            state,
                            "shock_compressed_and_entry_confirmed",
                            entry_basis,
                            None,
                        )
                    )
                    continue
                if self._reset(row):
                    state = State.RESET_READY.value
            if state == State.RESET_READY.value:
                if self._entry(row):
                    before = state
                    state, entry_time, entry_basis = (
                        State.HOLD.value,
                        now + MINUTE_MS,
                        row["basis_bps"],
                    )
                    events.append(
                        self._event(
                            row,
                            "ENTRY",
                            before,
                            state,
                            "reset_held_and_entry_confirmed",
                            entry_basis,
                            None,
                        )
                    )
                    continue
                if not self._reset(row):
                    state, shock_time = State.SHOCK.value, now
            if state == State.HOLD.value and entry_time is not None and entry_basis is not None:
                basis = row["basis_bps"]
                if basis is None:
                    continue
                gain = basis - entry_basis
                held = int((now - entry_time) / MINUTE_MS)
                exit_type = (
                    ("TAKE_PROFIT", "basis_converged")
                    if gain >= self.config.take_profit_bps
                    else ("STOP_LOSS", "basis_reversed")
                    if gain <= self.config.stop_loss_bps
                    else ("TIME_EXIT", "maximum_hold")
                    if held >= self.config.maximum_hold_minutes
                    else None
                )
                if exit_type is not None:
                    before = state
                    execution_time = now + MINUTE_MS
                    state = State.COOLDOWN.value
                    cooldown_until = execution_time + self.config.cooldown_minutes * MINUTE_MS
                    events.append(
                        self._event(
                            row,
                            exit_type[0],
                            before,
                            state,
                            exit_type[1],
                            entry_basis,
                            gain,
                        )
                    )
                    shock_time = entry_time = entry_basis = None
        return events

    async def scan_symbol(
        self,
        *,
        alpha_symbol: str,
        futures_symbol: str,
        hours: int,
    ) -> dict[str, Any]:
        end = datetime.now(UTC).replace(second=0, microsecond=0)
        start = end - timedelta(hours=hours)
        rows = await self._fetch_rows(
            alpha_symbol,
            futures_symbol,
            _timestamp_ms(start),
            _timestamp_ms(end),
        )
        features = self._features(rows)
        events = self.scan(rows)
        return {
            "alpha_symbol": alpha_symbol,
            "futures_symbol": futures_symbol,
            "hours": hours,
            "observed_at": end.isoformat(),
            "bar_count": len(features),
            "latest": features[-1] if features else None,
            "points": [
                {
                    "time_cst": item["time_cst"],
                    "basis_bps": item["basis_bps"],
                    "premium_bps": item["premium_bps"],
                    "basis_peak_60m_bps": item["basis_peak_60m_bps"],
                    "compression_ratio": item["compression_ratio"],
                }
                for item in features[-240:]
            ],
            "events": events[-100:],
            "warnings": [
                (
                    "Signal-only: planned_execution_time_cst is the next-minute execution "
                    "boundary; no fill/slippage is modeled."
                ),
                (
                    "Alpha spot and Binance futures are separate venues/market sources; "
                    "verify liquidity before acting."
                ),
            ],
        }

    async def aclose(self) -> None:
        if self._owned_client and self._client is not None:
            await self._client.aclose()
            self._client = None
