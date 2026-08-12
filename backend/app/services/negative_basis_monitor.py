from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from math import isfinite, log10
from typing import Any

import aiosqlite
import httpx

from app.exchanges.base import (
    DEFAULT_HEADERS,
    DEFAULT_LIMITS,
    normalize_usdt_symbol,
    parse_datetime_ms,
    parse_datetime_seconds,
    parse_float,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.settings import RiskSettings
from app.models.negative_basis import (
    NEGATIVE_BASIS_FUTURE_EXCHANGES,
    NEGATIVE_BASIS_SPOT_EXCHANGES,
    NEGATIVE_BASIS_LEVEL_ORDER,
    NegativeBasisAlertEvent,
    NegativeBasisAnalysisResult,
    NegativeBasisAutoCandidate,
    NegativeBasisAutoScanSettings,
    NegativeBasisCurrentSnapshot,
    NegativeBasisHourlyStatPoint,
    NegativeBasisMonitorStatus,
    NegativeBasisPoint,
    NegativeBasisSignalLevel,
    NegativeBasisSignalSample,
    NegativeBasisThresholdState,
    NegativeBasisWatchItem,
    negative_basis_exchange_symbol_key,
    utc_now,
)
from app.models.pair_spread import (
    PairSpreadLegQuery,
    PairSpreadQueryResult,
    PairSpreadValueStats,
    normalize_pair_spread_symbol,
)
from app.services.pair_spread_query import PairSpreadQueryError, PairSpreadQueryService
from app.services.snapshot_store import SnapshotStore
from app.services.symbol_aliases import (
    SymbolAliasResolver,
    apply_pair_spread_symbol_aliases,
)

logger = logging.getLogger(__name__)

DISPLAY_TZ = timezone(timedelta(hours=8))
GATE_STATS_TIMEOUT = httpx.Timeout(12.0, connect=3.0, read=8.0, write=4.0, pool=4.0)
AUTO_SCAN_INTERVAL_SECONDS = 60
AUTO_SCAN_MAX_CANDIDATES = 16
AUTO_SCAN_MIN_SPOT_VOLUME_24H_USDT = 100_000
AUTO_COLLECT_MAX_DUE = 6
NEGATIVE_BASIS_AUTO_SCAN_SETTINGS_KEY = "negative_basis_auto_scan_settings"


@dataclass(frozen=True)
class GateContractStatPoint:
    bucket_at: datetime
    open_interest_usdt: float | None = None
    open_interest_contracts: float | None = None
    long_account_pct: float | None = None
    short_account_pct: float | None = None
    long_account_count: float | None = None
    short_account_count: float | None = None
    long_short_ratio: float | None = None
    funding_rate_pct: float | None = None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _positive(value: float | None) -> float | None:
    if value is None or not isfinite(value) or value <= 0:
        return None
    return value


def _nonnegative(value: float | None) -> float | None:
    if value is None or not isfinite(value) or value < 0:
        return None
    return value


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    numerator = _nonnegative(numerator)
    denominator = _positive(denominator)
    if numerator is None or denominator is None:
        return None
    return numerator / denominator


def _safe_pct_change(start: float | None, end: float | None) -> float | None:
    start = _positive(start)
    end = _nonnegative(end)
    if start is None or end is None:
        return None
    return (end - start) / start * 100


def _gate_contract(symbol: str) -> str:
    _, base, quote = normalize_usdt_symbol(symbol)
    return f"{base}_{quote}"


def _display_hour_bucket(value: datetime) -> datetime:
    return _as_utc(value).astimezone(DISPLAY_TZ).replace(minute=0, second=0, microsecond=0).astimezone(UTC)


def _stats(values: list[float]) -> PairSpreadValueStats:
    finite_values = [value for value in values if isfinite(value)]
    if not finite_values:
        return PairSpreadValueStats()
    return PairSpreadValueStats(
        min=min(finite_values),
        max=max(finite_values),
        mean=sum(finite_values) / len(finite_values),
        current=finite_values[-1],
    )


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _json_dump_list(value: list[str]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _watch_from_row(row: aiosqlite.Row) -> NegativeBasisWatchItem:
    return NegativeBasisWatchItem.model_validate_json(row["payload"])


def _sample_from_row(row: aiosqlite.Row) -> NegativeBasisSignalSample:
    return NegativeBasisSignalSample(
        id=row["id"],
        watch_id=row["watch_id"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        symbol=row["symbol"],
        spot_exchange=row["spot_exchange"],
        future_exchange=row["future_exchange"],
        signal_level=row["signal_level"],
        score=row["score"],
        spot_premium_pct=row["spot_premium_pct"],
        spot_price=row["spot_price"],
        future_price=row["future_price"],
        spot_volume_24h_usdt=row["spot_volume_24h_usdt"],
        future_volume_24h_usdt=row["future_volume_24h_usdt"],
        open_interest_usdt=row["open_interest_usdt"],
        open_interest_change_pct=row["open_interest_change_pct"],
        long_account_pct=row["long_account_pct"],
        short_account_pct=row["short_account_pct"],
        long_short_ratio=row["long_short_ratio"],
        funding_rate_pct=row["funding_rate_pct"],
        reasons=_json_list(row["reasons_json"]),
    )


def _event_from_row(row: aiosqlite.Row) -> NegativeBasisAlertEvent:
    return NegativeBasisAlertEvent(
        id=row["id"],
        watch_id=row["watch_id"],
        symbol=row["symbol"],
        spot_exchange=row["spot_exchange"],
        future_exchange=row["future_exchange"],
        signal_level=row["signal_level"],
        score=row["score"],
        spot_premium_pct=row["spot_premium_pct"],
        message=row["message"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _pair_point_to_negative(point) -> NegativeBasisPoint:
    return NegativeBasisPoint(
        bucket_at=point.bucket_at,
        spot_close=point.leg1_close,
        future_close=point.leg2_close,
        spot_premium_abs=point.leg1_close - point.leg2_close,
        spot_premium_pct=-point.spread_pct,
    )


def _current_point(result: PairSpreadQueryResult) -> NegativeBasisPoint | None:
    current = result.current
    if current is None:
        return None
    return NegativeBasisPoint(
        bucket_at=current.observed_at,
        spot_close=current.leg1.price,
        future_close=current.leg2.price,
        spot_premium_abs=current.leg1.price - current.leg2.price,
        spot_premium_pct=-current.spread_pct,
    )


def _analysis_points(result: PairSpreadQueryResult) -> list[NegativeBasisPoint]:
    points = [_pair_point_to_negative(point) for point in result.points]
    current = _current_point(result)
    if current is None:
        return points
    if points and _as_utc(points[-1].bucket_at) >= _as_utc(current.bucket_at):
        points[-1] = current
    else:
        points.append(current)
    return points


def _threshold_state(
    points: list[NegativeBasisPoint],
    *,
    name: NegativeBasisSignalLevel,
    threshold_pct: float,
    required_hits: int,
) -> NegativeBasisThresholdState:
    first_seen_at: datetime | None = None
    first_consecutive_at: datetime | None = None
    current_run = 0
    max_run = 0
    for point in points:
        if point.spot_premium_pct + 1e-12 >= threshold_pct:
            if first_seen_at is None:
                first_seen_at = point.bucket_at
            current_run += 1
            max_run = max(max_run, current_run)
            if current_run >= required_hits and first_consecutive_at is None:
                first_consecutive_at = point.bucket_at
        else:
            current_run = 0
    return NegativeBasisThresholdState(
        name=name,
        threshold_pct=threshold_pct,
        required_hits=required_hits,
        first_seen_at=first_seen_at,
        first_consecutive_at=first_consecutive_at,
        current_consecutive_hits=current_run,
        max_consecutive_hits=max_run,
        currently_active=current_run >= required_hits,
    )


def _threshold_states(
    item: NegativeBasisWatchItem,
    points: list[NegativeBasisPoint],
) -> list[NegativeBasisThresholdState]:
    return [
        _threshold_state(
            points,
            name="watch",
            threshold_pct=item.watch_threshold_pct,
            required_hits=item.watch_consecutive_hits,
        ),
        _threshold_state(
            points,
            name="building",
            threshold_pct=item.building_threshold_pct,
            required_hits=item.building_consecutive_hits,
        ),
        _threshold_state(
            points,
            name="confirmed",
            threshold_pct=item.confirmed_threshold_pct,
            required_hits=item.confirmed_consecutive_hits,
        ),
        _threshold_state(
            points,
            name="strong",
            threshold_pct=item.strong_threshold_pct,
            required_hits=item.strong_consecutive_hits,
        ),
        _threshold_state(
            points,
            name="extreme",
            threshold_pct=item.extreme_threshold_pct,
            required_hits=item.extreme_consecutive_hits,
        ),
    ]


def _hourly_premium_stats(points: list[NegativeBasisPoint]) -> dict[datetime, dict[str, float]]:
    grouped: dict[datetime, list[NegativeBasisPoint]] = {}
    for point in points:
        grouped.setdefault(_display_hour_bucket(point.bucket_at), []).append(point)
    stats: dict[datetime, dict[str, float]] = {}
    for bucket_at, rows in grouped.items():
        ordered = sorted(rows, key=lambda item: item.bucket_at)
        values = [item.spot_premium_pct for item in ordered if isfinite(item.spot_premium_pct)]
        if not values:
            continue
        stats[bucket_at] = {
            "mean": sum(values) / len(values),
            "max": max(values),
            "last": ordered[-1].spot_premium_pct,
        }
    return stats


def _hourly_oi_stats(
    gate_stats: list[GateContractStatPoint],
) -> dict[datetime, NegativeBasisHourlyStatPoint]:
    grouped: dict[datetime, list[GateContractStatPoint]] = {}
    for point in gate_stats:
        grouped.setdefault(_display_hour_bucket(point.bucket_at), []).append(point)

    rows: dict[datetime, NegativeBasisHourlyStatPoint] = {}
    for bucket_at, points in grouped.items():
        ordered = sorted(points, key=lambda item: item.bucket_at)
        oi_points = [point for point in ordered if point.open_interest_usdt is not None]
        oi_open = oi_points[0].open_interest_usdt if oi_points else None
        oi_close = oi_points[-1].open_interest_usdt if oi_points else None
        latest = ordered[-1]
        rows[bucket_at] = NegativeBasisHourlyStatPoint(
            bucket_at=bucket_at,
            open_interest_open_usdt=oi_open,
            open_interest_close_usdt=oi_close,
            open_interest_change_pct=_safe_pct_change(oi_open, oi_close),
            long_account_pct=latest.long_account_pct,
            short_account_pct=latest.short_account_pct,
            long_account_count=latest.long_account_count,
            short_account_count=latest.short_account_count,
            long_short_ratio=latest.long_short_ratio,
            funding_rate_pct=latest.funding_rate_pct,
        )
    return rows


def _build_hourly_stats(
    result: PairSpreadQueryResult,
    points: list[NegativeBasisPoint],
    gate_stats: list[GateContractStatPoint],
) -> list[NegativeBasisHourlyStatPoint]:
    premium_by_hour = _hourly_premium_stats(points)
    oi_by_hour = _hourly_oi_stats(gate_stats)
    volume_by_hour = {point.bucket_at: point for point in result.hourly_volume}
    hours = sorted(set(premium_by_hour) | set(oi_by_hour) | set(volume_by_hour))
    rows: list[NegativeBasisHourlyStatPoint] = []
    previous_spot_volume: float | None = None

    for bucket_at in hours:
        premium = premium_by_hour.get(bucket_at, {})
        volume = volume_by_hour.get(bucket_at)
        oi = oi_by_hour.get(bucket_at)
        spot_volume = volume.leg1_volume_usdt if volume is not None else None
        future_volume = volume.leg2_volume_usdt if volume is not None else None
        spot_growth = (
            spot_volume / previous_spot_volume
            if spot_volume is not None and previous_spot_volume is not None and previous_spot_volume > 0
            else None
        )
        if spot_volume is not None and spot_volume > 0:
            previous_spot_volume = spot_volume

        rows.append(
            NegativeBasisHourlyStatPoint(
                bucket_at=bucket_at,
                spot_premium_mean_pct=premium.get("mean"),
                spot_premium_max_pct=premium.get("max"),
                spot_premium_last_pct=premium.get("last"),
                spot_volume_usdt=spot_volume,
                future_volume_usdt=future_volume,
                spot_volume_growth=spot_growth,
                future_volume_ratio=_safe_ratio(future_volume, spot_volume),
                open_interest_open_usdt=oi.open_interest_open_usdt if oi is not None else None,
                open_interest_close_usdt=oi.open_interest_close_usdt if oi is not None else None,
                open_interest_change_pct=oi.open_interest_change_pct if oi is not None else None,
                long_account_pct=oi.long_account_pct if oi is not None else None,
                short_account_pct=oi.short_account_pct if oi is not None else None,
                long_account_count=oi.long_account_count if oi is not None else None,
                short_account_count=oi.short_account_count if oi is not None else None,
                long_short_ratio=oi.long_short_ratio if oi is not None else None,
                funding_rate_pct=oi.funding_rate_pct if oi is not None else None,
            )
        )

    if result.current is not None:
        current_bucket = _display_hour_bucket(result.current.observed_at)
        current_row = next((row for row in rows if row.bucket_at == current_bucket), None)
        if current_row is None:
            current_row = NegativeBasisHourlyStatPoint(bucket_at=current_bucket)
            rows.append(current_row)
            rows.sort(key=lambda item: item.bucket_at)
        future_leg = result.current.leg2
        if current_row.open_interest_close_usdt is None:
            current_row.open_interest_close_usdt = future_leg.open_interest_usdt
        if current_row.long_account_pct is None:
            current_row.long_account_pct = future_leg.long_account_pct
        if current_row.short_account_pct is None:
            current_row.short_account_pct = future_leg.short_account_pct
        if current_row.long_account_count is None:
            current_row.long_account_count = future_leg.long_account_count
        if current_row.short_account_count is None:
            current_row.short_account_count = future_leg.short_account_count
        if current_row.long_short_ratio is None:
            current_row.long_short_ratio = future_leg.long_short_ratio
        if current_row.funding_rate_pct is None:
            current_row.funding_rate_pct = future_leg.funding_rate_pct

    return rows


def _latest_row_with_context(
    hourly_stats: list[NegativeBasisHourlyStatPoint],
) -> NegativeBasisHourlyStatPoint | None:
    for row in reversed(hourly_stats):
        if (
            row.spot_volume_growth is not None
            or row.open_interest_change_pct is not None
            or row.funding_rate_pct is not None
            or row.long_short_ratio is not None
        ):
            return row
    return hourly_stats[-1] if hourly_stats else None


def _active_threshold(thresholds: list[NegativeBasisThresholdState], name: str) -> bool:
    return any(item.name == name and item.currently_active for item in thresholds)


def _level_from_context(
    item: NegativeBasisWatchItem,
    thresholds: list[NegativeBasisThresholdState],
    latest_hour: NegativeBasisHourlyStatPoint | None,
) -> tuple[NegativeBasisSignalLevel, list[str]]:
    reasons: list[str] = []
    spot_growth = latest_hour.spot_volume_growth if latest_hour is not None else None
    oi_growth = latest_hour.open_interest_change_pct if latest_hour is not None else None
    funding = latest_hour.funding_rate_pct if latest_hour is not None else None
    spot_hour_volume = latest_hour.spot_volume_usdt if latest_hour is not None else None

    if _active_threshold(thresholds, "extreme"):
        reasons.append(f"现货溢价已连续站上 {item.extreme_threshold_pct:.2f}% 极端阈值")
        return "extreme", reasons

    volume_ok = spot_growth is not None and spot_growth >= item.spot_volume_growth_threshold
    volume_floor_ok = spot_hour_volume is not None and spot_hour_volume >= item.min_spot_hourly_volume_usdt
    oi_confirmed = oi_growth is not None and oi_growth >= item.oi_confirmed_growth_pct
    oi_strong = oi_growth is not None and oi_growth >= item.oi_strong_growth_pct

    if volume_ok:
        reasons.append(f"现货小时成交额放大 {spot_growth:.2f}x")
    elif spot_growth is not None:
        reasons.append(f"现货小时成交额变化 {spot_growth:.2f}x")
    if oi_growth is not None:
        reasons.append(f"合约 OI 小时变化 {oi_growth:+.2f}%")
    if funding is not None and funding < 0:
        reasons.append(f"合约资金费率为负 {funding:+.4f}%")

    if _active_threshold(thresholds, "strong"):
        if oi_strong and volume_ok:
            reasons.insert(0, f"现货溢价已连续站上 {item.strong_threshold_pct:.2f}% 强信号阈值")
            return "strong", reasons
        if oi_confirmed or volume_ok:
            reasons.insert(0, f"现货溢价已连续站上 {item.strong_threshold_pct:.2f}%")
            return "confirmed", reasons
        reasons.insert(0, f"现货溢价已连续站上 {item.strong_threshold_pct:.2f}%，等待成交额或 OI 配合")
        return "watch", reasons

    if _active_threshold(thresholds, "confirmed"):
        if oi_confirmed:
            reasons.insert(0, f"现货溢价已连续站上 {item.confirmed_threshold_pct:.2f}% 确认阈值")
            return "confirmed", reasons
        if volume_ok:
            reasons.insert(0, f"现货溢价已连续站上 {item.confirmed_threshold_pct:.2f}%，现货量能同步放大")
            return "building", reasons
        reasons.insert(0, f"现货溢价已连续站上 {item.confirmed_threshold_pct:.2f}%，等待 OI 确认")
        return "watch", reasons

    if _active_threshold(thresholds, "building"):
        if volume_ok or volume_floor_ok:
            reasons.insert(0, f"现货溢价已连续站上 {item.building_threshold_pct:.2f}% 启动阈值")
            return "building", reasons
        reasons.insert(0, f"现货溢价已连续站上 {item.building_threshold_pct:.2f}%，等待现货量能放大")
        return "watch", reasons

    if _active_threshold(thresholds, "watch"):
        reasons.insert(0, f"现货溢价已连续站上 {item.watch_threshold_pct:.2f}% 观察阈值")
        return "watch", reasons

    return "none", ["现货溢价还没有连续站上观察阈值"]


def _score(
    current_premium_pct: float | None,
    latest_hour: NegativeBasisHourlyStatPoint | None,
) -> float:
    premium_score = min(55.0, max(0.0, current_premium_pct or 0.0) * 8)
    volume_growth = latest_hour.spot_volume_growth if latest_hour is not None else None
    oi_growth = latest_hour.open_interest_change_pct if latest_hour is not None else None
    funding = latest_hour.funding_rate_pct if latest_hour is not None else None
    lsr = latest_hour.long_short_ratio if latest_hour is not None else None
    volume_score = min(15.0, max(0.0, (volume_growth or 0.0) - 1.0) * 4)
    oi_score = min(18.0, max(0.0, oi_growth or 0.0) * 0.45)
    funding_score = min(8.0, abs(min(0.0, funding or 0.0)) * 2.5)
    lsr_score = min(4.0, max(0.0, (lsr or 0.0) - 1.0) * 2)
    return round(premium_score + volume_score + oi_score + funding_score + lsr_score, 2)


def build_negative_basis_analysis(
    item: NegativeBasisWatchItem,
    result: PairSpreadQueryResult,
    *,
    gate_stats: list[GateContractStatPoint] | None = None,
    warnings: list[str] | None = None,
) -> NegativeBasisAnalysisResult:
    points = _analysis_points(result)
    thresholds = _threshold_states(item, points)
    hourly_stats = _build_hourly_stats(result, points, gate_stats or [])
    latest_hour = _latest_row_with_context(hourly_stats)
    current_point = _current_point(result) or (points[-1] if points else None)
    level, reasons = _level_from_context(item, thresholds, latest_hour)
    score = _score(current_point.spot_premium_pct if current_point is not None else None, latest_hour)
    current = (
        NegativeBasisCurrentSnapshot(
            observed_at=result.current.observed_at,
            spot_leg=result.current.leg1,
            future_leg=result.current.leg2,
            spot_premium_abs=result.current.leg1.price - result.current.leg2.price,
            spot_premium_pct=-result.current.spread_pct,
        )
        if result.current is not None
        else None
    )
    return NegativeBasisAnalysisResult(
        item=item,
        observed_at=result.observed_at,
        signal_level=level,
        score=score,
        reasons=reasons,
        warnings=[*result.warnings, *(warnings or [])],
        current=current,
        spot_premium=_stats([point.spot_premium_pct for point in points]),
        thresholds=thresholds,
        points=points,
        hourly_stats=hourly_stats,
    )


def _market_price(market: MarketSnapshot) -> float | None:
    if market.market_type == MarketType.FUTURE:
        mark = _positive(market.mark_price)
        if mark is not None:
            return mark
    bid = _positive(market.bid)
    ask = _positive(market.ask)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def _market_original_symbol(market: MarketSnapshot) -> str:
    return market.symbol_alias_original_symbol or market.symbol


def _market_alias_multiplier(market: MarketSnapshot) -> float:
    multiplier = market.symbol_alias_price_multiplier
    return multiplier if multiplier > 0 else 1.0


def _market_symbols(market: MarketSnapshot) -> set[str]:
    return {market.symbol, _market_original_symbol(market)}


def _market_exchange_symbol_blocked(
    market: MarketSnapshot,
    blocked_exchange_symbols: set[str],
) -> bool:
    return (
        negative_basis_exchange_symbol_key(market.exchange, _market_original_symbol(market))
        in blocked_exchange_symbols
    )


def _alias_reason(label: str, market: MarketSnapshot) -> str | None:
    original_symbol = _market_original_symbol(market)
    multiplier = _market_alias_multiplier(market)
    if original_symbol == market.symbol and abs(multiplier - 1) < 1e-12:
        return None
    return f"{label}映射 {original_symbol}->{market.symbol}，汇率 {multiplier:g}"


def _spot_premium_pct(spot_price: float, future_price: float) -> float:
    return (spot_price - future_price) / ((spot_price + future_price) / 2) * 100


def _volume_score(value: float | None, cap: float) -> float:
    value = _positive(value)
    if value is None:
        return 0.0
    return min(cap, max(0.0, (log10(value) - 5.0) / 4.0) * cap)


def _auto_candidate_selection_score(
    *,
    premium_pct: float,
    spot_volume_24h_usdt: float | None,
    future_volume_24h_usdt: float | None,
) -> float:
    premium_score = min(30.0, max(0.0, premium_pct) * 0.2)
    spot_liquidity_score = _volume_score(spot_volume_24h_usdt, 40.0)
    future_liquidity_score = _volume_score(future_volume_24h_usdt, 30.0)
    return round(premium_score + spot_liquidity_score + future_liquidity_score, 2)


def _auto_candidate_selection_reasons(
    *,
    premium_pct: float,
    spot_volume_24h_usdt: float | None,
    future_volume_24h_usdt: float | None,
    spot: MarketSnapshot,
    future: MarketSnapshot,
) -> list[str]:
    reasons = [
        f"现货溢价 {_fmt_pct(premium_pct, 3)}",
        f"现货24h {_fmt_usdt(spot_volume_24h_usdt)}",
        f"合约24h {_fmt_usdt(future_volume_24h_usdt)}",
    ]
    alias_reasons = [
        reason
        for reason in (
            _alias_reason("现货", spot),
            _alias_reason("合约", future),
        )
        if reason is not None
    ]
    if alias_reasons:
        reasons.extend(alias_reasons)
    return reasons


def _auto_watch_id(symbol: str, spot_exchange: str, future_exchange: str) -> str:
    return f"auto:{spot_exchange}:{future_exchange}:{symbol}"


def _auto_signal_level(premium_pct: float, item: NegativeBasisWatchItem) -> NegativeBasisSignalLevel:
    if premium_pct >= item.extreme_threshold_pct:
        return "extreme"
    if premium_pct >= item.strong_threshold_pct:
        return "strong"
    if premium_pct >= item.confirmed_threshold_pct:
        return "confirmed"
    if premium_pct >= item.building_threshold_pct:
        return "building"
    if premium_pct >= item.watch_threshold_pct:
        return "watch"
    return "none"


def _auto_watch_item(
    candidate: NegativeBasisAutoCandidate,
    settings: NegativeBasisAutoScanSettings,
    *,
    existing: NegativeBasisWatchItem | None = None,
) -> NegativeBasisWatchItem:
    strategy_values = settings.strategy.model_dump()
    base = existing or NegativeBasisWatchItem(
        id=candidate.id,
        auto_managed=True,
        enabled=True,
        symbol=candidate.symbol,
        spot_exchange=candidate.spot_exchange,
        future_exchange=candidate.future_exchange,
        spot_symbol=candidate.spot_symbol or candidate.symbol,
        future_symbol=candidate.future_symbol or candidate.symbol,
        future_multiplier=1.0,
        note="auto discovered spot-premium candidate",
        **strategy_values,
    )
    return base.model_copy(
        update={
            **strategy_values,
            "auto_managed": True,
            "enabled": True,
            "symbol": candidate.symbol,
            "spot_exchange": candidate.spot_exchange,
            "future_exchange": candidate.future_exchange,
            "spot_symbol": candidate.spot_symbol or candidate.symbol,
            "future_symbol": candidate.future_symbol or candidate.symbol,
            "future_multiplier": 1.0,
            "updated_at": utc_now(),
        }
    )


def _auto_candidate(
    spot: MarketSnapshot,
    future: MarketSnapshot,
    item: NegativeBasisWatchItem,
    *,
    observed_at: datetime,
) -> NegativeBasisAutoCandidate | None:
    spot_price = _market_price(spot)
    future_price = _market_price(future)
    if spot_price is None or future_price is None:
        return None
    premium_pct = _spot_premium_pct(spot_price, future_price)
    if premium_pct < item.watch_threshold_pct:
        return None
    if (
        spot.volume_24h_usdt is not None
        and spot.volume_24h_usdt < AUTO_SCAN_MIN_SPOT_VOLUME_24H_USDT
    ):
        return None
    selection_score = _auto_candidate_selection_score(
        premium_pct=premium_pct,
        spot_volume_24h_usdt=spot.volume_24h_usdt,
        future_volume_24h_usdt=future.volume_24h_usdt,
    )
    spot_symbol = _market_original_symbol(spot)
    future_symbol = _market_original_symbol(future)
    return NegativeBasisAutoCandidate(
        id=_auto_watch_id(spot.symbol, spot.exchange, future.exchange),
        symbol=spot.symbol,
        spot_exchange=spot.exchange,
        future_exchange=future.exchange,
        spot_symbol=spot_symbol,
        future_symbol=future_symbol,
        future_multiplier=1.0,
        signal_level=_auto_signal_level(premium_pct, item),
        selection_score=selection_score,
        selection_reasons=_auto_candidate_selection_reasons(
            premium_pct=premium_pct,
            spot_volume_24h_usdt=spot.volume_24h_usdt,
            future_volume_24h_usdt=future.volume_24h_usdt,
            spot=spot,
            future=future,
        ),
        spot_premium_pct=premium_pct,
        spot_price=spot_price,
        future_price=future_price,
        spot_volume_24h_usdt=spot.volume_24h_usdt,
        future_volume_24h_usdt=future.volume_24h_usdt,
        observed_at=observed_at,
    )


def _best_auto_candidates_by_symbol(
    candidates: list[NegativeBasisAutoCandidate],
) -> list[NegativeBasisAutoCandidate]:
    best_by_symbol: dict[str, NegativeBasisAutoCandidate] = {}
    for candidate in candidates:
        current = best_by_symbol.get(candidate.symbol)
        if current is None or _auto_candidate_sort_key(candidate) > _auto_candidate_sort_key(current):
            best_by_symbol[candidate.symbol] = candidate
    return sorted(best_by_symbol.values(), key=_auto_candidate_sort_key, reverse=True)


def _auto_candidate_sort_key(candidate: NegativeBasisAutoCandidate) -> tuple[float, float, float, float]:
    return (
        candidate.selection_score,
        candidate.spot_volume_24h_usdt or 0.0,
        candidate.future_volume_24h_usdt or 0.0,
        candidate.spot_premium_pct,
    )


def _auto_candidate_blocked(
    candidate: NegativeBasisAutoCandidate,
    settings: NegativeBasisAutoScanSettings,
) -> bool:
    symbols = {
        candidate.symbol,
        candidate.spot_symbol or candidate.symbol,
        candidate.future_symbol or candidate.symbol,
    }
    exchange_symbols = {
        negative_basis_exchange_symbol_key(
            candidate.spot_exchange,
            candidate.spot_symbol or candidate.symbol,
        ),
        negative_basis_exchange_symbol_key(
            candidate.future_exchange,
            candidate.future_symbol or candidate.symbol,
        ),
    }
    return (
        bool(symbols & set(settings.blocked_symbols))
        or candidate.spot_exchange in set(settings.blocked_exchanges)
        or candidate.future_exchange in set(settings.blocked_exchanges)
        or bool(exchange_symbols & set(settings.blocked_exchange_symbols))
    )


def _auto_watch_blocked(
    item: NegativeBasisWatchItem,
    settings: NegativeBasisAutoScanSettings,
) -> bool:
    if not item.auto_managed:
        return False
    return not settings.enabled or _auto_watch_matches_blocklist(item, settings)


def _auto_watch_matches_blocklist(
    item: NegativeBasisWatchItem,
    settings: NegativeBasisAutoScanSettings,
) -> bool:
    if not item.auto_managed:
        return False
    symbols = {
        item.symbol,
        item.spot_symbol or item.symbol,
        item.future_symbol or item.symbol,
    }
    exchange_symbols = {
        negative_basis_exchange_symbol_key(item.spot_exchange, item.spot_symbol or item.symbol),
        negative_basis_exchange_symbol_key(item.future_exchange, item.future_symbol or item.symbol),
    }
    return (
        bool(symbols & set(settings.blocked_symbols))
        or item.spot_exchange in set(settings.blocked_exchanges)
        or item.future_exchange in set(settings.blocked_exchanges)
        or bool(exchange_symbols & set(settings.blocked_exchange_symbols))
    )


class GateContractStatsClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=GATE_STATS_TIMEOUT,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            limits=DEFAULT_LIMITS,
            http2=False,
            trust_env=True,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and not self.client.is_closed:
            await self.client.aclose()

    async def fetch(
        self,
        symbol: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[GateContractStatPoint]:
        duration_seconds = max(300, int((_as_utc(end) - _as_utc(start)).total_seconds()))
        limit = min(1000, max(10, duration_seconds // 300 + 10))
        contract = _gate_contract(symbol)
        response = await self.client.get(
            "https://api.gateio.ws/api/v4/futures/usdt/contract_stats"
            f"?contract={contract}&interval=5m&limit={limit}"
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
        points = [
            point
            for row in rows
            if isinstance(row, dict)
            if (point := _parse_gate_stats_row(row)) is not None
            and _as_utc(start) <= point.bucket_at <= _as_utc(end)
        ]
        return sorted(points, key=lambda item: item.bucket_at)


def _parse_gate_stats_time(row: dict[str, Any]) -> datetime | None:
    for key in ("time_ms", "timestamp_ms", "t_ms"):
        parsed = parse_datetime_ms(row.get(key))
        if parsed is not None:
            return parsed
    for key in ("time", "t", "timestamp"):
        raw = parse_float(row.get(key))
        if raw is None:
            continue
        if raw > 10_000_000_000:
            parsed = parse_datetime_ms(raw)
        else:
            parsed = parse_datetime_seconds(raw)
        if parsed is not None:
            return parsed
    return None


def _parse_gate_stats_row(row: dict[str, Any]) -> GateContractStatPoint | None:
    bucket_at = _parse_gate_stats_time(row)
    if bucket_at is None:
        return None
    long_count = _nonnegative(parse_float(row.get("long_users")))
    short_count = _nonnegative(parse_float(row.get("short_users")))
    account_ratio = _nonnegative(parse_float(row.get("lsr_account")))
    account_total = long_count + short_count if long_count is not None and short_count is not None else None
    long_pct = long_count / account_total * 100 if account_total and account_total > 0 else None
    short_pct = short_count / account_total * 100 if account_total and account_total > 0 else None
    if long_pct is None and account_ratio is not None:
        long_pct = account_ratio / (1 + account_ratio) * 100
        short_pct = 100 / (1 + account_ratio)

    open_interest_contracts = _nonnegative(parse_float(row.get("open_interest")))
    open_interest_usdt = _nonnegative(
        parse_float(row.get("open_interest_usd") or row.get("open_interest_usdt"))
    )
    if open_interest_usdt is None and open_interest_contracts is not None:
        mark = _positive(parse_float(row.get("mark_price")))
        if mark is not None:
            open_interest_usdt = open_interest_contracts * mark
    funding_rate = parse_float(row.get("last_funding_rate") or row.get("funding_rate"))
    return GateContractStatPoint(
        bucket_at=_as_utc(bucket_at),
        open_interest_usdt=open_interest_usdt,
        open_interest_contracts=open_interest_contracts,
        long_account_pct=long_pct,
        short_account_pct=short_pct,
        long_account_count=long_count,
        short_account_count=short_count,
        long_short_ratio=account_ratio,
        funding_rate_pct=funding_rate * 100 if funding_rate is not None else None,
    )


class NegativeBasisMonitorRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def get_auto_scan_settings(self) -> NegativeBasisAutoScanSettings:
        cursor = await self.db.execute(
            "SELECT payload FROM app_settings WHERE key = ?",
            (NEGATIVE_BASIS_AUTO_SCAN_SETTINGS_KEY,),
        )
        row = await cursor.fetchone()
        if row is None:
            return NegativeBasisAutoScanSettings()
        try:
            return NegativeBasisAutoScanSettings.model_validate_json(row["payload"])
        except ValueError:
            logger.exception("invalid negative basis auto scan settings payload")
            return NegativeBasisAutoScanSettings()

    async def upsert_auto_scan_settings(
        self,
        settings: NegativeBasisAutoScanSettings,
    ) -> NegativeBasisAutoScanSettings:
        saved = NegativeBasisAutoScanSettings.model_validate(
            {**settings.model_dump(), "updated_at": utc_now()}
        )
        await self.db.execute(
            """
            INSERT INTO app_settings (key, payload)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload
            """,
            (NEGATIVE_BASIS_AUTO_SCAN_SETTINGS_KEY, saved.model_dump_json()),
        )
        await self.db.commit()
        return saved

    async def upsert_watch_item(self, item: NegativeBasisWatchItem) -> NegativeBasisWatchItem:
        saved = item.model_copy(update={"updated_at": utc_now()})
        await self.db.execute(
            """
            INSERT INTO negative_basis_watchlist (id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (
                saved.id,
                saved.model_dump_json(),
                saved.created_at.isoformat(),
                saved.updated_at.isoformat(),
            ),
        )
        await self.db.commit()
        return saved

    async def get_watch_item(self, item_id: str) -> NegativeBasisWatchItem | None:
        cursor = await self.db.execute(
            "SELECT payload FROM negative_basis_watchlist WHERE id = ?",
            (item_id,),
        )
        row = await cursor.fetchone()
        return _watch_from_row(row) if row is not None else None

    async def list_watch_items(self) -> list[NegativeBasisWatchItem]:
        cursor = await self.db.execute(
            """
            SELECT payload
            FROM negative_basis_watchlist
            ORDER BY updated_at DESC, created_at DESC
            """
        )
        rows = await cursor.fetchall()
        return [_watch_from_row(row) for row in rows]

    async def delete_watch_item(self, item_id: str) -> None:
        await self.db.execute("DELETE FROM negative_basis_watchlist WHERE id = ?", (item_id,))
        await self.db.commit()

    async def delete_auto_watch_items_matching(
        self,
        settings: NegativeBasisAutoScanSettings,
    ) -> int:
        blocked_exchanges = set(settings.blocked_exchanges)
        blocked_symbols = set(settings.blocked_symbols)
        blocked_exchange_symbols = set(settings.blocked_exchange_symbols)
        if not blocked_exchanges and not blocked_symbols and not blocked_exchange_symbols:
            return 0

        deleted = 0
        for item in await self.list_watch_items():
            if not _auto_watch_matches_blocklist(item, settings):
                continue
            await self.db.execute("DELETE FROM negative_basis_watchlist WHERE id = ?", (item.id,))
            deleted += 1
        if deleted:
            await self.db.commit()
        return deleted

    async def delete_unselected_auto_watch_items(self, keep_ids: set[str]) -> int:
        deleted = 0
        for item in await self.list_watch_items():
            if not item.auto_managed or item.id in keep_ids:
                continue
            await self.db.execute("DELETE FROM negative_basis_watchlist WHERE id = ?", (item.id,))
            deleted += 1
        if deleted:
            await self.db.commit()
        return deleted

    async def insert_sample(self, sample: NegativeBasisSignalSample) -> None:
        await self.db.execute(
            """
            INSERT INTO negative_basis_signal_samples (
              watch_id, observed_at, symbol, spot_exchange, future_exchange,
              signal_level, score, spot_premium_pct, spot_price, future_price,
              spot_volume_24h_usdt, future_volume_24h_usdt, open_interest_usdt,
              open_interest_change_pct, long_account_pct, short_account_pct,
              long_short_ratio, funding_rate_pct, reasons_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sample.watch_id,
                sample.observed_at.isoformat(),
                sample.symbol,
                sample.spot_exchange,
                sample.future_exchange,
                sample.signal_level,
                sample.score,
                sample.spot_premium_pct,
                sample.spot_price,
                sample.future_price,
                sample.spot_volume_24h_usdt,
                sample.future_volume_24h_usdt,
                sample.open_interest_usdt,
                sample.open_interest_change_pct,
                sample.long_account_pct,
                sample.short_account_pct,
                sample.long_short_ratio,
                sample.funding_rate_pct,
                _json_dump_list(sample.reasons),
            ),
        )
        await self.db.commit()

    async def create_event(self, event: NegativeBasisAlertEvent) -> NegativeBasisAlertEvent:
        await self.db.execute(
            """
            INSERT INTO negative_basis_alert_events (
              id, watch_id, symbol, spot_exchange, future_exchange,
              signal_level, score, spot_premium_pct, message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.watch_id,
                event.symbol,
                event.spot_exchange,
                event.future_exchange,
                event.signal_level,
                event.score,
                event.spot_premium_pct,
                event.message,
                event.created_at.isoformat(),
            ),
        )
        await self.db.commit()
        return event

    async def list_samples(
        self,
        *,
        watch_id: str | None = None,
        symbol: str | None = None,
        start_at: datetime | None = None,
        limit: int = 1000,
    ) -> list[NegativeBasisSignalSample]:
        clauses: list[str] = []
        params: list[object] = []
        if watch_id:
            clauses.append("watch_id = ?")
            params.append(watch_id)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if start_at:
            clauses.append("observed_at >= ?")
            params.append(_as_utc(start_at).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self.db.execute(
            f"""
            SELECT *
            FROM negative_basis_signal_samples
            {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [_sample_from_row(row) for row in rows]

    async def list_events(
        self,
        *,
        watch_id: str | None = None,
        symbol: str | None = None,
        start_at: datetime | None = None,
        limit: int = 200,
    ) -> list[NegativeBasisAlertEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if watch_id:
            clauses.append("watch_id = ?")
            params.append(watch_id)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if start_at:
            clauses.append("created_at >= ?")
            params.append(_as_utc(start_at).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self.db.execute(
            f"""
            SELECT *
            FROM negative_basis_alert_events
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [_event_from_row(row) for row in rows]

    async def count_samples(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) AS c FROM negative_basis_signal_samples")
        row = await cursor.fetchone()
        return int(row["c"] if row is not None else 0)

    async def count_events(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) AS c FROM negative_basis_alert_events")
        row = await cursor.fetchone()
        return int(row["c"] if row is not None else 0)

    async def prune(self, retention_hours: int) -> int:
        cutoff = utc_now() - timedelta(hours=retention_hours)
        sample_cursor = await self.db.execute(
            "DELETE FROM negative_basis_signal_samples WHERE observed_at < ?",
            (cutoff.isoformat(),),
        )
        event_cursor = await self.db.execute(
            "DELETE FROM negative_basis_alert_events WHERE created_at < ?",
            (cutoff.isoformat(),),
        )
        await self.db.commit()
        deleted = sample_cursor.rowcount if sample_cursor.rowcount is not None else 0
        deleted += event_cursor.rowcount if event_cursor.rowcount is not None else 0
        return deleted


class NegativeBasisMonitor:
    def __init__(
        self,
        repo: NegativeBasisMonitorRepository,
        *,
        snapshot_store: SnapshotStore | None = None,
        query_service_factory: Callable[[], PairSpreadQueryService] | None = None,
        gate_stats_client: GateContractStatsClient | None = None,
        alert_sender: Callable[[str], Awaitable[None]] | None = None,
        risk_settings_loader: Callable[[], Awaitable[RiskSettings]] | None = None,
    ) -> None:
        self.repo = repo
        self.snapshot_store = snapshot_store
        self.query_service_factory = query_service_factory or PairSpreadQueryService
        self.gate_stats_client = gate_stats_client or GateContractStatsClient()
        self.alert_sender = alert_sender
        self.risk_settings_loader = risk_settings_loader
        self._last_run_at: dict[str, datetime] = {}
        self._last_sent_at: dict[str, datetime] = {}
        self._last_sent_level: dict[str, NegativeBasisSignalLevel] = {}
        self._last_auto_scan_at: datetime | None = None
        self._auto_candidates: list[NegativeBasisAutoCandidate] = []
        self._auto_scan_error: str | None = None
        self._latest_error: str | None = None
        self._running = False

    def running(self) -> bool:
        return self._running

    async def aclose(self) -> None:
        await self.gate_stats_client.aclose()

    async def update_auto_scan_settings(
        self,
        settings: NegativeBasisAutoScanSettings,
    ) -> NegativeBasisAutoScanSettings:
        saved = await self.repo.upsert_auto_scan_settings(settings)
        await self.repo.delete_auto_watch_items_matching(saved)
        if saved.enabled:
            await self.discover_auto_candidates(force=True)
        else:
            self._auto_candidates = []
        return saved

    async def block_auto_symbol(self, symbol: str) -> NegativeBasisAutoScanSettings:
        normalized_symbol = normalize_pair_spread_symbol(symbol)
        settings = await self.repo.get_auto_scan_settings()
        blocked_symbols = [*settings.blocked_symbols]
        if normalized_symbol not in blocked_symbols:
            blocked_symbols.append(normalized_symbol)
        return await self.update_auto_scan_settings(
            NegativeBasisAutoScanSettings(
                enabled=settings.enabled,
                strategy=settings.strategy,
                blocked_exchanges=settings.blocked_exchanges,
                blocked_symbols=blocked_symbols,
                blocked_exchange_symbols=settings.blocked_exchange_symbols,
            )
        )

    async def unblock_auto_symbol(self, symbol: str) -> NegativeBasisAutoScanSettings:
        normalized_symbol = normalize_pair_spread_symbol(symbol)
        settings = await self.repo.get_auto_scan_settings()
        return await self.update_auto_scan_settings(
            NegativeBasisAutoScanSettings(
                enabled=settings.enabled,
                strategy=settings.strategy,
                blocked_exchanges=settings.blocked_exchanges,
                blocked_symbols=[
                    item for item in settings.blocked_symbols if item != normalized_symbol
                ],
                blocked_exchange_symbols=settings.blocked_exchange_symbols,
            )
        )

    async def block_auto_exchange(self, exchange: str) -> NegativeBasisAutoScanSettings:
        normalized_exchange = exchange.strip().lower()
        settings = await self.repo.get_auto_scan_settings()
        blocked_exchanges = [*settings.blocked_exchanges]
        if normalized_exchange not in blocked_exchanges:
            blocked_exchanges.append(normalized_exchange)
        return await self.update_auto_scan_settings(
            NegativeBasisAutoScanSettings(
                enabled=settings.enabled,
                strategy=settings.strategy,
                blocked_exchanges=blocked_exchanges,
                blocked_symbols=settings.blocked_symbols,
                blocked_exchange_symbols=settings.blocked_exchange_symbols,
            )
        )

    async def unblock_auto_exchange(self, exchange: str) -> NegativeBasisAutoScanSettings:
        normalized_exchange = exchange.strip().lower()
        settings = await self.repo.get_auto_scan_settings()
        return await self.update_auto_scan_settings(
            NegativeBasisAutoScanSettings(
                enabled=settings.enabled,
                strategy=settings.strategy,
                blocked_exchanges=[
                    item for item in settings.blocked_exchanges if item != normalized_exchange
                ],
                blocked_symbols=settings.blocked_symbols,
                blocked_exchange_symbols=settings.blocked_exchange_symbols,
            )
        )

    async def block_auto_exchange_symbol(
        self,
        *,
        exchange: str,
        symbol: str,
    ) -> NegativeBasisAutoScanSettings:
        key = negative_basis_exchange_symbol_key(exchange, symbol)
        settings = await self.repo.get_auto_scan_settings()
        blocked_exchange_symbols = [*settings.blocked_exchange_symbols]
        if key not in blocked_exchange_symbols:
            blocked_exchange_symbols.append(key)
        return await self.update_auto_scan_settings(
            NegativeBasisAutoScanSettings(
                enabled=settings.enabled,
                strategy=settings.strategy,
                blocked_exchanges=settings.blocked_exchanges,
                blocked_symbols=settings.blocked_symbols,
                blocked_exchange_symbols=blocked_exchange_symbols,
            )
        )

    async def unblock_auto_exchange_symbol(
        self,
        *,
        exchange: str,
        symbol: str,
    ) -> NegativeBasisAutoScanSettings:
        key = negative_basis_exchange_symbol_key(exchange, symbol)
        settings = await self.repo.get_auto_scan_settings()
        return await self.update_auto_scan_settings(
            NegativeBasisAutoScanSettings(
                enabled=settings.enabled,
                strategy=settings.strategy,
                blocked_exchanges=settings.blocked_exchanges,
                blocked_symbols=settings.blocked_symbols,
                blocked_exchange_symbols=[
                    item for item in settings.blocked_exchange_symbols if item != key
                ],
            )
        )

    async def run(self, stop_event: asyncio.Event) -> None:
        self._running = True
        prune_counter = 0
        try:
            while not stop_event.is_set():
                try:
                    await self.discover_auto_candidates()
                    await self.collect_due()
                    self._latest_error = None
                except Exception as exc:  # noqa: BLE001 - background monitor should keep running.
                    self._latest_error = _error_message(exc)
                    logger.exception("negative basis monitor cycle failed")
                prune_counter += 1
                if prune_counter >= 120:
                    prune_counter = 0
                    with suppress(Exception):
                        items = await self.repo.list_watch_items()
                        retention_hours = max((item.retention_hours for item in items), default=720)
                        await self.repo.prune(retention_hours)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=1.0)
                except TimeoutError:
                    continue
        finally:
            self._running = False

    async def collect_due(self) -> list[NegativeBasisAnalysisResult]:
        now = utc_now()
        settings = await self.repo.get_auto_scan_settings()
        items = [
            item
            for item in await self.repo.list_watch_items()
            if item.enabled and not _auto_watch_blocked(item, settings)
        ]
        due_items = [
            item
            for item in items
            if self._last_run_at.get(item.id) is None
            or (now - self._last_run_at[item.id]).total_seconds() >= item.interval_seconds
        ]
        if not due_items:
            return []
        due_items = sorted(
            due_items,
            key=lambda item: (
                item.auto_managed,
                self._last_run_at.get(item.id) or datetime.min.replace(tzinfo=UTC),
            ),
        )[:AUTO_COLLECT_MAX_DUE]
        results = await asyncio.gather(*(self.collect_watch_item(item) for item in due_items))
        return list(results)

    async def discover_auto_candidates(self, *, force: bool = False) -> list[NegativeBasisAutoCandidate]:
        if self.snapshot_store is None:
            self._auto_candidates = []
            return []
        now = utc_now()
        settings = await self.repo.get_auto_scan_settings()
        if not settings.enabled:
            self._auto_candidates = []
            self._last_auto_scan_at = now
            self._auto_scan_error = None
            return []
        if (
            not force
            and self._last_auto_scan_at is not None
            and (now - self._last_auto_scan_at).total_seconds() < AUTO_SCAN_INTERVAL_SECONDS
        ):
            return self._auto_candidates
        try:
            await self.repo.delete_auto_watch_items_matching(settings)
            markets = self.snapshot_store.get_markets()
            candidates = self._discover_auto_candidates_from_markets(
                markets,
                now=now,
                settings=settings,
            )
            self._auto_candidates = candidates
            self._last_auto_scan_at = now
            self._auto_scan_error = None
            await self._upsert_auto_candidates(candidates, settings=settings)
            await self.repo.delete_unselected_auto_watch_items({candidate.id for candidate in candidates})
            return candidates
        except Exception as exc:  # noqa: BLE001 - keep existing watchlist monitoring alive.
            self._auto_scan_error = _error_message(exc)
            logger.exception("negative basis auto discovery failed")
            return self._auto_candidates

    def _discover_auto_candidates_from_markets(
        self,
        markets: list[MarketSnapshot],
        *,
        now: datetime,
        settings: NegativeBasisAutoScanSettings | None = None,
    ) -> list[NegativeBasisAutoCandidate]:
        settings = settings or NegativeBasisAutoScanSettings()
        if not settings.enabled:
            return []
        blocked_exchanges = set(settings.blocked_exchanges)
        blocked_symbols = set(settings.blocked_symbols)
        blocked_exchange_symbols = set(settings.blocked_exchange_symbols)
        template = NegativeBasisWatchItem(
            id="auto-template",
            auto_managed=True,
            enabled=True,
            **settings.strategy.model_dump(),
        )
        spots_by_symbol: dict[str, list[MarketSnapshot]] = {}
        futures_by_symbol: dict[str, list[MarketSnapshot]] = {}
        for market in markets:
            if not market.symbol.endswith("USDT"):
                continue
            if _market_symbols(market) & blocked_symbols or market.exchange in blocked_exchanges:
                continue
            if market.market_type == MarketType.SPOT:
                if market.exchange not in NEGATIVE_BASIS_SPOT_EXCHANGES:
                    continue
                if _market_exchange_symbol_blocked(market, blocked_exchange_symbols):
                    continue
                spots_by_symbol.setdefault(market.symbol, []).append(market)
            elif market.market_type == MarketType.FUTURE:
                if market.exchange not in NEGATIVE_BASIS_FUTURE_EXCHANGES:
                    continue
                if _market_exchange_symbol_blocked(market, blocked_exchange_symbols):
                    continue
                futures_by_symbol.setdefault(market.symbol, []).append(market)

        candidates: list[NegativeBasisAutoCandidate] = []
        for symbol in sorted(spots_by_symbol.keys() & futures_by_symbol.keys()):
            for spot in spots_by_symbol[symbol]:
                for future in futures_by_symbol[symbol]:
                    candidate = _auto_candidate(spot, future, template, observed_at=now)
                    if candidate is not None:
                        candidates.append(candidate)
        return _best_auto_candidates_by_symbol(candidates)[:AUTO_SCAN_MAX_CANDIDATES]

    async def _upsert_auto_candidates(
        self,
        candidates: list[NegativeBasisAutoCandidate],
        *,
        settings: NegativeBasisAutoScanSettings | None = None,
    ) -> None:
        settings = settings or NegativeBasisAutoScanSettings()
        for candidate in candidates:
            if _auto_candidate_blocked(candidate, settings):
                continue
            existing = await self.repo.get_watch_item(candidate.id)
            if existing is not None and not existing.auto_managed:
                continue
            saved = _auto_watch_item(candidate, settings, existing=existing)
            await self.repo.upsert_watch_item(saved)

    async def collect_watch_item(self, item: NegativeBasisWatchItem) -> NegativeBasisAnalysisResult:
        result = await self.analyze_item(item)
        await self.repo.insert_sample(_sample_from_analysis(result))
        if self._should_alert(item, result):
            event = NegativeBasisAlertEvent(
                watch_id=item.id,
                symbol=item.symbol,
                spot_exchange=item.spot_exchange,
                future_exchange=item.future_exchange,
                signal_level=result.signal_level,
                score=result.score,
                spot_premium_pct=result.spot_premium.current,
                message=_alert_message(result),
                created_at=result.observed_at,
            )
            await self.repo.create_event(event)
            self._last_sent_at[item.id] = result.observed_at
            self._last_sent_level[item.id] = result.signal_level
            if self.alert_sender is not None:
                try:
                    await self.alert_sender(event.message)
                except Exception:  # noqa: BLE001 - event has already been recorded.
                    logger.exception("negative basis alert notification failed")
        self._last_run_at[item.id] = result.observed_at
        return result

    async def analyze_item(self, item: NegativeBasisWatchItem) -> NegativeBasisAnalysisResult:
        resolver = SymbolAliasResolver([])
        if self.risk_settings_loader is not None:
            resolver = SymbolAliasResolver((await self.risk_settings_loader()).symbol_aliases)
        spot_alias = resolver.resolve(
            exchange=item.spot_exchange,
            symbol=item.spot_symbol or item.symbol,
            market_type=MarketType.SPOT,
        )
        future_alias = resolver.resolve(
            exchange=item.future_exchange,
            symbol=item.future_symbol or item.symbol,
            market_type=MarketType.FUTURE,
        )
        spot_leg = PairSpreadLegQuery(
            exchange=item.spot_exchange,
            symbol=spot_alias.raw_symbol,
            market_type=MarketType.SPOT,
        )
        future_leg = PairSpreadLegQuery(
            exchange=item.future_exchange,
            symbol=future_alias.raw_symbol,
            market_type=MarketType.FUTURE,
        )
        service = self.query_service_factory()
        try:
            result = await service.query(
                spot_leg,
                future_leg,
                hours=item.lookback_hours,
                interval_minutes=1,
                interval_seconds=60,
                leg2_multiplier=item.future_multiplier,
                include_current=True,
            )
        except PairSpreadQueryError:
            raise
        finally:
            close = getattr(service, "aclose", None)
            if close is not None:
                await close()
        result = apply_pair_spread_symbol_aliases(
            result,
            leg1_alias=spot_alias,
            leg2_alias=future_alias,
        )

        gate_stats: list[GateContractStatPoint] = []
        warnings: list[str] = []
        if item.future_exchange == "gate":
            try:
                gate_stats = await self.gate_stats_client.fetch(
                    future_alias.raw_symbol,
                    start=result.first_seen_at or result.observed_at - timedelta(hours=item.lookback_hours),
                    end=result.observed_at,
                )
            except Exception as exc:  # noqa: BLE001 - OI is a signal enhancer, not a hard blocker.
                warnings.append(f"Gate OI/多空历史获取失败: {_error_message(exc)}")
        else:
            warnings.append(f"{item.future_exchange} 暂未接入小时级 OI/多空历史，仅展示当前快照")

        return build_negative_basis_analysis(item, result, gate_stats=gate_stats, warnings=warnings)

    async def status(self) -> NegativeBasisMonitorStatus:
        watchlist = await self.repo.list_watch_items()
        settings = await self.repo.get_auto_scan_settings()
        auto_candidates = (
            [
                candidate
                for candidate in self._auto_candidates
                if not _auto_candidate_blocked(candidate, settings)
            ]
            if settings.enabled
            else []
        )
        return NegativeBasisMonitorStatus(
            running=self.running(),
            auto_scan_enabled=self.snapshot_store is not None and settings.enabled,
            auto_scan_settings=settings,
            auto_scan_last_at=self._last_auto_scan_at,
            auto_scan_error=self._auto_scan_error,
            auto_candidate_count=len(auto_candidates),
            auto_candidates=auto_candidates,
            watch_count=len(watchlist),
            enabled_watch_count=sum(
                1 for item in watchlist if item.enabled and not _auto_watch_blocked(item, settings)
            ),
            sample_count=await self.repo.count_samples(),
            event_count=await self.repo.count_events(),
            latest_error=self._latest_error,
            watchlist=watchlist,
            latest_samples=await self.repo.list_samples(limit=80),
            latest_events=await self.repo.list_events(limit=20),
        )

    def _should_alert(
        self,
        item: NegativeBasisWatchItem,
        result: NegativeBasisAnalysisResult,
    ) -> bool:
        rank = NEGATIVE_BASIS_LEVEL_ORDER[result.signal_level]
        min_rank = NEGATIVE_BASIS_LEVEL_ORDER[item.alert_min_level]
        if rank < min_rank or rank == 0:
            return False
        last_sent = self._last_sent_at.get(item.id)
        if last_sent is None:
            return True
        elapsed = (result.observed_at - last_sent).total_seconds()
        last_level = self._last_sent_level.get(item.id, "none")
        escalated = rank > NEGATIVE_BASIS_LEVEL_ORDER[last_level]
        return escalated or elapsed >= item.cooldown_seconds


def _sample_from_analysis(result: NegativeBasisAnalysisResult) -> NegativeBasisSignalSample:
    current = result.current
    latest_hour = _latest_row_with_context(result.hourly_stats)
    return NegativeBasisSignalSample(
        watch_id=result.item.id,
        observed_at=result.observed_at,
        symbol=result.item.symbol,
        spot_exchange=result.item.spot_exchange,
        future_exchange=result.item.future_exchange,
        signal_level=result.signal_level,
        score=result.score,
        spot_premium_pct=result.spot_premium.current,
        spot_price=current.spot_leg.price if current is not None else None,
        future_price=current.future_leg.price if current is not None else None,
        spot_volume_24h_usdt=current.spot_leg.volume_24h_usdt if current is not None else None,
        future_volume_24h_usdt=current.future_leg.volume_24h_usdt if current is not None else None,
        open_interest_usdt=current.future_leg.open_interest_usdt if current is not None else None,
        open_interest_change_pct=latest_hour.open_interest_change_pct if latest_hour is not None else None,
        long_account_pct=current.future_leg.long_account_pct if current is not None else None,
        short_account_pct=current.future_leg.short_account_pct if current is not None else None,
        long_short_ratio=current.future_leg.long_short_ratio if current is not None else None,
        funding_rate_pct=current.future_leg.funding_rate_pct if current is not None else None,
        reasons=result.reasons,
    )


def _alert_message(result: NegativeBasisAnalysisResult) -> str:
    current = result.current
    latest_hour = _latest_row_with_context(result.hourly_stats)
    spot_price = current.spot_leg.price if current is not None else None
    future_price = current.future_leg.price if current is not None else None
    oi = current.future_leg.open_interest_usdt if current is not None else None
    funding = current.future_leg.funding_rate_pct if current is not None else None
    long_pct = current.future_leg.long_account_pct if current is not None else None
    short_pct = current.future_leg.short_account_pct if current is not None else None
    volume_growth = latest_hour.spot_volume_growth if latest_hour is not None else None
    oi_growth = latest_hour.open_interest_change_pct if latest_hour is not None else None
    reasons = "；".join(result.reasons[:4]) if result.reasons else "-"
    return (
        f"负基差埋伏信号 [{result.signal_level.upper()}] {result.item.symbol}\n"
        f"方向: {result.item.spot_exchange} 现货 / {result.item.future_exchange} 合约\n"
        f"现货溢价: {_fmt_pct(result.spot_premium.current, 3)}，评分 {result.score:.1f}\n"
        f"价格: 现货 {_fmt_number(spot_price)} / 合约 {_fmt_number(future_price)}\n"
        f"1h现货量能: {_fmt_ratio(volume_growth)}，OI变化: {_fmt_pct(oi_growth, 2)}，OI: {_fmt_usdt(oi)}\n"
        f"多空: 多 {_fmt_pct(long_pct, 2)} / 空 {_fmt_pct(short_pct, 2)}，资金费率: {_fmt_pct(funding, 4)}\n"
        f"原因: {reasons}\n"
        f"时间: {result.observed_at.astimezone(DISPLAY_TZ).strftime('%Y-%m-%d %H:%M:%S')}"
    )


def _fmt_number(value: float | None) -> str:
    if value is None or not isfinite(value):
        return "-"
    if abs(value) >= 100:
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if abs(value) >= 1:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{value:.8g}"


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None or not isfinite(value):
        return "-"
    return f"{value:+.{digits}f}%"


def _fmt_ratio(value: float | None) -> str:
    if value is None or not isfinite(value):
        return "-"
    return f"{value:.2f}x"


def _fmt_usdt(value: float | None) -> str:
    if value is None or not isfinite(value):
        return "-"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B USDT"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M USDT"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K USDT"
    return f"{value:.0f} USDT"


def _error_message(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{exc.__class__.__name__}: {text}" if text else exc.__class__.__name__
