from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests


API_URL = "https://pulse-lite-api.astro-btc.xyz/api/query/new"
OUT_DIR = Path("output")
EXCHANGE_ALLOWLIST = ("binance", "bybit", "gate", "okx", "bitget", "aster", "htx")


def fetch_data() -> dict[str, Any]:
    response = requests.get(
        API_URL,
        headers={
            "Accept": "application/json",
            "Origin": "https://pulse-lite.astro-btc.xyz",
            "Referer": "https://pulse-lite.astro-btc.xyz/",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(payload)
    return payload["data"]


def is_allowed_exchange(key: str) -> bool:
    lowered = key.lower()
    return any(item in lowered for item in EXCHANGE_ALLOWLIST)


def build_rows(data: dict[str, Any], arb_type: str, *, min_turnover: float = 0) -> list[dict[str, Any]]:
    by_coin: dict[str, dict[str, dict[str, Any]]] = {}
    for exchange_key, payload in data.items():
        if not payload or not is_allowed_exchange(exchange_key):
            continue
        for item in payload.get("list") or []:
            name = item.get("name")
            if not name:
                continue
            by_coin.setdefault(name, {})[exchange_key] = item

    rows: list[dict[str, Any]] = []
    for coin, venues in by_coin.items():
        keys = list(venues)
        if len(keys) < 2:
            continue
        seen: set[tuple[str, str]] = set()
        for key1 in keys:
            for key2 in keys:
                if key1 == key2:
                    continue
                if tuple(sorted((key1, key2))) in seen:
                    continue
                first = venues[key1]
                second = venues[key2]
                if float(first.get("trade24Count") or 0) < min_turnover:
                    continue
                if float(second.get("trade24Count") or 0) < min_turnover:
                    continue

                key1_spot = key1.endswith("Spot")
                key1_future = key1.endswith("Future")
                key2_spot = key2.endswith("Spot")
                key2_future = key2.endswith("Future")
                if arb_type == "SF" and not (key1_spot and key2_future):
                    continue
                if arb_type == "FF" and not (key1_future and key2_future):
                    continue
                if arb_type == "SS" and not (key1_spot and key2_spot):
                    continue

                ask1 = float(first["a"])
                bid1 = float(first["b"])
                ask2 = float(second["a"])
                bid2 = float(second["b"])
                if min(ask1, bid1, ask2, bid2) <= 0:
                    continue

                open_diff = (bid2 - ask1) / (ask1 + bid2) * 2
                close_diff = (ask2 - bid1) / (bid1 + ask2) * 2
                ex1, ex2 = key1, key2
                leg1, leg2 = first, second
                if open_diff < 0 and close_diff < 0 and arb_type != "SF":
                    open_diff, close_diff = -close_diff, -open_diff
                    ex1, ex2 = key2, key1
                    leg1, leg2 = second, first

                if not (-1 < open_diff < 1):
                    continue
                seen.add(tuple(sorted((key1, key2))))

                def fmt_exchange(value: str) -> str:
                    return value.replace("Spot", "-S").replace("Future", "-F")

                def to_float(value: Any) -> float | None:
                    if value in (None, "", "--"):
                        return None
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return None

                rate1 = to_float(leg1.get("rate"))
                rate2 = to_float(leg2.get("rate"))
                mark1 = to_float(leg1.get("markPrice"))
                index1 = to_float(leg1.get("indexPrice"))
                mark2 = to_float(leg2.get("markPrice"))
                index2 = to_float(leg2.get("indexPrice"))
                index_diff1 = (mark1 - index1) / index1 * 100 if mark1 and index1 else None
                index_diff2 = (mark2 - index2) / index2 * 100 if mark2 and index2 else None
                rows.append(
                    {
                        "type": arb_type,
                        "coin": coin,
                        "ex1": fmt_exchange(ex1),
                        "ex2": fmt_exchange(ex2),
                        "open_diff_pct": open_diff * 100,
                        "close_diff_pct": close_diff * 100,
                        "spread_width_pct": abs(open_diff - close_diff) * 100,
                        "trade24_1": float(leg1.get("trade24Count") or 0),
                        "trade24_2": float(leg2.get("trade24Count") or 0),
                        "rate1": rate1,
                        "rate2": rate2,
                        "net_rate2_minus_rate1": (rate2 - rate1) if rate1 is not None and rate2 is not None else None,
                        "rate_interval1_h": leg1.get("rateInterval"),
                        "rate_interval2_h": leg2.get("rateInterval"),
                        "rate_max1": to_float(leg1.get("rateMax")),
                        "rate_max2": to_float(leg2.get("rateMax")),
                        "index_diff1_pct": index_diff1,
                        "index_diff2_pct": index_diff2,
                    }
                )
    return sorted(rows, key=lambda item: item["open_diff_pct"], reverse=True)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    data = fetch_data()
    generated_at = pd.Timestamp.now(tz="Asia/Shanghai")
    raw_path = OUT_DIR / "pulse_lite_query_new_full.json"
    raw_path.write_text(json.dumps({"generated_at": str(generated_at), "data": data}, ensure_ascii=False), encoding="utf-8")

    summaries: dict[str, Any] = {
        "generated_at": str(generated_at),
        "api_url": API_URL,
        "groups": {
            key: {"ts": payload.get("ts"), "count": len(payload.get("list") or [])}
            for key, payload in data.items()
            if isinstance(payload, dict)
        },
        "files": [str(raw_path)],
    }

    for min_turnover in (0, 100_000, 1_000_000):
        bucket: dict[str, Any] = {}
        for arb_type in ("SF", "FF", "SS"):
            rows = build_rows(data, arb_type, min_turnover=min_turnover)
            df = pd.DataFrame(rows)
            path = OUT_DIR / f"pulse_lite_{arb_type.lower()}_minvol_{int(min_turnover)}.csv"
            df.to_csv(path, index=False, encoding="utf-8-sig")
            summaries["files"].append(str(path))
            bucket[arb_type] = {
                "count": int(len(df)),
                "top10": df.head(10).round(6).to_dict(orient="records") if not df.empty else [],
            }
        summaries[f"min_turnover_{int(min_turnover)}"] = bucket

    summary_path = OUT_DIR / "pulse_lite_analysis_summary.json"
    summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    summaries["files"].append(str(summary_path))
    print(json.dumps(summaries, ensure_ascii=False, indent=2)[:12000])

    for min_turnover in (100_000, 1_000_000):
        print(f"\n=== min_turnover >= {min_turnover:,} ===")
        for arb_type in ("SF", "FF", "SS"):
            df = pd.read_csv(OUT_DIR / f"pulse_lite_{arb_type.lower()}_minvol_{int(min_turnover)}.csv")
            print(f"\n{arb_type} count={len(df)}")
            cols = [
                "coin",
                "ex1",
                "ex2",
                "open_diff_pct",
                "close_diff_pct",
                "spread_width_pct",
                "rate1",
                "rate2",
                "net_rate2_minus_rate1",
                "trade24_1",
                "trade24_2",
            ]
            print(df[cols].head(12).round(4).to_string(index=False) if not df.empty else "empty")


if __name__ == "__main__":
    main()
