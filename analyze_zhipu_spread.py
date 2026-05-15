from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests


HK_TZ = "Asia/Hong_Kong"
OUT_DIR = Path("output")
HK_TICKER = "2513.HK"
GATE_CONTRACT = "ZHIPU_USDT"
GATE_BASE = "https://api.gateio.ws/api/v4"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def get_json(url: str, params: dict | None = None, *, retries: int = 4) -> dict | list:
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise RuntimeError(f"{exc}; body={response.text[:500]}") from exc
            return response.json()
        except Exception as exc:  # noqa: BLE001 - network diagnostics are surfaced below.
            last_exc = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Failed request {url} params={params}: {last_exc}")


def parse_yahoo_chart(symbol: str, *, range_: str = "5d", interval: str = "1m") -> tuple[pd.DataFrame, dict]:
    payload = get_json(
        YAHOO_CHART.format(symbol=symbol),
        {"range": range_, "interval": interval},
    )
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    frame = pd.DataFrame(
        {
            "ts": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(HK_TZ),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        }
    )
    return frame, result["meta"]


def fetch_gate_candles(contract: str, start_ts: int, end_ts: int) -> pd.DataFrame:
    rows: list[dict] = []
    # Gate returns at most about 2000 bars; use 1900-minute slices to keep each
    # request under the documented maximum.
    step = 1900 * 60
    cursor = start_ts
    while cursor <= end_ts:
        chunk_end = min(cursor + step, end_ts)
        data = get_json(
            f"{GATE_BASE}/futures/usdt/candlesticks",
            {
                "contract": contract,
                "interval": "1m",
                "from": cursor,
                "to": chunk_end,
            },
        )
        if isinstance(data, list):
            rows.extend(data)
        cursor = chunk_end + 60
        time.sleep(0.15)

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows).drop_duplicates(subset=["t"]).sort_values("t")
    for col in ["o", "h", "l", "c", "sum", "v"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["ts"] = pd.to_datetime(frame["t"], unit="s", utc=True).dt.tz_convert(HK_TZ)
    return frame.rename(
        columns={
            "o": "gate_open",
            "h": "gate_high",
            "l": "gate_low",
            "c": "gate_close",
            "v": "gate_volume_contracts",
            "sum": "gate_turnover_base",
        }
    )


def fetch_gate_funding(contract: str, start_ts: int, end_ts: int) -> pd.DataFrame:
    data = get_json(
        f"{GATE_BASE}/futures/usdt/funding_rate",
        {"contract": contract, "from": start_ts, "to": end_ts, "limit": 1000},
    )
    frame = pd.DataFrame(data)
    if frame.empty:
        return frame
    frame["funding_rate"] = pd.to_numeric(frame["r"], errors="coerce")
    frame["funding_pct"] = frame["funding_rate"] * 100
    frame["ts"] = pd.to_datetime(frame["t"], unit="s", utc=True).dt.tz_convert(HK_TZ)
    return frame[["ts", "funding_rate", "funding_pct"]].sort_values("ts")


def fetch_gate_contract(contract: str) -> dict:
    return get_json(f"{GATE_BASE}/futures/usdt/contracts/{contract}")


def trading_periods_from_meta(meta: dict) -> list[dict]:
    periods: list[dict] = []
    for group in meta.get("tradingPeriods", []):
        for item in group:
            periods.append(
                {
                    "start": pd.Timestamp(item["start"], unit="s", tz="UTC").tz_convert(HK_TZ),
                    "end": pd.Timestamp(item["end"], unit="s", tz="UTC").tz_convert(HK_TZ),
                }
            )
    return periods


def add_session_label(frame: pd.DataFrame, periods: Iterable[dict]) -> pd.DataFrame:
    result = frame.copy()
    result["session"] = "closed"
    for period in periods:
        mask = (result["ts"] >= period["start"]) & (result["ts"] <= period["end"])
        result.loc[mask, "session"] = "hk_open"
    return result


def closed_segments(gate: pd.DataFrame, periods: list[dict], end_ts: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    minute_rows: list[pd.DataFrame] = []
    summary_rows: list[dict] = []
    final_end = pd.Timestamp(end_ts, unit="s", tz="UTC").tz_convert(HK_TZ)

    for idx, period in enumerate(periods):
        segment_start = period["end"]
        segment_end = periods[idx + 1]["start"] if idx + 1 < len(periods) else final_end
        segment = gate[(gate["ts"] >= segment_start) & (gate["ts"] <= segment_end)].copy()
        if segment.empty:
            continue

        anchor = segment["gate_close"].iloc[0]
        segment_name = f"{segment_start.strftime('%Y-%m-%d %H:%M')} -> {segment_end.strftime('%Y-%m-%d %H:%M')}"
        segment["segment"] = segment_name
        segment["change_from_segment_start_usdt"] = segment["gate_close"] - anchor
        segment["change_from_segment_start_pct"] = (segment["gate_close"] / anchor - 1) * 100
        segment["ts_hk"] = segment["ts"].dt.strftime("%Y-%m-%d %H:%M")
        minute_rows.append(segment)

        low_idx = segment["gate_close"].idxmin()
        high_idx = segment["gate_close"].idxmax()
        summary_rows.append(
            {
                "segment": segment_name,
                "minutes": int(len(segment)),
                "start_time": segment["ts_hk"].iloc[0],
                "end_time": segment["ts_hk"].iloc[-1],
                "start_price": float(anchor),
                "end_price": float(segment["gate_close"].iloc[-1]),
                "change_pct": float(segment["change_from_segment_start_pct"].iloc[-1]),
                "min_price": float(segment["gate_close"].min()),
                "min_change_pct": float(segment["change_from_segment_start_pct"].min()),
                "min_time": segment.loc[low_idx, "ts_hk"],
                "max_price": float(segment["gate_close"].max()),
                "max_change_pct": float(segment["change_from_segment_start_pct"].max()),
                "max_time": segment.loc[high_idx, "ts_hk"],
            }
        )

    minute_frame = pd.concat(minute_rows, ignore_index=True) if minute_rows else pd.DataFrame()
    summary_frame = pd.DataFrame(summary_rows)
    return minute_frame, summary_frame


def max_draw(items: pd.DataFrame, col: str, n: int = 8) -> str:
    if items.empty:
        return ""
    cols = ["ts_hk", col]
    return items[cols].head(n).to_string(index=False)


def fmt_pct(value: float | int | None, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.{digits}f}%"


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    hk_raw, hk_meta = parse_yahoo_chart(HK_TICKER)
    fx_raw, fx_meta = parse_yahoo_chart("USDHKD=X")
    periods = trading_periods_from_meta(hk_meta)
    start_ts = int(min(period["start"].timestamp() for period in periods))
    now_ts = int(time.time())
    end_ts = max(now_ts, int(max(period["end"].timestamp() for period in periods)))
    gate_start_ts = max(start_ts, end_ts - 9999 * 60)

    gate = fetch_gate_candles(GATE_CONTRACT, gate_start_ts, end_ts)
    funding = fetch_gate_funding(GATE_CONTRACT, start_ts, end_ts + 24 * 3600)
    contract = fetch_gate_contract(GATE_CONTRACT)

    hk = hk_raw.dropna(subset=["close"]).copy()
    hk = hk.rename(
        columns={
            "open": "hk_open",
            "high": "hk_high",
            "low": "hk_low",
            "close": "hk_close_hkd",
            "volume": "hk_volume",
        }
    )
    hk["ts_minute"] = hk["ts"].dt.floor("min")

    fx = fx_raw.dropna(subset=["close"]).copy()
    fx = fx.rename(columns={"close": "usdhkd"})
    fx["ts_minute"] = fx["ts"].dt.floor("min")
    fx = fx[["ts_minute", "usdhkd"]].sort_values("ts_minute")

    gate = gate.copy()
    gate["ts_minute"] = gate["ts"].dt.floor("min")

    merged = (
        hk.merge(gate, on="ts_minute", how="inner", suffixes=("_hk", "_gate"))
        .merge(fx, on="ts_minute", how="left")
        .sort_values("ts_minute")
    )
    merged["usdhkd"] = merged["usdhkd"].ffill().bfill()
    merged["hk_close_usdt"] = merged["hk_close_hkd"] / merged["usdhkd"]
    merged["spread_usdt_minus_hk_usdt"] = merged["gate_close"] - merged["hk_close_usdt"]
    merged["spread_pct_vs_hk_usdt"] = merged["spread_usdt_minus_hk_usdt"] / merged["hk_close_usdt"] * 100
    merged["hk_ret_from_first_pct"] = (
        merged["hk_close_hkd"] / merged["hk_close_hkd"].iloc[0] - 1
    ) * 100
    merged["gate_ret_from_first_pct"] = (
        merged["gate_close"] / merged["gate_close"].iloc[0] - 1
    ) * 100
    merged["relative_return_gap_pct"] = merged["gate_ret_from_first_pct"] - merged["hk_ret_from_first_pct"]
    merged["ts_hk"] = merged["ts_minute"].dt.strftime("%Y-%m-%d %H:%M")

    merged_export_cols = [
        "ts_hk",
        "hk_close_hkd",
        "usdhkd",
        "hk_close_usdt",
        "gate_close",
        "spread_usdt_minus_hk_usdt",
        "spread_pct_vs_hk_usdt",
        "hk_volume",
        "gate_volume_contracts",
        "hk_ret_from_first_pct",
        "gate_ret_from_first_pct",
        "relative_return_gap_pct",
    ]
    merged[merged_export_cols].to_csv(OUT_DIR / "zhipu_minute_spread_hk_open.csv", index=False, encoding="utf-8-sig")

    gate_sessions = add_session_label(gate, periods)
    last_period_end = max(period["end"] for period in periods)
    after_close = gate_sessions[gate_sessions["ts"] >= last_period_end].copy()
    closed_minutes, closed_summary = closed_segments(gate, periods, end_ts)
    if not after_close.empty:
        close_anchor = after_close["gate_close"].iloc[0]
        after_close["change_from_hk_close_usdt"] = after_close["gate_close"] - close_anchor
        after_close["change_from_hk_close_pct"] = (after_close["gate_close"] / close_anchor - 1) * 100
        after_close["ts_hk"] = after_close["ts"].dt.strftime("%Y-%m-%d %H:%M")
        after_close[
            [
                "ts_hk",
                "gate_open",
                "gate_high",
                "gate_low",
                "gate_close",
                "gate_volume_contracts",
                "change_from_hk_close_usdt",
                "change_from_hk_close_pct",
            ]
        ].to_csv(OUT_DIR / "zhipu_gate_after_hk_close.csv", index=False, encoding="utf-8-sig")

    if not closed_minutes.empty:
        closed_minutes[
            [
                "segment",
                "ts_hk",
                "gate_open",
                "gate_high",
                "gate_low",
                "gate_close",
                "gate_volume_contracts",
                "change_from_segment_start_usdt",
                "change_from_segment_start_pct",
            ]
        ].to_csv(OUT_DIR / "zhipu_gate_closed_minutes.csv", index=False, encoding="utf-8-sig")
    if not closed_summary.empty:
        closed_summary.to_csv(OUT_DIR / "zhipu_gate_closed_segments_summary.csv", index=False, encoding="utf-8-sig")

    funding_export = funding.copy()
    if not funding_export.empty:
        funding_export["ts_hk"] = funding_export["ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
        funding_export[["ts_hk", "funding_rate", "funding_pct"]].to_csv(
            OUT_DIR / "zhipu_gate_funding_rates.csv", index=False, encoding="utf-8-sig"
        )

    by_day = (
        merged.assign(day=merged["ts_minute"].dt.strftime("%Y-%m-%d"))
        .groupby("day")
        .agg(
            minutes=("ts_minute", "count"),
            hk_first=("hk_close_hkd", "first"),
            hk_last=("hk_close_hkd", "last"),
            gate_first=("gate_close", "first"),
            gate_last=("gate_close", "last"),
            spread_pct_mean=("spread_pct_vs_hk_usdt", "mean"),
            spread_pct_min=("spread_pct_vs_hk_usdt", "min"),
            spread_pct_max=("spread_pct_vs_hk_usdt", "max"),
            spread_pct_last=("spread_pct_vs_hk_usdt", "last"),
            rel_gap_last=("relative_return_gap_pct", "last"),
        )
        .reset_index()
    )
    by_day["hk_change_pct"] = (by_day["hk_last"] / by_day["hk_first"] - 1) * 100
    by_day["gate_change_pct"] = (by_day["gate_last"] / by_day["gate_first"] - 1) * 100
    by_day.to_csv(OUT_DIR / "zhipu_daily_summary.csv", index=False, encoding="utf-8-sig")

    summary: dict = {
        "generated_at_hk": pd.Timestamp.now(tz=HK_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "hk_symbol": HK_TICKER,
        "hk_long_name": hk_meta.get("longName"),
        "hk_currency": hk_meta.get("currency"),
        "gate_contract": GATE_CONTRACT,
        "contract": {
            "last_price": contract.get("last_price"),
            "mark_price": contract.get("mark_price"),
            "index_price": contract.get("index_price"),
            "funding_rate": contract.get("funding_rate"),
            "funding_rate_indicative": contract.get("funding_rate_indicative"),
            "funding_interval_seconds": contract.get("funding_interval"),
            "funding_next_apply_hk": pd.Timestamp(contract.get("funding_next_apply"), unit="s", tz="UTC")
            .tz_convert(HK_TZ)
            .strftime("%Y-%m-%d %H:%M:%S")
            if contract.get("funding_next_apply")
            else None,
            "max_leverage": contract.get("leverage_max"),
            "maker_fee_rate": contract.get("maker_fee_rate"),
            "taker_fee_rate": contract.get("taker_fee_rate"),
            "quanto_multiplier": contract.get("quanto_multiplier"),
        },
        "periods_hk": [
            {
                "start": period["start"].strftime("%Y-%m-%d %H:%M"),
                "end": period["end"].strftime("%Y-%m-%d %H:%M"),
            }
            for period in periods
        ],
        "data_window": {
            "requested_start_hk": pd.Timestamp(start_ts, unit="s", tz="UTC")
            .tz_convert(HK_TZ)
            .strftime("%Y-%m-%d %H:%M:%S"),
            "gate_effective_start_hk": pd.Timestamp(gate_start_ts, unit="s", tz="UTC")
            .tz_convert(HK_TZ)
            .strftime("%Y-%m-%d %H:%M:%S"),
            "end_hk": pd.Timestamp(end_ts, unit="s", tz="UTC").tz_convert(HK_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "note": "Gate 1m futures candles are limited to the most recent 10000 points; older requested minutes are omitted.",
        },
        "rows": {
            "hk_raw_non_null": int(len(hk)),
            "gate_candles": int(len(gate)),
            "aligned_hk_open_minutes": int(len(merged)),
            "after_close_minutes": int(len(after_close)),
            "closed_minutes": int(len(closed_minutes)),
            "closed_segments": int(len(closed_summary)),
            "funding_rows": int(len(funding)),
        },
        "spread": {
            "mean_pct": float(merged["spread_pct_vs_hk_usdt"].mean()),
            "median_pct": float(merged["spread_pct_vs_hk_usdt"].median()),
            "min_pct": float(merged["spread_pct_vs_hk_usdt"].min()),
            "max_pct": float(merged["spread_pct_vs_hk_usdt"].max()),
            "last_pct": float(merged["spread_pct_vs_hk_usdt"].iloc[-1]),
            "last_spread_usdt": float(merged["spread_usdt_minus_hk_usdt"].iloc[-1]),
            "last_hk_close_hkd": float(merged["hk_close_hkd"].iloc[-1]),
            "last_hk_close_usdt": float(merged["hk_close_usdt"].iloc[-1]),
            "last_gate_close": float(merged["gate_close"].iloc[-1]),
            "max_premium_time": merged.loc[merged["spread_pct_vs_hk_usdt"].idxmax(), "ts_hk"],
            "max_discount_time": merged.loc[merged["spread_pct_vs_hk_usdt"].idxmin(), "ts_hk"],
        },
        "after_close": {},
        "closed_segments": closed_summary.to_dict(orient="records") if not closed_summary.empty else [],
        "files": [
            str(OUT_DIR / "zhipu_minute_spread_hk_open.csv"),
            str(OUT_DIR / "zhipu_gate_after_hk_close.csv"),
            str(OUT_DIR / "zhipu_gate_closed_minutes.csv"),
            str(OUT_DIR / "zhipu_gate_closed_segments_summary.csv"),
            str(OUT_DIR / "zhipu_gate_funding_rates.csv"),
            str(OUT_DIR / "zhipu_daily_summary.csv"),
            str(OUT_DIR / "zhipu_analysis_summary.json"),
        ],
    }
    if not after_close.empty:
        summary["after_close"] = {
            "start_time": after_close["ts_hk"].iloc[0],
            "end_time": after_close["ts_hk"].iloc[-1],
            "start_price": float(after_close["gate_close"].iloc[0]),
            "last_price": float(after_close["gate_close"].iloc[-1]),
            "change_pct": float(after_close["change_from_hk_close_pct"].iloc[-1]),
            "min_change_pct": float(after_close["change_from_hk_close_pct"].min()),
            "max_change_pct": float(after_close["change_from_hk_close_pct"].max()),
            "high_time": after_close.loc[after_close["gate_close"].idxmax(), "ts_hk"],
            "low_time": after_close.loc[after_close["gate_close"].idxmin(), "ts_hk"],
        }

    (OUT_DIR / "zhipu_analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nDaily summary:")
    print(
        by_day[
            [
                "day",
                "minutes",
                "hk_change_pct",
                "gate_change_pct",
                "spread_pct_mean",
                "spread_pct_min",
                "spread_pct_max",
                "spread_pct_last",
                "rel_gap_last",
            ]
        ].round(4).to_string(index=False)
    )
    print("\nLargest Gate premiums vs HK converted price:")
    print(max_draw(merged.nlargest(8, "spread_pct_vs_hk_usdt"), "spread_pct_vs_hk_usdt"))
    print("\nLargest Gate discounts vs HK converted price:")
    print(max_draw(merged.nsmallest(8, "spread_pct_vs_hk_usdt"), "spread_pct_vs_hk_usdt"))
    if not funding_export.empty:
        print("\nFunding rates:")
        print(funding_export[["ts_hk", "funding_pct"]].to_string(index=False))
    if not closed_summary.empty:
        print("\nGate closed-session changes:")
        print(
            closed_summary[
                [
                    "segment",
                    "minutes",
                    "start_price",
                    "end_price",
                    "change_pct",
                    "min_change_pct",
                    "max_change_pct",
                    "min_time",
                    "max_time",
                ]
            ].round(4).to_string(index=False)
        )


if __name__ == "__main__":
    main()
