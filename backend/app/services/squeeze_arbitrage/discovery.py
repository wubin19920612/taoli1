from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import WatchFeatures

DISCOVERY_RULE_VERSION = "squeeze-discovery-v1"
DEFAULT_EXCLUDED_SYMBOLS = ("AKEUSDT", "BTRUSDT", "GUSDT", "LSKUSDT", "TUTUSDT")


@dataclass(frozen=True)
class DiscoveryTicker:
    raw_symbol: str
    metadata: dict[str, Any]
    quote_volume_24h: float
    price_change_24h: float
    range_24h: float
    trade_count_24h: int
    source_at: datetime


def screen_tickers(
    metadata: dict[str, dict[str, Any]], rows: Any, *, excluded: set[str], limit: int,
    now: datetime | None = None,
) -> tuple[list[DiscoveryTicker], int]:
    if not isinstance(rows, list):
        raise TypeError("Binance 24h ticker response is not a list")
    eligible: list[DiscoveryTicker] = []
    decision_at = now or datetime.now(UTC)
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or symbol not in metadata or symbol in excluded:
            continue
        try:
            volume = float(row["quoteVolume"])
            change = float(row["priceChangePercent"]) / 100
            high = float(row["highPrice"])
            low = float(row["lowPrice"])
            trades = int(row["count"])
            source_at = datetime.fromtimestamp(int(row["closeTime"]) / 1000, UTC)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not timedelta(seconds=-60) <= decision_at - source_at <= timedelta(minutes=5):
            continue
        if not all(math.isfinite(value) for value in (volume, change, high, low)):
            continue
        price_range = high / low - 1 if low > 0 else 0
        if not (1_000_000 <= volume <= 250_000_000 and 0.02 <= change <= 0.35):
            continue
        if price_range < 0.06 or trades < 300:
            continue
        eligible.append(DiscoveryTicker(symbol, metadata[symbol], volume, change,
                                        price_range, trades, source_at))

    # Prefer an active but still moderate daily move before spending OI requests.
    eligible.sort(key=lambda item: (
        -abs(item.price_change_24h - 0.12),
        min(item.range_24h, 0.3),
        item.quote_volume_24h,
        item.raw_symbol,
    ), reverse=True)
    return eligible[:limit], len(eligible)


def early_candidate_reasons(features: WatchFeatures) -> tuple[str, ...]:
    if features.status != "ready":
        return features.reasons or (features.status,)
    reasons: list[str] = []
    if not (0.01 <= features.return_4h <= 0.15):
        reasons.append("four_hour_move_outside_early_range")
    if not (0.03 <= features.return_24h <= 0.35):
        reasons.append("day_move_outside_early_range")
    if features.volume_ratio < 1.5:
        reasons.append("volume_not_accelerating")
    if features.oi_current_growth < 0.01:
        reasons.append("raw_oi_not_growing")
    if features.account_ratio > 1.1:
        reasons.append("short_crowding_not_evident")
    return tuple(reasons)


def early_candidate_score(features: WatchFeatures) -> float:
    return round(
        2 * min(features.volume_ratio, 10)
        + 20 * min(features.oi_current_growth, 0.3)
        + 20 * min(features.return_4h, 0.15)
        + 10 * max(0, 1 - features.account_ratio),
        4,
    )
