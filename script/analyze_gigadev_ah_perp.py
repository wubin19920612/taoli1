"""Compare GigaDevice A/H premium with Hyperliquid/Binance perpetual premium.

The script intentionally reports both the symmetric spread used by this project
and the conventional price-ratio premium. All timestamps in exported files are
Asia/Shanghai minutes.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


TZ = ZoneInfo("Asia/Shanghai")
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
BINANCE_FAPI_URL = "https://fapi.binance.com"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
ONE_MINUTE_MS = 60_000


@dataclass(frozen=True)
class EventDefinition:
    name: str
    entry_time: str
    exit_time: str
    exit_next_trading_day: bool


EVENTS = (
    EventDefinition("a_lunch_to_pm_reopen", "11:59", "13:05", False),
    EventDefinition("a_close_to_hk_close", "15:01", "16:09", False),
    EventDefinition("hk_close_to_next_open", "16:09", "09:48", True),
    EventDefinition("evening_1822_to_next_open", "18:22", "09:48", True),
    EventDefinition("late_2105_to_next_open", "21:05", "09:48", True),
)


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = client.request(method, url, params=params, json=payload)
            response.raise_for_status()
            result = response.json()
            if isinstance(result, str):
                result = json.loads(result)
            return result
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 4:
                break
            time.sleep(0.7 * (attempt + 1))
    raise RuntimeError(f"request failed after retries: {url}") from last_error


def fetch_yahoo_minutes(client: httpx.Client, symbol: str, column: str) -> pd.DataFrame:
    payload = request_json(
        client,
        "GET",
        YAHOO_CHART_URL.format(symbol=symbol),
        params={
            "range": "5d",
            "interval": "1m",
            "events": "history",
            "includePrePost": "false",
        },
    )
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(TZ),
            column: quote.get("close", []),
        }
    )
    if len(quote.get("volume", [])) == len(frame):
        frame[f"{column}_volume"] = quote["volume"]
    return (
        frame.dropna(subset=[column])
        .drop_duplicates("timestamp", keep="last")
        .set_index("timestamp")
        .sort_index()
    )


def fetch_binance_minutes(
    client: httpx.Client, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    cursor = int(start.tz_convert("UTC").timestamp() * 1000)
    end_ms = int(end.tz_convert("UTC").timestamp() * 1000)
    rows: list[list[Any]] = []
    while cursor <= end_ms:
        batch = request_json(
            client,
            "GET",
            f"{BINANCE_FAPI_URL}/fapi/v1/klines",
            params={
                "symbol": "GIGADEVUSDT",
                "interval": "1m",
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1500,
            },
        )
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + ONE_MINUTE_MS
        if next_cursor <= cursor:
            break
        cursor = next_cursor
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [row[0] for row in rows], unit="ms", utc=True
            ).tz_convert(TZ),
            "binance_close": [float(row[4]) for row in rows],
            "binance_base_volume": [float(row[5]) for row in rows],
            "binance_quote_volume": [float(row[7]) for row in rows],
            "binance_trades": [int(row[8]) for row in rows],
        }
    )
    return frame.drop_duplicates("timestamp", keep="last").set_index("timestamp").sort_index()


def fetch_hyperliquid_minutes(
    client: httpx.Client, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    current_date = start.date()
    while current_date <= end.date():
        day_start = pd.Timestamp(datetime.combine(current_date, datetime_time.min), tz=TZ)
        day_end = day_start + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
        batch = request_json(
            client,
            "POST",
            HYPERLIQUID_INFO_URL,
            payload={
                "type": "candleSnapshot",
                "req": {
                    "coin": "xyz:GIGADEV",
                    "interval": "1m",
                    "startTime": int(day_start.tz_convert("UTC").timestamp() * 1000),
                    "endTime": int(day_end.tz_convert("UTC").timestamp() * 1000),
                },
            },
        )
        if isinstance(batch, list):
            rows.extend(row for row in batch if isinstance(row, dict))
        current_date += timedelta(days=1)
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [row["t"] for row in rows], unit="ms", utc=True
            ).tz_convert(TZ),
            "hyper_close": [float(row["c"]) for row in rows],
            "hyper_base_volume": [float(row.get("v", 0)) for row in rows],
            "hyper_trades": [int(row.get("n", 0)) for row in rows],
        }
    )
    return frame.drop_duplicates("timestamp", keep="last").set_index("timestamp").sort_index()


def fetch_funding(
    client: httpx.Client, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    start_ms = int(start.tz_convert("UTC").timestamp() * 1000)
    end_ms = int(end.tz_convert("UTC").timestamp() * 1000)
    binance = request_json(
        client,
        "GET",
        f"{BINANCE_FAPI_URL}/fapi/v1/fundingRate",
        params={
            "symbol": "GIGADEVUSDT",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        },
    )
    hyper = request_json(
        client,
        "POST",
        HYPERLIQUID_INFO_URL,
        payload={
            "type": "fundingHistory",
            "coin": "xyz:GIGADEV",
            "startTime": start_ms,
            "endTime": end_ms,
        },
    )
    frames: list[pd.DataFrame] = []
    if binance:
        frames.append(
            pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(
                        [row["fundingTime"] for row in binance], unit="ms", utc=True
                    ).tz_convert(TZ),
                    "exchange": "binance",
                    "funding_rate": [float(row["fundingRate"]) for row in binance],
                }
            )
        )
    if hyper:
        frames.append(
            pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(
                        [row["time"] for row in hyper], unit="ms", utc=True
                    ).tz_convert(TZ),
                    "exchange": "hyperliquid",
                    "funding_rate": [float(row["fundingRate"]) for row in hyper],
                }
            )
        )
    return pd.concat(frames, ignore_index=True).sort_values("timestamp")


def minute_of_day(index: pd.DatetimeIndex) -> np.ndarray:
    return index.hour.to_numpy() * 60 + index.minute.to_numpy()


def a_share_active(index: pd.DatetimeIndex) -> np.ndarray:
    minute = minute_of_day(index)
    weekday = index.weekday < 5
    return weekday & (((minute >= 570) & (minute <= 690)) | ((minute >= 780) & (minute <= 900)))


def h_share_active(index: pd.DatetimeIndex) -> np.ndarray:
    minute = minute_of_day(index)
    weekday = index.weekday < 5
    return weekday & (((minute >= 570) & (minute <= 720)) | ((minute >= 780) & (minute <= 969)))


def regime_for(index: pd.DatetimeIndex) -> pd.Series:
    minute = minute_of_day(index)
    weekday = index.weekday < 5
    result = np.full(len(index), "weekend", dtype=object)
    result[weekday & (minute < 570)] = "overnight_preopen"
    result[weekday & (minute >= 570) & (minute <= 690)] = "overlap_am"
    result[weekday & (minute >= 691) & (minute <= 720)] = "a_lunch_h_open"
    result[weekday & (minute >= 721) & (minute < 780)] = "both_lunch"
    result[weekday & (minute >= 780) & (minute <= 900)] = "overlap_pm"
    result[weekday & (minute >= 901) & (minute <= 969)] = "a_closed_h_open"
    result[weekday & (minute >= 970)] = "both_closed_evening"
    return pd.Series(result, index=index)


def effective_spot_series(
    raw: pd.Series, grid: pd.DatetimeIndex, active: np.ndarray
) -> tuple[pd.Series, pd.Series, pd.Series]:
    observed = raw.reindex(grid).where(active)
    effective = observed.copy()
    observed_at = pd.Series(pd.NaT, index=grid, dtype=f"datetime64[ns, {TZ.key}]")
    observed_at.loc[observed.notna()] = grid[observed.notna()]
    last_prior_at = raw.loc[raw.index < grid[0]].last_valid_index()
    if last_prior_at is not None and pd.isna(effective.iloc[0]):
        effective.iloc[0] = raw.loc[last_prior_at]
        observed_at.iloc[0] = last_prior_at
    effective = effective.ffill()
    observed_at = observed_at.ffill()
    stale_minutes = (pd.Series(grid, index=grid) - observed_at).dt.total_seconds() / 60
    return observed, effective, stale_minutes


def symmetric_spread(left: pd.Series, right: pd.Series) -> pd.Series:
    return (right - left) / ((left + right) / 2) * 100


def build_minute_frame(
    a_share: pd.DataFrame,
    h_share: pd.DataFrame,
    usd_cny: pd.DataFrame,
    usd_hkd: pd.DataFrame,
    binance: pd.DataFrame,
    hyper: pd.DataFrame,
) -> pd.DataFrame:
    start = max(binance.index.min(), hyper.index.min()).ceil("min")
    end = min(binance.index.max(), hyper.index.max()).floor("min")
    grid = pd.date_range(start, end, freq="1min", tz=TZ)
    result = pd.DataFrame(index=grid)
    result.index.name = "timestamp"
    result = result.join(binance.reindex(grid)).join(hyper.reindex(grid))

    a_active = a_share_active(grid)
    h_active = h_share_active(grid)
    a_observed, result["a_cny"], result["a_stale_minutes"] = effective_spot_series(
        a_share["a_cny"], grid, a_active
    )
    h_observed, result["h_hkd"], result["h_stale_minutes"] = effective_spot_series(
        h_share["h_hkd"], grid, h_active
    )
    result["a_observed_this_minute"] = a_observed.notna()
    result["h_observed_this_minute"] = h_observed.notna()
    result["a_market_active"] = a_active
    result["h_market_active"] = h_active

    result["usd_cny"] = usd_cny["usd_cny"].reindex(grid, method="ffill", tolerance="15min")
    result["usd_hkd"] = usd_hkd["usd_hkd"].reindex(grid, method="ffill", tolerance="15min")
    result["a_usd"] = result["a_cny"] / result["usd_cny"]
    result["h_usd"] = result["h_hkd"] / result["usd_hkd"]

    # One H share and one A share represent the same economic interest; no ADR ratio applies.
    result["spot_ah_ratio_pct"] = (result["a_usd"] / result["h_usd"] - 1) * 100
    # Exchange metadata and index constituents show Hyper is A-linked while
    # Binance is H-linked. Keep both spreads in the same A-minus-H direction.
    result["perp_ah_ratio_pct"] = (
        result["hyper_close"] / result["binance_close"] - 1
    ) * 100
    result["difference_ratio_pp"] = (
        result["perp_ah_ratio_pct"] - result["spot_ah_ratio_pct"]
    )
    result["spot_ah_symmetric_pct"] = symmetric_spread(result["h_usd"], result["a_usd"])
    result["perp_ah_symmetric_pct"] = symmetric_spread(
        result["binance_close"], result["hyper_close"]
    )
    result["difference_symmetric_pp"] = (
        result["perp_ah_symmetric_pct"] - result["spot_ah_symmetric_pct"]
    )
    result["hyper_vs_a_tracking_pct"] = (result["hyper_close"] / result["a_usd"] - 1) * 100
    result["hyper_vs_h_tracking_pct"] = (result["hyper_close"] / result["h_usd"] - 1) * 100
    result["binance_vs_h_tracking_pct"] = (
        result["binance_close"] / result["h_usd"] - 1
    ) * 100
    result["binance_vs_a_tracking_pct"] = (
        result["binance_close"] / result["a_usd"] - 1
    ) * 100
    result["regime"] = regime_for(grid)
    result["strict_same_minute"] = (
        result["a_market_active"]
        & result["h_market_active"]
        & result["a_observed_this_minute"]
        & result["h_observed_this_minute"]
        & result[["binance_close", "hyper_close", "usd_cny", "usd_hkd"]].notna().all(axis=1)
    )
    return result


def describe(values: pd.Series, prefix: str = "") -> dict[str, float | int]:
    clean = values.dropna()
    if clean.empty:
        return {f"{prefix}minutes": 0}
    return {
        f"{prefix}minutes": int(len(clean)),
        f"{prefix}mean": float(clean.mean()),
        f"{prefix}std": float(clean.std(ddof=1)) if len(clean) > 1 else math.nan,
        f"{prefix}p05": float(clean.quantile(0.05)),
        f"{prefix}p25": float(clean.quantile(0.25)),
        f"{prefix}median": float(clean.median()),
        f"{prefix}p75": float(clean.quantile(0.75)),
        f"{prefix}p95": float(clean.quantile(0.95)),
        f"{prefix}min": float(clean.min()),
        f"{prefix}max": float(clean.max()),
    }


def lag_properties(frame: pd.DataFrame) -> tuple[float, float]:
    pairs: list[pd.DataFrame] = []
    for _, group in frame.groupby(frame.index.date):
        values = group["difference_symmetric_pp"].dropna()
        if len(values) > 2:
            pairs.append(
                pd.DataFrame(
                    {
                        "current": values.iloc[1:].to_numpy(),
                        "lag": values.iloc[:-1].to_numpy(),
                    }
                )
            )
    if not pairs:
        return math.nan, math.nan
    joined = pd.concat(pairs, ignore_index=True)
    lag = joined["lag"].to_numpy()
    current = joined["current"].to_numpy()
    denominator = float(np.sum((lag - lag.mean()) ** 2))
    if denominator == 0:
        return math.nan, math.nan
    phi = float(np.sum((lag - lag.mean()) * (current - current.mean())) / denominator)
    half_life = -math.log(2) / math.log(phi) if 0 < phi < 1 else math.nan
    return phi, half_life


def daily_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    strict = frame.loc[frame["strict_same_minute"]]
    for trading_date, group in strict.groupby(strict.index.date):
        row: dict[str, Any] = {"trading_date": trading_date.isoformat()}
        row.update(describe(group["spot_ah_symmetric_pct"], "spot_sym_"))
        row.update(describe(group["perp_ah_symmetric_pct"], "perp_sym_"))
        row.update(describe(group["difference_symmetric_pp"], "difference_sym_"))
        row["level_correlation"] = group[
            ["spot_ah_symmetric_pct", "perp_ah_symmetric_pct"]
        ].corr().iloc[0, 1]
        row["change_correlation"] = group[
            ["spot_ah_symmetric_pct", "perp_ah_symmetric_pct"]
        ].diff().corr().iloc[0, 1]
        row["hyper_trade_minute_pct"] = float((group["hyper_trades"] > 0).mean() * 100)
        rows.append(row)
    return pd.DataFrame(rows)


def overall_strict_summary(frame: pd.DataFrame) -> pd.DataFrame:
    strict = frame.loc[frame["strict_same_minute"]]
    difference = strict["difference_symmetric_pp"].dropna()
    row: dict[str, Any] = {
        "independent_trading_days": int(pd.Series(strict.index.date).nunique()),
        "abs_difference_le_0_5_pct": float((difference.abs() <= 0.5).mean() * 100),
        "abs_difference_le_1_0_pct": float((difference.abs() <= 1.0).mean() * 100),
        "hyper_trade_minute_pct": float((strict["hyper_trades"] > 0).mean() * 100),
    }
    row.update(describe(difference, "difference_sym_"))
    return pd.DataFrame([row])


def window_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    valid = frame.dropna(subset=["difference_symmetric_pp"])
    for regime, group in valid.groupby("regime", sort=False):
        if regime == "weekend":
            continue
        row: dict[str, Any] = {
            "regime": regime,
            "trading_dates": int(pd.Series(group.index.date).nunique()),
        }
        row.update(describe(group["difference_symmetric_pp"], "difference_sym_"))
        row.update(describe(group["spot_ah_symmetric_pct"], "spot_sym_"))
        row.update(describe(group["perp_ah_symmetric_pct"], "perp_sym_"))
        row["hyper_trade_minute_pct"] = float((group["hyper_trades"] > 0).mean() * 100)
        phi, half_life = lag_properties(group)
        row["difference_lag1_phi"] = phi
        row["estimated_half_life_minutes"] = half_life
        rows.append(row)
    return pd.DataFrame(rows)


def tracking_summary(frame: pd.DataFrame) -> pd.DataFrame:
    strict = frame.loc[frame["strict_same_minute"]]
    definitions = (
        ("hyper_to_a_share", "hyper_close", "a_usd", "hyper_vs_a_tracking_pct"),
        ("hyper_to_h_share", "hyper_close", "h_usd", "hyper_vs_h_tracking_pct"),
        ("binance_to_h_share", "binance_close", "h_usd", "binance_vs_h_tracking_pct"),
        ("binance_to_a_share", "binance_close", "a_usd", "binance_vs_a_tracking_pct"),
    )
    rows: list[dict[str, Any]] = []
    for name, perp_column, spot_column, error_column in definitions:
        sample = strict[[perp_column, spot_column, error_column]].dropna()
        rows.append(
            {
                "mapping": name,
                "minutes": len(sample),
                "mean_tracking_error_pct": sample[error_column].mean(),
                "mean_absolute_tracking_error_pct": sample[error_column].abs().mean(),
                "tracking_error_std_pct": sample[error_column].std(ddof=1),
                "price_level_correlation": sample[[perp_column, spot_column]].corr().iloc[0, 1],
            }
        )
    return pd.DataFrame(rows)


def value_at(frame: pd.DataFrame, target_date: date, clock: str, column: str) -> float:
    target = pd.Timestamp(f"{target_date.isoformat()} {clock}", tz=TZ)
    if target not in frame.index:
        return math.nan
    value = frame.at[target, column]
    return float(value) if pd.notna(value) else math.nan


def event_summary(frame: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    stock_dates = sorted(
        {
            timestamp.date()
            for timestamp, value in frame["a_cny"].items()
            if timestamp.weekday() < 5 and pd.notna(value)
        }
    )
    next_date = {stock_dates[i]: stock_dates[i + 1] for i in range(len(stock_dates) - 1)}
    rows: list[dict[str, Any]] = []
    for entry_date in stock_dates:
        for definition in EVENTS:
            exit_date = (
                next_date.get(entry_date) if definition.exit_next_trading_day else entry_date
            )
            if exit_date is None:
                continue
            entry_at = pd.Timestamp(f"{entry_date.isoformat()} {definition.entry_time}", tz=TZ)
            exit_at = pd.Timestamp(f"{exit_date.isoformat()} {definition.exit_time}", tz=TZ)
            if entry_at not in frame.index or exit_at not in frame.index:
                continue
            entry_d = value_at(frame, entry_date, definition.entry_time, "difference_symmetric_pp")
            exit_d = value_at(frame, exit_date, definition.exit_time, "difference_symmetric_pp")
            entry_perp = value_at(frame, entry_date, definition.entry_time, "perp_ah_symmetric_pct")
            exit_perp = value_at(frame, exit_date, definition.exit_time, "perp_ah_symmetric_pct")
            if not all(math.isfinite(value) for value in (entry_d, exit_d, entry_perp, exit_perp)):
                continue
            direction = 1 if entry_d > 0 else -1
            entry_hyper = float(frame.at[entry_at, "hyper_close"])
            exit_hyper = float(frame.at[exit_at, "hyper_close"])
            entry_binance = float(frame.at[entry_at, "binance_close"])
            exit_binance = float(frame.at[exit_at, "binance_close"])
            average_entry_price = (entry_hyper + entry_binance) / 2
            price_return_pp = (
                direction
                * (
                    (entry_hyper - exit_hyper)
                    + (exit_binance - entry_binance)
                )
                / average_entry_price
                * 100
            )
            path = frame.loc[entry_at:exit_at, ["hyper_close", "binance_close"]].dropna()
            path_return = (
                direction
                * (
                    (entry_hyper - path["hyper_close"])
                    + (path["binance_close"] - entry_binance)
                )
                / average_entry_price
                * 100
            )
            relevant_funding = funding.loc[
                (funding["timestamp"] > entry_at) & (funding["timestamp"] <= exit_at)
            ]
            hyper_sum = relevant_funding.loc[
                relevant_funding["exchange"] == "hyperliquid", "funding_rate"
            ].sum()
            binance_sum = relevant_funding.loc[
                relevant_funding["exchange"] == "binance", "funding_rate"
            ].sum()
            # direction=1 means short Hyper and long Binance. Positive funding is paid by longs.
            funding_return_pp = (
                direction
                * (entry_hyper * hyper_sum - entry_binance * binance_sum)
                / average_entry_price
                * 100
            )
            after_funding = price_return_pp + funding_return_pp
            rows.append(
                {
                    "event": definition.name,
                    "entry_at": entry_at.isoformat(),
                    "exit_at": exit_at.isoformat(),
                    "entry_difference_sym_pp": entry_d,
                    "exit_difference_sym_pp": exit_d,
                    "abs_difference_convergence_pp": abs(entry_d) - abs(exit_d),
                    "difference_converged": abs(exit_d) < abs(entry_d),
                    "trade_direction": (
                        "short_hyper_long_binance" if direction == 1 else "long_hyper_short_binance"
                    ),
                    "entry_perp_sym_pct": entry_perp,
                    "exit_perp_sym_pct": exit_perp,
                    "perp_spread_gross_move_pp": direction * (entry_perp - exit_perp),
                    "equal_quantity_price_return_pct": price_return_pp,
                    "max_favorable_price_return_pct": float(path_return.max()),
                    "max_adverse_price_return_pct": float(path_return.min()),
                    "funding_return_pp_approx": float(funding_return_pp),
                    "price_plus_funding_pct_approx": float(after_funding),
                    "after_funding_and_10bp_fees_pct_approx": float(after_funding - 0.10),
                }
            )
    return pd.DataFrame(rows)


def event_strategy_summary(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event, group in events.groupby("event", sort=False):
        rows.append(
            {
                "event": event,
                "independent_events": len(group),
                "difference_convergence_rate_pct": group["difference_converged"].mean() * 100,
                "price_win_rate_pct": (group["equal_quantity_price_return_pct"] > 0).mean() * 100,
                "after_funding_and_10bp_fees_win_rate_pct": (
                    group["after_funding_and_10bp_fees_pct_approx"] > 0
                ).mean()
                * 100,
                "mean_price_return_pct": group["equal_quantity_price_return_pct"].mean(),
                "mean_after_funding_and_10bp_fees_pct": group[
                    "after_funding_and_10bp_fees_pct_approx"
                ].mean(),
                "minimum_after_funding_and_10bp_fees_pct": group[
                    "after_funding_and_10bp_fees_pct_approx"
                ].min(),
                "maximum_after_funding_and_10bp_fees_pct": group[
                    "after_funding_and_10bp_fees_pct_approx"
                ].max(),
                "worst_intraperiod_price_return_pct": group[
                    "max_adverse_price_return_pct"
                ].min(),
            }
        )
    return pd.DataFrame(rows)


def fixed_time_summary(frame: pd.DataFrame) -> pd.DataFrame:
    clocks = (
        "09:30",
        "09:48",
        "10:00",
        "11:30",
        "11:59",
        "13:00",
        "15:00",
        "16:09",
        "18:22",
        "21:05",
    )
    dates = sorted(
        {
            timestamp.date()
            for timestamp, value in frame["a_cny"].items()
            if timestamp.weekday() < 5 and pd.notna(value)
        }
    )
    rows: list[dict[str, Any]] = []
    for trading_date in dates:
        for clock in clocks:
            target = pd.Timestamp(f"{trading_date.isoformat()} {clock}", tz=TZ)
            if target not in frame.index:
                continue
            row = frame.loc[target]
            if pd.isna(row["difference_symmetric_pp"]):
                continue
            rows.append(
                {
                    "timestamp": target.isoformat(),
                    "regime": row["regime"],
                    "spot_ah_symmetric_pct": row["spot_ah_symmetric_pct"],
                    "perp_ah_symmetric_pct": row["perp_ah_symmetric_pct"],
                    "difference_symmetric_pp": row["difference_symmetric_pp"],
                    "spot_ah_ratio_pct": row["spot_ah_ratio_pct"],
                    "perp_ah_ratio_pct": row["perp_ah_ratio_pct"],
                    "difference_ratio_pp": row["difference_ratio_pp"],
                    "hyper_trades": row["hyper_trades"],
                }
            )
    return pd.DataFrame(rows)


def save_chart(frame: pd.DataFrame, path: Path) -> None:
    plot = frame.dropna(subset=["difference_symmetric_pp"])
    fig, axes = plt.subplots(2, 1, figsize=(16, 9), sharex=True)
    axes[0].plot(
        plot.index,
        plot["spot_ah_symmetric_pct"],
        label="A/H spot symmetric premium",
        linewidth=1,
    )
    axes[0].plot(
        plot.index,
        plot["perp_ah_symmetric_pct"],
        label="Hyper/Binance symmetric premium",
        linewidth=1,
    )
    axes[0].axhline(0, color="black", linewidth=0.6)
    axes[0].set_ylabel("Percent")
    axes[0].legend(loc="best")
    axes[0].grid(alpha=0.2)
    axes[1].plot(plot.index, plot["difference_symmetric_pp"], color="#b42318", linewidth=1)
    axes[1].axhline(0, color="black", linewidth=0.6)
    axes[1].set_ylabel("Difference (percentage points)")
    axes[1].grid(alpha=0.2)
    fig.suptitle("GigaDevice: perpetual premium minus A/H spot premium")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/gigadev_ah_perp_minute"),
        help="Directory for CSV and PNG artifacts",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 taoli1-gigadev-research/1.0"}
    with httpx.Client(headers=headers, timeout=45.0, follow_redirects=True) as client:
        a_share = fetch_yahoo_minutes(client, "603986.SS", "a_cny")
        h_share = fetch_yahoo_minutes(client, "3986.HK", "h_hkd")
        usd_cny = fetch_yahoo_minutes(client, "CNY=X", "usd_cny")
        usd_hkd = fetch_yahoo_minutes(client, "HKD=X", "usd_hkd")
        first_date = min(a_share.index.min(), h_share.index.min()).normalize()
        last_date = max(a_share.index.max(), h_share.index.max()).normalize() + pd.Timedelta(days=1)
        now = pd.Timestamp.now(tz=TZ).floor("min")
        end = min(last_date, now)
        binance = fetch_binance_minutes(client, first_date, end)
        hyper = fetch_hyperliquid_minutes(client, first_date, end)
        funding = fetch_funding(client, first_date, end)

    minute = build_minute_frame(a_share, h_share, usd_cny, usd_hkd, binance, hyper)
    daily = daily_summary(minute)
    overall = overall_strict_summary(minute)
    windows = window_summary(minute)
    events = event_summary(minute, funding)
    event_strategies = event_strategy_summary(events)
    fixed = fixed_time_summary(minute)
    tracking = tracking_summary(minute)

    minute.to_csv(args.output / "gigadev_minute_spreads.csv", float_format="%.8f")
    daily.to_csv(args.output / "daily_strict_summary.csv", index=False, float_format="%.8f")
    overall.to_csv(args.output / "overall_strict_summary.csv", index=False, float_format="%.8f")
    windows.to_csv(args.output / "regime_summary.csv", index=False, float_format="%.8f")
    events.to_csv(args.output / "convergence_events.csv", index=False, float_format="%.8f")
    event_strategies.to_csv(
        args.output / "event_strategy_summary.csv", index=False, float_format="%.8f"
    )
    fixed.to_csv(args.output / "fixed_time_daily.csv", index=False, float_format="%.8f")
    tracking.to_csv(args.output / "tracking_mapping_summary.csv", index=False, float_format="%.8f")
    funding.to_csv(args.output / "funding_history.csv", index=False, float_format="%.10f")
    save_chart(minute, args.output / "gigadev_premium_comparison.png")

    metadata = {
        "generated_at": pd.Timestamp.now(tz=TZ).isoformat(),
        "minute_start": minute.index.min().isoformat(),
        "minute_end": minute.index.max().isoformat(),
        "complete_strict_dates": daily["trading_date"].tolist(),
        "symbols": {
            "a_share": "603986.SS",
            "h_share": "3986.HK",
            "binance_h_share": "GIGADEVUSDT",
            "hyperliquid_a_share": "xyz:GIGADEV",
            "usd_cny": "CNY=X",
            "usd_hkd": "HKD=X",
        },
        "share_ratio": "1 A share : 1 H share",
        "mapping_evidence": {
            "binance_underlying_type": "HK_EQUITY",
            "binance_index_constituents": ["KK_RFR_3986HKD_USD", "3986HK"],
            "hyperliquid": "A-share linkage inferred from minute-level tracking against 603986.SS",
        },
        "primary_formula": "symmetric(perp) - symmetric(spot), in percentage points",
        "symmetric_formula": "(A - H) / ((A + H) / 2) * 100",
        "ratio_formula": "(A / H - 1) * 100",
        "important_limitations": [
            "Yahoo and exchange one-minute candles are last/close prices, not executable bid/ask.",
            "Hyperliquid candles repeat the last trade in zero-trade minutes.",
            (
                "Only three complete common trading days are available because the "
                "Hyperliquid market started during 2026-09-08."
            ),
            (
                "Funding is applied to equal quantities at entry prices; the net "
                "column assumes 10 bp round-trip fees."
            ),
            (
                "Bid/ask spread and slippage are not available from candle history "
                "and remain unmodeled."
            ),
        ],
    }
    (args.output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
