from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, timezone
from math import ceil, isfinite
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.exchanges.base import (
    DEFAULT_HEADERS,
    DEFAULT_LIMITS,
    next_aligned_funding_time,
    normalize_usdt_symbol,
    parse_datetime_ms,
    parse_datetime_seconds,
    parse_float,
    utc_now,
)
from app.models.market import MarketType
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadCurrentSnapshot,
    PairSpreadFundingHistoryResult,
    PairSpreadFundingPoint,
    PairSpreadHourlyVolumePoint,
    PairSpreadKlinePoint,
    PairSpreadLegQuery,
    PairSpreadPoint,
    PairSpreadPriceField,
    PairSpreadQueryResult,
    PairSpreadRealtimeFundingPoint,
    PairSpreadValueStats,
    SUPPORTED_SYMBOL_SPREAD_EXCHANGES,
    SymbolExchangePriceSnapshot,
    SymbolSpreadPoint,
    SymbolSpreadQueryResult,
    SymbolSpreadSeries,
    normalize_binance_alpha_symbol,
)

MINUTE_MS = 60_000
HYPERLIQUID_CANDLE_LIMIT = 5000
PAIR_SPREAD_REALTIME_MAX_POINTS = 4000
PAIR_SPREAD_HISTORICAL_INTERVAL_SECONDS = (60, 300, 900)
PAIR_SPREAD_TIMEOUT = httpx.Timeout(18.0, connect=3.0, read=14.0, write=5.0, pool=5.0)
DISPLAY_TZ = timezone(timedelta(hours=8))
BINANCE_ALPHA_KLINES_URL = "https://www.binance.com/bapi/defi/v1/public/alpha-trade/klines"
BINANCE_ALPHA_TICKER_URL = "https://www.binance.com/bapi/defi/v1/public/alpha-trade/ticker"


def _binance_alpha_error(payload: Any) -> str:
    if not isinstance(payload, dict):
        return f"Binance Alpha 接口返回格式异常: {type(payload).__name__}"
    message = payload.get("message") or payload.get("messageDetail") or "unknown error"
    code = payload.get("code")
    return f"Binance Alpha 接口失败: {message} (code={code})"


def _is_start_after_end_error(exc: Exception) -> bool:
    return "Start time is greater than end time" in str(exc)


class PairSpreadQueryError(RuntimeError):
    pass


def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _interval_ms(interval_minutes: int) -> int:
    return interval_minutes * MINUTE_MS


def _interval_minutes_from_seconds(interval_seconds: int) -> int:
    return max(1, interval_seconds // 60)


def _floor_minute(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(second=0, microsecond=0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _floor_interval(value: datetime, interval_seconds: int) -> datetime:
    timestamp = int(value.astimezone(UTC).timestamp())
    bucket_timestamp = timestamp - (timestamp % interval_seconds)
    return datetime.fromtimestamp(bucket_timestamp, UTC)


def _bucket_datetime_ms(value: Any) -> datetime | None:
    parsed = parse_datetime_ms(value)
    return _floor_minute(parsed) if parsed is not None else None


def _bucket_datetime_seconds(value: Any) -> datetime | None:
    parsed = parse_datetime_seconds(value)
    return _floor_minute(parsed) if parsed is not None else None


def _compact_symbol(symbol: str) -> str:
    compact, _, _ = normalize_usdt_symbol(symbol)
    return compact


def _okx_inst_id(symbol: str) -> str:
    _, base, quote = normalize_usdt_symbol(symbol)
    return f"{base}-{quote}-SWAP"


def _okx_spot_inst_id(symbol: str) -> str:
    _, base, quote = normalize_usdt_symbol(symbol)
    return f"{base}-{quote}"


def _gate_contract(symbol: str) -> str:
    _, base, quote = normalize_usdt_symbol(symbol)
    return f"{base}_{quote}"


def _hyperliquid_coin(symbol: str) -> str:
    _, base, _ = normalize_usdt_symbol(symbol)
    return base


def _hyperliquid_base_from_raw(raw_coin: str) -> str:
    return raw_coin.split(":", 1)[1] if ":" in raw_coin else raw_coin


def _positive(value: float | None) -> float | None:
    if value is None or not isfinite(value) or value <= 0:
        return None
    return value


def _nonnegative(value: float | None) -> float | None:
    if value is None or not isfinite(value) or value < 0:
        return None
    return value


def _ratio_to_pct(value: float | None) -> float | None:
    if value is None or not isfinite(value):
        return None
    return value * 100


def _account_ratio_to_pct(value: float | None) -> float | None:
    parsed = _nonnegative(value)
    if parsed is None:
        return None
    return parsed * 100 if parsed <= 1 else parsed


def _long_short_ratio(long_value: float | None, short_value: float | None) -> float | None:
    long_value = _nonnegative(long_value)
    short_value = _positive(short_value)
    if long_value is None or short_value is None:
        return None
    return long_value / short_value


def _open_interest_usdt(size: float | None, price: float | None) -> float | None:
    size = _nonnegative(size)
    price = _positive(price)
    if size is None or price is None:
        return None
    return size * price


def _rate_pct_from_row(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        parsed = parse_float(row.get(key))
        if parsed is not None:
            return _ratio_to_pct(parsed)
    return None


def _symmetric_rate_limit_pct_from_row(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        parsed = parse_float(row.get(key))
        if parsed is not None:
            return abs(_ratio_to_pct(parsed))
    return None


def _funding_interval_hours_between(
    funding_time: datetime | None,
    next_funding_time: datetime | None,
) -> float | None:
    if funding_time is None or next_funding_time is None:
        return None
    seconds = (next_funding_time - funding_time).total_seconds()
    if seconds <= 0:
        return None
    return seconds / 3600


def _funding_interval_hours_from_row(row: dict[str, Any]) -> float | None:
    return _funding_interval_hours_between(
        parse_datetime_ms(row.get("fundingTime")),
        parse_datetime_ms(row.get("nextFundingTime")),
    )


def _next_aligned_funding_time_from_hours(now: datetime, interval_hours: float | None) -> datetime | None:
    interval = _positive(interval_hours)
    if interval is None:
        return None
    rounded = int(round(interval))
    if rounded <= 0 or abs(interval - rounded) > 1e-9:
        return None
    return next_aligned_funding_time(now, rounded)


def _mid_price(bid: float | None, ask: float | None) -> float | None:
    bid = _positive(bid)
    ask = _positive(ask)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def _spread_pct(spread_abs: float, leg1_price: float, leg2_price: float) -> float:
    midpoint = (leg1_price + leg2_price) / 2
    return spread_abs / midpoint * 100


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


def _dedupe_sorted(points: list[PairSpreadKlinePoint]) -> list[PairSpreadKlinePoint]:
    by_bucket: dict[datetime, PairSpreadKlinePoint] = {}
    for point in points:
        by_bucket[point.bucket_at] = point
    return [by_bucket[key] for key in sorted(by_bucket)]


def _display_hour_bucket(value: datetime) -> datetime:
    return value.astimezone(DISPLAY_TZ).replace(minute=0, second=0, microsecond=0).astimezone(UTC)


def _hourly_volume_totals(points: list[PairSpreadKlinePoint]) -> dict[datetime, float]:
    totals: dict[datetime, float] = {}
    for point in points:
        volume = _nonnegative(point.volume_usdt)
        if volume is None:
            continue
        bucket_at = _display_hour_bucket(point.bucket_at)
        totals[bucket_at] = totals.get(bucket_at, 0) + volume
    return totals


def _duration_text(hours: int) -> str:
    if hours % 24 == 0:
        return f"{hours // 24}天"
    return f"{hours}小时"


def _display_time(value: datetime) -> str:
    return value.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M")


def _query_window_hours(hours: int) -> list[int]:
    candidates = [hours, 720, 168, 72, 24, 12, 6, 3, 1]
    return list(dict.fromkeys(candidate for candidate in candidates if 0 < candidate <= hours))


def _append_unique(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)


def _interval_text(interval_seconds: int) -> str:
    if interval_seconds % 60 == 0:
        return f"{interval_seconds // 60}分钟"
    return f"{interval_seconds}秒"


def _market_type_text(market_type: MarketType) -> str:
    return "现货" if market_type == MarketType.SPOT else "合约"


def _extend_unique(items: list[str], new_items: list[str]) -> None:
    for item in new_items:
        _append_unique(items, item)


def _warnings_text(items: list[str]) -> str:
    unique_items = list(dict.fromkeys(items))
    return "; ".join(unique_items)


_REALTIME_PAIR_SPREAD_CACHE: dict[str, list[PairSpreadPoint]] = {}
# 实时资金费率由每次价差查询抓到的当前快照累积，和交易所整点结算历史分开保存。
_REALTIME_PAIR_FUNDING_CACHE: dict[str, list[PairSpreadRealtimeFundingPoint]] = {}
# 单标的跨交易所秒级价差没有统一历史源，使用每次查询抓到的当前价形成本地采样线。
_REALTIME_SYMBOL_SPREAD_CACHE: dict[str, dict[str, list[SymbolSpreadPoint]]] = {}


def _realtime_cache_key(
    leg1: PairSpreadLegQuery,
    leg2: PairSpreadLegQuery,
    *,
    leg2_multiplier: float,
    interval_seconds: int,
) -> str:
    return "|".join(
        (
            leg1.exchange,
            leg1.market_type,
            leg1.symbol,
            leg2.exchange,
            leg2.market_type,
            leg2.symbol,
            f"{leg2_multiplier:.12g}",
            str(interval_seconds),
        )
    )


def _append_realtime_point(
    points: list[PairSpreadPoint],
    point: PairSpreadPoint,
) -> list[PairSpreadPoint]:
    if points and points[-1].bucket_at == point.bucket_at:
        points[-1] = point
    else:
        points.append(point)
    if len(points) > PAIR_SPREAD_REALTIME_MAX_POINTS:
        del points[:-PAIR_SPREAD_REALTIME_MAX_POINTS]
    return points


def _realtime_funding_point(
    current: PairSpreadCurrentSnapshot,
    bucket_at: datetime,
) -> PairSpreadRealtimeFundingPoint | None:
    left_rate = current.leg1.funding_rate_pct
    right_rate = current.leg2.funding_rate_pct
    left_rate = left_rate if left_rate is not None and isfinite(left_rate) else None
    right_rate = right_rate if right_rate is not None and isfinite(right_rate) else None
    if left_rate is None and right_rate is None:
        return None
    normalized_left = left_rate or 0
    normalized_right = right_rate or 0
    return PairSpreadRealtimeFundingPoint(
        bucket_at=bucket_at,
        left_rate_pct=normalized_left,
        right_rate_pct=normalized_right,
        net_rate_pct=normalized_right - normalized_left,
    )


def _append_realtime_funding_point(
    points: list[PairSpreadRealtimeFundingPoint],
    point: PairSpreadRealtimeFundingPoint,
) -> list[PairSpreadRealtimeFundingPoint]:
    if points and points[-1].bucket_at == point.bucket_at:
        points[-1] = point
    else:
        points.append(point)
    if len(points) > PAIR_SPREAD_REALTIME_MAX_POINTS:
        del points[:-PAIR_SPREAD_REALTIME_MAX_POINTS]
    return points


def _realtime_funding_points_from_current(
    cache_key: str,
    current: PairSpreadCurrentSnapshot | None,
    *,
    observed_at: datetime,
    interval_seconds: int,
    hours: int,
) -> list[PairSpreadRealtimeFundingPoint]:
    if current is None:
        return []
    cached_funding = _REALTIME_PAIR_FUNDING_CACHE.setdefault(cache_key, [])
    current_funding_point = _realtime_funding_point(
        current,
        _floor_interval(observed_at, interval_seconds),
    )
    if current_funding_point is not None:
        _append_realtime_funding_point(cached_funding, current_funding_point)
    cutoff = observed_at - timedelta(hours=hours)
    points = [point for point in cached_funding if point.bucket_at >= cutoff]
    if len(points) != len(cached_funding):
        _REALTIME_PAIR_FUNDING_CACHE[cache_key] = points
    return points


def _hyperliquid_history_limit_warning(
    exchanges: set[str],
    *,
    hours: int,
    interval_minutes: int,
) -> str | None:
    expected_points = ceil(hours * 60 / interval_minutes)
    if "hyperliquid" not in exchanges or expected_points <= HYPERLIQUID_CANDLE_LIMIT:
        return None
    max_days = HYPERLIQUID_CANDLE_LIMIT * interval_minutes / 60 / 24
    recommended = next(
        (
            candidate
            for candidate in (1, 5, 15)
            if ceil(hours * 60 / candidate) <= HYPERLIQUID_CANDLE_LIMIT
        ),
        None,
    )
    recommendation = (
        f"；切换到{recommended}分钟可覆盖{_duration_text(hours)}"
        if recommended is not None
        else ""
    )
    return (
        f"Hyperliquid 官方接口只提供最近{HYPERLIQUID_CANDLE_LIMIT}根K线："
        f"{_duration_text(hours)}的{interval_minutes}分钟周期需要约{expected_points}根，"
        f"当前最多约{max_days:.1f}天{recommendation}。"
    )


def build_pair_spread_points(
    leg1_klines: list[PairSpreadKlinePoint],
    leg2_klines: list[PairSpreadKlinePoint],
    *,
    leg2_multiplier: float = 1.0,
) -> list[PairSpreadPoint]:
    if leg2_multiplier <= 0:
        raise ValueError("leg2_multiplier must be positive")
    leg1_by_time = {point.bucket_at: point.close for point in leg1_klines}
    leg2_by_time = {point.bucket_at: point.close for point in leg2_klines}
    points: list[PairSpreadPoint] = []
    for bucket_at in sorted(leg1_by_time.keys() & leg2_by_time.keys()):
        leg1_close = leg1_by_time[bucket_at]
        leg2_close = leg2_by_time[bucket_at] / leg2_multiplier
        if leg1_close <= 0 or leg2_close <= 0:
            continue
        spread_abs = leg2_close - leg1_close
        points.append(
            PairSpreadPoint(
                bucket_at=bucket_at,
                leg1_close=leg1_close,
                leg2_close=leg2_close,
                spread_abs=spread_abs,
                spread_pct=_spread_pct(spread_abs, leg1_close, leg2_close),
            )
        )
    return points


def build_pair_hourly_volume_points(
    leg1_klines: list[PairSpreadKlinePoint],
    leg2_klines: list[PairSpreadKlinePoint],
) -> list[PairSpreadHourlyVolumePoint]:
    leg1_by_hour = _hourly_volume_totals(leg1_klines)
    leg2_by_hour = _hourly_volume_totals(leg2_klines)
    volume_hours = sorted(leg1_by_hour.keys() | leg2_by_hour.keys())
    if not volume_hours:
        return []

    points: list[PairSpreadHourlyVolumePoint] = []
    bucket_at = volume_hours[0]
    last_bucket_at = volume_hours[-1]
    while bucket_at <= last_bucket_at:
        leg1_volume = leg1_by_hour.get(bucket_at)
        leg2_volume = leg2_by_hour.get(bucket_at)
        total_volume = (
            (leg1_volume or 0) + (leg2_volume or 0)
            if leg1_volume is not None or leg2_volume is not None
            else None
        )
        volume_diff = (
            leg2_volume - leg1_volume
            if leg1_volume is not None and leg2_volume is not None
            else None
        )
        volume_ratio = (
            leg2_volume / leg1_volume
            if leg1_volume is not None
            and leg1_volume > 0
            and leg2_volume is not None
            else None
        )
        points.append(
            PairSpreadHourlyVolumePoint(
                bucket_at=bucket_at,
                leg1_volume_usdt=leg1_volume,
                leg2_volume_usdt=leg2_volume,
                total_volume_usdt=total_volume,
                volume_diff_usdt=volume_diff,
                volume_ratio=volume_ratio,
            )
        )
        bucket_at += timedelta(hours=1)
    return points


def build_symbol_spread_points(
    base_klines: list[PairSpreadKlinePoint],
    exchange_klines: list[PairSpreadKlinePoint],
) -> list[SymbolSpreadPoint]:
    base_by_time = {point.bucket_at: point.close for point in base_klines}
    exchange_by_time = {point.bucket_at: point.close for point in exchange_klines}
    points: list[SymbolSpreadPoint] = []
    for bucket_at in sorted(base_by_time.keys() & exchange_by_time.keys()):
        base_close = base_by_time[bucket_at]
        exchange_close = exchange_by_time[bucket_at]
        if base_close <= 0 or exchange_close <= 0:
            continue
        spread_abs = exchange_close - base_close
        points.append(
            SymbolSpreadPoint(
                bucket_at=bucket_at,
                base_close=base_close,
                exchange_close=exchange_close,
                spread_abs=spread_abs,
                spread_pct=_spread_pct(spread_abs, base_close, exchange_close),
            )
        )
    return points


def _symbol_spread_stats(points: list[SymbolSpreadPoint], field: str) -> PairSpreadValueStats:
    return _stats([getattr(point, field) for point in points])


def _symbol_spread_series(
    *,
    exchange: str,
    symbol: str,
    market_type: MarketType,
    points: list[SymbolSpreadPoint],
    current: SymbolSpreadPoint | None = None,
) -> SymbolSpreadSeries:
    stats_points = [*points]
    if current is not None:
        if stats_points and stats_points[-1].bucket_at == current.bucket_at:
            stats_points[-1] = current
        elif not stats_points or stats_points[-1].bucket_at < current.bucket_at:
            stats_points.append(current)
    return SymbolSpreadSeries(
        exchange=exchange,
        symbol=symbol,
        market_type=market_type,
        point_count=len(points),
        first_seen_at=points[0].bucket_at if points else None,
        last_seen_at=points[-1].bucket_at if points else None,
        spread_abs=_symbol_spread_stats(stats_points, "spread_abs"),
        spread_pct=_symbol_spread_stats(stats_points, "spread_pct"),
        current=current,
        points=points,
    )


def _symbol_current_price_snapshot(leg: PairSpreadCurrentLeg) -> SymbolExchangePriceSnapshot:
    return SymbolExchangePriceSnapshot(
        exchange=leg.exchange,
        symbol=leg.symbol,
        market_type=leg.market_type,
        raw_symbol=leg.raw_symbol,
        price=leg.price,
        price_field=leg.price_field,
        funding_rate_pct=leg.funding_rate_pct,
        timestamp=leg.timestamp,
    )


def _symbol_current_spread_point(
    base_leg: PairSpreadCurrentLeg,
    exchange_leg: PairSpreadCurrentLeg,
    bucket_at: datetime,
) -> SymbolSpreadPoint:
    spread_abs = exchange_leg.price - base_leg.price
    return SymbolSpreadPoint(
        bucket_at=bucket_at,
        base_close=base_leg.price,
        exchange_close=exchange_leg.price,
        spread_abs=spread_abs,
        spread_pct=_spread_pct(spread_abs, base_leg.price, exchange_leg.price),
    )


def _append_symbol_spread_point(
    points: list[SymbolSpreadPoint],
    point: SymbolSpreadPoint,
) -> list[SymbolSpreadPoint]:
    if points and points[-1].bucket_at == point.bucket_at:
        points[-1] = point
    else:
        points.append(point)
    if len(points) > PAIR_SPREAD_REALTIME_MAX_POINTS:
        del points[:-PAIR_SPREAD_REALTIME_MAX_POINTS]
    return points


def _symbol_spread_cache_key(
    *,
    symbol: str,
    market_type: MarketType,
    base_exchange: str,
    exchanges: list[str],
    interval_seconds: int,
) -> str:
    return "|".join(
        (
            _compact_symbol(symbol),
            str(market_type),
            base_exchange,
            ",".join(exchanges),
            str(interval_seconds),
        )
    )


def _normalize_symbol_spread_exchanges(
    exchanges: list[str] | None,
    base_exchange: str,
) -> list[str]:
    allowed = set(SUPPORTED_SYMBOL_SPREAD_EXCHANGES)
    requested = exchanges or list(SUPPORTED_SYMBOL_SPREAD_EXCHANGES)
    normalized: list[str] = []
    for exchange in [base_exchange, *requested]:
        item = exchange.strip().lower()
        if item in allowed and item not in normalized:
            normalized.append(item)
    return normalized


class PairSpreadQueryService:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=PAIR_SPREAD_TIMEOUT,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            limits=DEFAULT_LIMITS,
            http2=False,
            trust_env=True,
        )
        self._owns_client = client is None
        self._hyperliquid_dex_names: list[str] | None = None
        self._hyperliquid_meta_contexts_by_dex: dict[str, tuple[dict[str, Any], list[Any]]] = {}
        self._hyperliquid_coin_by_base: dict[str, tuple[str, str]] = {}

    async def aclose(self) -> None:
        if self._owns_client and not self.client.is_closed:
            await self.client.aclose()

    async def query_funding_history(
        self,
        leg1: PairSpreadLegQuery,
        leg2: PairSpreadLegQuery,
        *,
        start: datetime,
        end: datetime,
    ) -> PairSpreadFundingHistoryResult:
        start_at = _as_utc(start)
        end_at = _as_utc(end)
        if start_at > end_at:
            raise PairSpreadQueryError("开始时间不能晚于结束时间")

        warnings: list[str] = []
        funding1, funding2 = await asyncio.gather(
            self._fetch_funding_with_warning(leg1, start_at, end_at, warnings),
            self._fetch_funding_with_warning(leg2, start_at, end_at, warnings),
        )
        funding_history = sorted(
            (
                point
                for point in [*funding1, *funding2]
                if start_at <= _as_utc(point.funding_time) <= end_at
            ),
            key=lambda item: item.funding_time,
        )
        return PairSpreadFundingHistoryResult(
            leg1=leg1,
            leg2=leg2,
            start_at=start_at,
            end_at=end_at,
            funding_history=funding_history,
            warnings=warnings,
        )

    async def query(
        self,
        leg1: PairSpreadLegQuery,
        leg2: PairSpreadLegQuery,
        *,
        hours: int,
        interval_minutes: int = 1,
        interval_seconds: int | None = None,
        leg2_multiplier: float = 1.0,
        now: datetime | None = None,
        include_current: bool = True,
    ) -> PairSpreadQueryResult:
        if leg2_multiplier <= 0:
            raise PairSpreadQueryError("leg2_multiplier must be positive")
        resolved_interval_seconds = interval_seconds or interval_minutes * 60
        if resolved_interval_seconds not in PAIR_SPREAD_HISTORICAL_INTERVAL_SECONDS:
            if not include_current:
                raise PairSpreadQueryError("秒级周期只支持实时采样，历史对比请使用 1 分钟、5 分钟或 15 分钟周期")
            return await self._query_realtime(
                leg1,
                leg2,
                hours=hours,
                interval_seconds=resolved_interval_seconds,
                leg2_multiplier=leg2_multiplier,
                now=now,
            )
        interval_minutes = _interval_minutes_from_seconds(resolved_interval_seconds)
        observed_at = now or utc_now()
        end = _floor_minute(observed_at)
        requested_start = end - timedelta(hours=hours)
        warnings: list[str] = []
        kline_keys = list(
            dict.fromkeys(
                (
                    (leg1.exchange, leg1.symbol, leg1.market_type),
                    (leg2.exchange, leg2.symbol, leg2.market_type),
                )
            )
        )

        points: list[PairSpreadPoint] = []
        leg1_klines: list[PairSpreadKlinePoint] = []
        leg2_klines: list[PairSpreadKlinePoint] = []
        used_start = requested_start
        failed_window_warnings: list[str] = []

        for window_hours in _query_window_hours(hours):
            window_start = end - timedelta(hours=window_hours)
            window_warnings: list[str] = []
            kline_results = await asyncio.gather(
                *(
                    self._fetch_klines_with_warning(
                        exchange,
                        symbol,
                        market_type,
                        window_start,
                        end,
                        interval_minutes,
                        window_warnings,
                    )
                    for exchange, symbol, market_type in kline_keys
                )
            )
            klines_by_key = dict(zip(kline_keys, kline_results, strict=True))
            candidate_leg1_klines = klines_by_key[(leg1.exchange, leg1.symbol, leg1.market_type)]
            candidate_leg2_klines = klines_by_key[(leg2.exchange, leg2.symbol, leg2.market_type)]
            candidate_points = build_pair_spread_points(
                candidate_leg1_klines,
                candidate_leg2_klines,
                leg2_multiplier=leg2_multiplier,
            )
            if candidate_points:
                points = candidate_points
                leg1_klines = candidate_leg1_klines
                leg2_klines = candidate_leg2_klines
                used_start = window_start
                if window_hours != hours:
                    _append_unique(
                        warnings,
                        f"请求{_duration_text(hours)}没有拿到可对齐K线，已自动改查最近{_duration_text(window_hours)}。"
                    )
                _extend_unique(warnings, window_warnings)
                break
            _extend_unique(failed_window_warnings, window_warnings)

        if not points:
            suffix = f": {_warnings_text(failed_window_warnings)}" if failed_window_warnings else ""
            raise PairSpreadQueryError(f"没有拿到可对齐的分钟K线{suffix}")

        earliest_expected = used_start + timedelta(minutes=interval_minutes)
        if points[0].bucket_at > earliest_expected:
            history_limit_warning = _hyperliquid_history_limit_warning(
                {
                    leg.exchange
                    for leg in (leg1, leg2)
                    if leg.market_type == MarketType.FUTURE
                },
                hours=hours,
                interval_minutes=interval_minutes,
            )
            warnings.insert(
                0,
                history_limit_warning
                or (
                    f"请求{_duration_text(hours)}，最早可对齐K线为"
                    f"{_display_time(points[0].bucket_at)}，已按可获取数据展示。"
                ),
            )

        current: PairSpreadCurrentSnapshot | None = None
        funding1: list[PairSpreadFundingPoint] = []
        funding2: list[PairSpreadFundingPoint] = []
        if include_current:
            funding_start = points[0].bucket_at
            funding_end = points[-1].bucket_at
            current_leg1, current_leg2, funding1, funding2 = await asyncio.gather(
                self._fetch_current_with_warning(leg1, warnings),
                self._fetch_current_with_warning(leg2, warnings),
                self._fetch_funding_with_warning(leg1, funding_start, funding_end, warnings),
                self._fetch_funding_with_warning(leg2, funding_start, funding_end, warnings),
            )
            current = (
                self._build_current_snapshot(
                    current_leg1,
                    _scale_current_leg(current_leg2, leg2_multiplier),
                    observed_at,
                )
                if current_leg1 is not None and current_leg2 is not None
                else None
            )
        realtime_funding = _realtime_funding_points_from_current(
            _realtime_cache_key(
                leg1,
                leg2,
                leg2_multiplier=leg2_multiplier,
                interval_seconds=resolved_interval_seconds,
            ),
            current,
            observed_at=observed_at,
            interval_seconds=resolved_interval_seconds,
            hours=hours,
        )

        return PairSpreadQueryResult(
            leg1=leg1,
            leg2=leg2,
            hours=hours,
            interval_minutes=interval_minutes,
            interval_seconds=resolved_interval_seconds,
            leg2_multiplier=leg2_multiplier,
            observed_at=observed_at,
            point_count=len(points),
            first_seen_at=points[0].bucket_at,
            last_seen_at=points[-1].bucket_at,
            spread_abs=_stats([point.spread_abs for point in points]),
            spread_pct=_stats([point.spread_pct for point in points]),
            current=current,
            points=points,
            hourly_volume=build_pair_hourly_volume_points(leg1_klines, leg2_klines),
            funding_history=sorted(funding1 + funding2, key=lambda item: item.funding_time),
            realtime_funding=realtime_funding,
            warnings=warnings,
        )

    async def query_symbol_spreads(
        self,
        symbol: str,
        *,
        market_type: MarketType = MarketType.FUTURE,
        base_exchange: str = "binance",
        exchanges: list[str] | None = None,
        hours: int,
        interval_seconds: int = 60,
        now: datetime | None = None,
        include_current: bool = True,
    ) -> SymbolSpreadQueryResult:
        if interval_seconds not in PAIR_SPREAD_HISTORICAL_INTERVAL_SECONDS:
            if not include_current:
                raise PairSpreadQueryError("秒级周期只支持实时采样，历史对比请使用 1 分钟、5 分钟或 15 分钟周期")
            return await self._query_symbol_spreads_realtime(
                symbol,
                market_type=market_type,
                base_exchange=base_exchange,
                exchanges=exchanges,
                hours=hours,
                interval_seconds=interval_seconds,
                now=now,
            )

        interval_minutes = _interval_minutes_from_seconds(interval_seconds)
        observed_at = now or utc_now()
        end = _floor_minute(observed_at)
        requested_start = end - timedelta(hours=hours)
        normalized_exchanges = _normalize_symbol_spread_exchanges(exchanges, base_exchange)
        if len(normalized_exchanges) < 2:
            raise PairSpreadQueryError("至少需要两个支持的主流交易所才能画跨所价差")

        legs_by_exchange = {
            exchange: PairSpreadLegQuery(exchange=exchange, symbol=symbol, market_type=market_type)
            for exchange in normalized_exchanges
        }
        query_symbol = next(iter(legs_by_exchange.values())).symbol
        requested_base_exchange = base_exchange.strip().lower()
        warnings: list[str] = []
        failed_window_warnings: list[str] = []
        effective_base_exchange = requested_base_exchange
        raw_series_points: list[tuple[str, list[SymbolSpreadPoint]]] = []
        used_start = requested_start

        for window_hours in _query_window_hours(hours):
            window_start = end - timedelta(hours=window_hours)
            window_warnings: list[str] = []
            kline_results = await asyncio.gather(
                *(
                    self._fetch_klines_with_warning(
                        leg.exchange,
                        leg.symbol,
                        leg.market_type,
                        window_start,
                        end,
                        interval_minutes,
                        window_warnings,
                    )
                    for leg in legs_by_exchange.values()
                )
            )
            klines_by_exchange = dict(zip(legs_by_exchange.keys(), kline_results, strict=True))
            candidate_base_exchange = (
                requested_base_exchange
                if klines_by_exchange.get(requested_base_exchange)
                else next((exchange for exchange in normalized_exchanges if klines_by_exchange.get(exchange)), None)
            )
            if candidate_base_exchange is None:
                _extend_unique(failed_window_warnings, window_warnings)
                continue

            base_klines = klines_by_exchange[candidate_base_exchange]
            candidate_series: list[tuple[str, list[SymbolSpreadPoint]]] = []
            for exchange in normalized_exchanges:
                if exchange == candidate_base_exchange:
                    continue
                exchange_klines = klines_by_exchange.get(exchange, [])
                if not exchange_klines:
                    continue
                points = build_symbol_spread_points(base_klines, exchange_klines)
                if points:
                    candidate_series.append((exchange, points))
                else:
                    _append_unique(
                        window_warnings,
                        f"{exchange} 与基准 {candidate_base_exchange} 没有可对齐的K线。",
                    )

            if candidate_series:
                raw_series_points = candidate_series
                effective_base_exchange = candidate_base_exchange
                used_start = window_start
                if window_hours != hours:
                    _append_unique(
                        warnings,
                        f"请求{_duration_text(hours)}没有拿到可对齐K线，已自动改查最近{_duration_text(window_hours)}。",
                    )
                if effective_base_exchange != requested_base_exchange:
                    _append_unique(
                        warnings,
                        f"基准 {requested_base_exchange} 没有可用K线，已改用 {effective_base_exchange} 做基准。",
                    )
                _extend_unique(warnings, window_warnings)
                break
            _extend_unique(failed_window_warnings, window_warnings)

        if not raw_series_points:
            suffix = f": {_warnings_text(failed_window_warnings)}" if failed_window_warnings else ""
            raise PairSpreadQueryError(f"没有拿到可用于跨所对比的分钟K线{suffix}")

        all_points = [point for _, points in raw_series_points for point in points]
        earliest_expected = used_start + timedelta(minutes=interval_minutes)
        if all_points and min(point.bucket_at for point in all_points) > earliest_expected:
            history_limit_warning = _hyperliquid_history_limit_warning(
                {
                    exchange
                    for exchange, leg in legs_by_exchange.items()
                    if leg.market_type == MarketType.FUTURE
                },
                hours=hours,
                interval_minutes=interval_minutes,
            )
            warnings.insert(
                0,
                history_limit_warning
                or (
                    f"请求{_duration_text(hours)}，最早可对齐K线为"
                    f"{_display_time(min(point.bucket_at for point in all_points))}，已按可获取数据展示。"
                ),
            )

        current_prices: list[SymbolExchangePriceSnapshot] = []
        current_by_exchange: dict[str, PairSpreadCurrentLeg] = {}
        if include_current:
            current_results = await asyncio.gather(
                *(
                    self._fetch_current_with_warning(leg, warnings)
                    for leg in legs_by_exchange.values()
                )
            )
            current_by_exchange = {
                exchange: leg
                for exchange, leg in zip(legs_by_exchange.keys(), current_results, strict=True)
                if leg is not None
            }
            current_prices = [
                _symbol_current_price_snapshot(current_by_exchange[exchange])
                for exchange in normalized_exchanges
                if exchange in current_by_exchange
            ]

        current_base = current_by_exchange.get(effective_base_exchange)
        series = [
            _symbol_spread_series(
                exchange=exchange,
                symbol=legs_by_exchange[exchange].symbol,
                market_type=market_type,
                points=points,
                current=(
                    _symbol_current_spread_point(current_base, current_by_exchange[exchange], observed_at)
                    if current_base is not None and exchange in current_by_exchange
                    else None
                ),
            )
            for exchange, points in raw_series_points
        ]

        result_exchanges = [effective_base_exchange, *[item.exchange for item in series]]
        result_points = [point for item in series for point in item.points]
        return SymbolSpreadQueryResult(
            symbol=query_symbol,
            market_type=market_type,
            base_exchange=effective_base_exchange,
            exchanges=result_exchanges,
            hours=hours,
            interval_minutes=interval_minutes,
            interval_seconds=interval_seconds,
            observed_at=observed_at,
            point_count=sum(item.point_count for item in series),
            first_seen_at=min((point.bucket_at for point in result_points), default=None),
            last_seen_at=max((point.bucket_at for point in result_points), default=None),
            current_prices=current_prices,
            series=series,
            warnings=list(dict.fromkeys(warnings)),
        )

    async def _query_symbol_spreads_realtime(
        self,
        symbol: str,
        *,
        market_type: MarketType,
        base_exchange: str,
        exchanges: list[str] | None,
        hours: int,
        interval_seconds: int,
        now: datetime | None = None,
    ) -> SymbolSpreadQueryResult:
        observed_at = now or utc_now()
        bucket_at = _floor_interval(observed_at, interval_seconds)
        normalized_exchanges = _normalize_symbol_spread_exchanges(exchanges, base_exchange)
        if len(normalized_exchanges) < 2:
            raise PairSpreadQueryError("至少需要两个支持的主流交易所才能画跨所价差")

        legs_by_exchange = {
            exchange: PairSpreadLegQuery(exchange=exchange, symbol=symbol, market_type=market_type)
            for exchange in normalized_exchanges
        }
        query_symbol = next(iter(legs_by_exchange.values())).symbol
        requested_base_exchange = base_exchange.strip().lower()
        warnings: list[str] = []
        current_results = await asyncio.gather(
            *(
                self._fetch_current_with_warning(leg, warnings)
                for leg in legs_by_exchange.values()
            )
        )
        current_by_exchange = {
            exchange: leg
            for exchange, leg in zip(legs_by_exchange.keys(), current_results, strict=True)
            if leg is not None
        }
        if len(current_by_exchange) < 2:
            suffix = f": {_warnings_text(warnings)}" if warnings else ""
            raise PairSpreadQueryError(f"没有拿到足够的当前价格用于实时跨所采样{suffix}")

        effective_base_exchange = (
            requested_base_exchange
            if requested_base_exchange in current_by_exchange
            else next(exchange for exchange in normalized_exchanges if exchange in current_by_exchange)
        )
        if effective_base_exchange != requested_base_exchange:
            _append_unique(
                warnings,
                f"基准 {requested_base_exchange} 没有当前价格，已改用 {effective_base_exchange} 做基准。",
            )
        base_leg = current_by_exchange[effective_base_exchange]
        result_exchanges = [
            effective_base_exchange,
            *[
                exchange
                for exchange in normalized_exchanges
                if exchange != effective_base_exchange and exchange in current_by_exchange
            ],
        ]
        cache_key = _symbol_spread_cache_key(
            symbol=query_symbol,
            market_type=market_type,
            base_exchange=effective_base_exchange,
            exchanges=result_exchanges,
            interval_seconds=interval_seconds,
        )
        cached_by_exchange = _REALTIME_SYMBOL_SPREAD_CACHE.setdefault(cache_key, {})
        cutoff = observed_at - timedelta(hours=hours)
        series: list[SymbolSpreadSeries] = []
        for exchange in result_exchanges:
            if exchange == effective_base_exchange:
                continue
            current_point = _symbol_current_spread_point(base_leg, current_by_exchange[exchange], bucket_at)
            cached_points = cached_by_exchange.setdefault(exchange, [])
            _append_symbol_spread_point(cached_points, current_point)
            points = [point for point in cached_points if point.bucket_at >= cutoff]
            if len(points) != len(cached_points):
                cached_by_exchange[exchange] = points
            series.append(
                _symbol_spread_series(
                    exchange=exchange,
                    symbol=legs_by_exchange[exchange].symbol,
                    market_type=market_type,
                    points=points,
                    current=current_point,
                )
            )

        if any(item.point_count <= 1 for item in series):
            _append_unique(
                warnings,
                f"{_interval_text(interval_seconds)}周期为实时采样，需要保持刷新一段时间后才会形成连续曲线。",
            )

        result_points = [point for item in series for point in item.points]
        return SymbolSpreadQueryResult(
            symbol=query_symbol,
            market_type=market_type,
            base_exchange=effective_base_exchange,
            exchanges=result_exchanges,
            hours=hours,
            interval_minutes=1,
            interval_seconds=interval_seconds,
            observed_at=observed_at,
            point_count=sum(item.point_count for item in series),
            first_seen_at=min((point.bucket_at for point in result_points), default=None),
            last_seen_at=max((point.bucket_at for point in result_points), default=None),
            current_prices=[
                _symbol_current_price_snapshot(current_by_exchange[exchange])
                for exchange in result_exchanges
            ],
            series=series,
            warnings=list(dict.fromkeys(warnings)),
        )

    async def _query_realtime(
        self,
        leg1: PairSpreadLegQuery,
        leg2: PairSpreadLegQuery,
        *,
        hours: int,
        interval_seconds: int,
        leg2_multiplier: float,
        now: datetime | None = None,
    ) -> PairSpreadQueryResult:
        observed_at = now or utc_now()
        warnings: list[str] = []
        current_leg1, current_leg2 = await asyncio.gather(
            self._fetch_current_with_warning(leg1, warnings),
            self._fetch_current_with_warning(leg2, warnings),
        )
        current = (
            self._build_current_snapshot(
                current_leg1,
                _scale_current_leg(current_leg2, leg2_multiplier),
                observed_at,
            )
            if current_leg1 is not None and current_leg2 is not None
            else None
        )
        if current is None:
            suffix = f": {_warnings_text(warnings)}" if warnings else ""
            raise PairSpreadQueryError(f"没有拿到可用于实时采样的当前价格{suffix}")

        bucket_at = _floor_interval(observed_at, interval_seconds)
        cache_key = _realtime_cache_key(
            leg1,
            leg2,
            leg2_multiplier=leg2_multiplier,
            interval_seconds=interval_seconds,
        )
        # 秒级周期没有统一的跨交易所历史K线来源，使用每次查询拿到的实时价格形成可保存的本地采样序列。
        cached_points = _REALTIME_PAIR_SPREAD_CACHE.setdefault(cache_key, [])
        _append_realtime_point(
            cached_points,
            PairSpreadPoint(
                bucket_at=bucket_at,
                leg1_close=current.leg1.price,
                leg2_close=current.leg2.price,
                spread_abs=current.spread_abs,
                spread_pct=current.spread_pct,
            ),
        )
        cutoff = observed_at - timedelta(hours=hours)
        points = [point for point in cached_points if point.bucket_at >= cutoff]
        if len(points) != len(cached_points):
            _REALTIME_PAIR_SPREAD_CACHE[cache_key] = points

        realtime_funding = _realtime_funding_points_from_current(
            cache_key,
            current,
            observed_at=observed_at,
            interval_seconds=interval_seconds,
            hours=hours,
        )

        if len(points) <= 1:
            _append_unique(
                warnings,
                f"{_interval_text(interval_seconds)}周期为实时采样，需要保持自动刷新一段时间后才会形成连续曲线。",
            )

        return PairSpreadQueryResult(
            leg1=leg1,
            leg2=leg2,
            hours=hours,
            interval_minutes=1,
            interval_seconds=interval_seconds,
            leg2_multiplier=leg2_multiplier,
            observed_at=observed_at,
            point_count=len(points),
            first_seen_at=points[0].bucket_at if points else None,
            last_seen_at=points[-1].bucket_at if points else None,
            spread_abs=_stats([point.spread_abs for point in points]),
            spread_pct=_stats([point.spread_pct for point in points]),
            current=current,
            points=points,
            funding_history=[],
            realtime_funding=realtime_funding,
            warnings=warnings,
        )

    async def _fetch_klines_with_warning(
        self,
        exchange: str,
        symbol: str,
        market_type: MarketType,
        start: datetime,
        end: datetime,
        interval_minutes: int,
        warnings: list[str],
    ) -> list[PairSpreadKlinePoint]:
        try:
            if market_type == MarketType.FUTURE:
                return await self._fetch_klines(exchange, symbol, start, end, interval_minutes)
            return await self._fetch_klines(exchange, symbol, start, end, interval_minutes, market_type)
        except Exception as exc:  # noqa: BLE001 - keep pair query error actionable.
            _append_unique(
                warnings,
                f"{exchange}:{_market_type_text(market_type)}:{symbol} 分钟K线失败: "
                f"{_market_data_error_text(exchange, exc)}",
            )
            return []

    async def _fetch_current_with_warning(
        self,
        leg: PairSpreadLegQuery,
        warnings: list[str],
    ) -> PairSpreadCurrentLeg | None:
        try:
            if leg.market_type == MarketType.FUTURE:
                return await self._fetch_current_leg(leg.exchange, leg.symbol)
            return await self._fetch_current_leg(leg.exchange, leg.symbol, leg.market_type)
        except Exception as exc:  # noqa: BLE001 - current snapshot should not block chart.
            _append_unique(
                warnings,
                f"{leg.exchange}:{_market_type_text(leg.market_type)}:{leg.symbol} 当前价格/资金失败: "
                f"{_exception_text(exc)}",
            )
            return None

    async def _fetch_funding_with_warning(
        self,
        leg: PairSpreadLegQuery,
        start: datetime,
        end: datetime,
        warnings: list[str],
    ) -> list[PairSpreadFundingPoint]:
        if leg.market_type == MarketType.SPOT:
            return []
        try:
            return await self._fetch_funding_history(leg.exchange, leg.symbol, start, end)
        except Exception as exc:  # noqa: BLE001 - funding history is supplementary.
            _append_unique(warnings, f"{leg.exchange}:{leg.symbol} 历史资金费率失败: {_exception_text(exc)}")
            return []

    def _build_current_snapshot(
        self,
        leg1: PairSpreadCurrentLeg,
        leg2: PairSpreadCurrentLeg,
        observed_at: datetime,
    ) -> PairSpreadCurrentSnapshot:
        spread_abs = leg2.price - leg1.price
        return PairSpreadCurrentSnapshot(
            observed_at=observed_at,
            leg1=leg1,
            leg2=leg2,
            spread_abs=spread_abs,
            spread_pct=_spread_pct(spread_abs, leg1.price, leg2.price),
        )

    async def _fetch_klines(
        self,
        exchange: str,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
        market_type: MarketType = MarketType.FUTURE,
    ) -> list[PairSpreadKlinePoint]:
        if market_type == MarketType.SPOT:
            spot_handlers: dict[str, Callable[[str, datetime, datetime, int], Awaitable[list[PairSpreadKlinePoint]]]] = {
                "binance": self._fetch_binance_spot_klines,
                "binance_alpha": self._fetch_binance_alpha_spot_klines,
                "okx": self._fetch_okx_spot_klines,
                "bybit": self._fetch_bybit_spot_klines,
                "gate": self._fetch_gate_spot_klines,
                "bitget": self._fetch_bitget_spot_klines,
            }
            handler = spot_handlers.get(exchange)
            if handler is None:
                raise RuntimeError(f"{exchange} 暂不支持现货价差查询")
            return await handler(symbol, start, end, interval_minutes)

        futures_handlers: dict[str, Callable[[str, datetime, datetime, int], Awaitable[list[PairSpreadKlinePoint]]]] = {
            "binance": lambda s, a, b, i: self._fetch_binance_like_klines(
                "https://fapi.binance.com",
                s,
                a,
                b,
                i,
            ),
            "aster": lambda s, a, b, i: self._fetch_binance_like_klines(
                "https://fapi.asterdex.com",
                s,
                a,
                b,
                i,
            ),
            "okx": self._fetch_okx_klines,
            "bybit": self._fetch_bybit_klines,
            "gate": self._fetch_gate_klines,
            "bitget": self._fetch_bitget_klines,
            "hyperliquid": self._fetch_hyperliquid_klines,
        }
        return await futures_handlers[exchange](symbol, start, end, interval_minutes)

    async def _fetch_current_leg(
        self,
        exchange: str,
        symbol: str,
        market_type: MarketType = MarketType.FUTURE,
    ) -> PairSpreadCurrentLeg:
        if market_type == MarketType.SPOT:
            spot_handlers: dict[str, Callable[[str], Awaitable[PairSpreadCurrentLeg]]] = {
                "binance": self._fetch_binance_spot_current,
                "binance_alpha": self._fetch_binance_alpha_spot_current,
                "okx": self._fetch_okx_spot_current,
                "bybit": self._fetch_bybit_spot_current,
                "gate": self._fetch_gate_spot_current,
                "bitget": self._fetch_bitget_spot_current,
            }
            handler = spot_handlers.get(exchange)
            if handler is None:
                raise RuntimeError(f"{exchange} 暂不支持现货当前价格查询")
            return await handler(symbol)

        futures_handlers: dict[str, Callable[[str], Awaitable[PairSpreadCurrentLeg]]] = {
            "binance": lambda s: self._fetch_binance_like_current("https://fapi.binance.com", "binance", s),
            "aster": lambda s: self._fetch_binance_like_current("https://fapi.asterdex.com", "aster", s),
            "okx": self._fetch_okx_current,
            "bybit": self._fetch_bybit_current,
            "gate": self._fetch_gate_current,
            "bitget": self._fetch_bitget_current,
            "hyperliquid": self._fetch_hyperliquid_current,
        }
        return await futures_handlers[exchange](symbol)

    async def _fetch_funding_history(
        self,
        exchange: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PairSpreadFundingPoint]:
        handlers: dict[str, Callable[[str, datetime, datetime], Awaitable[list[PairSpreadFundingPoint]]]] = {
            "binance": lambda s, a, b: self._fetch_binance_like_funding(
                "https://fapi.binance.com",
                "binance",
                s,
                a,
                b,
            ),
            "aster": lambda s, a, b: self._fetch_binance_like_funding(
                "https://fapi.asterdex.com",
                "aster",
                s,
                a,
                b,
            ),
            "okx": self._fetch_okx_funding,
            "bybit": self._fetch_bybit_funding,
            "gate": self._fetch_gate_funding,
            "bitget": self._fetch_bitget_funding,
            "hyperliquid": self._fetch_hyperliquid_funding,
        }
        return await handlers[exchange](symbol, start, end)

    async def _get_json(self, url: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.client.get(url)
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.15)
        raise RuntimeError(f"GET {_endpoint_label(url)}失败: {_exception_text(last_error)}")

    async def _get_json_optional(self, url: str) -> Any:
        try:
            return await self._get_json(url)
        except Exception:  # noqa: BLE001 - supplemental volume must not block price data.
            return {}

    async def _post_json(self, url: str, body: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.client.post(url, json=body)
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.15)
        raise RuntimeError(f"POST {_endpoint_label(url)}失败: {_exception_text(last_error)}")

    async def _get_binance_alpha_payload(self, url: str) -> Any:
        payload = await self._get_json(url)
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise RuntimeError(_binance_alpha_error(payload))
        return payload.get("data")

    async def _fetch_binance_like_klines(
        self,
        base_url: str,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        raw = _compact_symbol(symbol)
        start_ms = _to_ms(start)
        end_ms = _to_ms(end)
        interval_ms = _interval_ms(interval_minutes)
        cursor = start_ms
        points: list[PairSpreadKlinePoint] = []
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + 1500 * interval_ms - 1)
            url = (
                f"{base_url}/fapi/v1/klines?symbol={raw}&interval={interval_minutes}m"
                f"&startTime={cursor}&endTime={chunk_end}&limit=1500"
            )
            rows = await self._get_json(url)
            parsed = [_parse_array_kline(row, 0, 4, 7, 5) for row in rows if isinstance(row, list)]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                cursor = chunk_end + 1
                continue
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            next_cursor = max(_to_ms(point.bucket_at) for point in parsed) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_binance_spot_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        raw = _compact_symbol(symbol)
        start_ms = _to_ms(start)
        end_ms = _to_ms(end)
        interval_ms = _interval_ms(interval_minutes)
        cursor = start_ms
        points: list[PairSpreadKlinePoint] = []
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + 1000 * interval_ms - 1)
            url = (
                "https://api.binance.com/api/v3/klines"
                f"?symbol={raw}&interval={interval_minutes}m"
                f"&startTime={cursor}&endTime={chunk_end}&limit=1000"
            )
            rows = await self._get_json(url)
            parsed = [_parse_array_kline(row, 0, 4, 7, 5) for row in rows if isinstance(row, list)]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                cursor = chunk_end + 1
                continue
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            next_cursor = max(_to_ms(point.bucket_at) for point in parsed) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_binance_alpha_spot_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        raw = normalize_binance_alpha_symbol(symbol)
        start_ms = _to_ms(start)
        end_ms = _to_ms(end)
        interval_ms = _interval_ms(interval_minutes)
        cursor = start_ms
        points: list[PairSpreadKlinePoint] = []
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + 1000 * interval_ms - 1)
            url = (
                f"{BINANCE_ALPHA_KLINES_URL}?symbol={raw}&interval={interval_minutes}m"
                f"&startTime={cursor}&endTime={chunk_end}&limit=1000"
            )
            try:
                rows = await self._get_binance_alpha_payload(url)
            except RuntimeError as exc:
                if _is_start_after_end_error(exc):
                    break
                raise
            parsed = [_parse_array_kline(row, 0, 4, 7, 5) for row in rows if isinstance(row, list)]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                cursor = chunk_end + 1
                continue
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            next_cursor = max(_to_ms(point.bucket_at) for point in parsed) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_okx_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        inst_id = _okx_inst_id(symbol)
        points = await self._fetch_okx_klines_backward(inst_id, start, end, interval_minutes)
        if points:
            return points
        return await self._fetch_okx_klines_forward(inst_id, start, end, interval_minutes)

    async def _fetch_okx_spot_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        inst_id = _okx_spot_inst_id(symbol)
        points = await self._fetch_okx_klines_backward(inst_id, start, end, interval_minutes)
        if points:
            return points
        return await self._fetch_okx_klines_forward(inst_id, start, end, interval_minutes)

    async def _fetch_okx_klines_backward(
        self,
        inst_id: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        start_ms = _to_ms(start)
        cursor = _to_ms(end)
        points: list[PairSpreadKlinePoint] = []
        while cursor > start_ms:
            url = (
                "https://www.okx.com/api/v5/market/history-candles"
                f"?instId={inst_id}&bar={interval_minutes}m&after={cursor}&limit=100"
            )
            rows = (await self._get_json(url)).get("data", [])
            parsed = [_parse_array_kline(row, 0, 4, 7, 6) for row in rows if isinstance(row, list)]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                break
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            oldest = min(_to_ms(point.bucket_at) for point in parsed)
            if oldest >= cursor:
                break
            cursor = oldest
        return _dedupe_sorted(points)

    async def _fetch_okx_klines_forward(
        self,
        inst_id: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        end_ms = _to_ms(end)
        cursor = _to_ms(start)
        interval_ms = _interval_ms(interval_minutes)
        points: list[PairSpreadKlinePoint] = []
        while cursor < end_ms:
            url = (
                "https://www.okx.com/api/v5/market/history-candles"
                f"?instId={inst_id}&bar={interval_minutes}m&before={cursor}&limit=100"
            )
            rows = (await self._get_json(url)).get("data", [])
            parsed = [_parse_array_kline(row, 0, 4, 7, 6) for row in rows if isinstance(row, list)]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                break
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            newest = max(_to_ms(point.bucket_at) for point in parsed)
            if newest <= cursor:
                break
            cursor = newest + interval_ms
        return _dedupe_sorted(points)

    async def _fetch_bybit_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        raw = _compact_symbol(symbol)
        end_ms = _to_ms(end)
        interval_ms = _interval_ms(interval_minutes)
        cursor = _to_ms(start)
        points: list[PairSpreadKlinePoint] = []
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + 1000 * interval_ms - 1)
            url = (
                "https://api.bybit.com/v5/market/kline"
                f"?category=linear&symbol={raw}&interval={interval_minutes}&start={cursor}&end={chunk_end}&limit=1000"
            )
            rows = (await self._get_json(url)).get("result", {}).get("list", [])
            parsed = [_parse_array_kline(row, 0, 4, 6, 5) for row in rows if isinstance(row, list)]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                cursor = chunk_end + 1
                continue
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            next_cursor = max(_to_ms(point.bucket_at) for point in parsed) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_bybit_spot_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        raw = _compact_symbol(symbol)
        end_ms = _to_ms(end)
        interval_ms = _interval_ms(interval_minutes)
        cursor = _to_ms(start)
        points: list[PairSpreadKlinePoint] = []
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + 1000 * interval_ms - 1)
            url = (
                "https://api.bybit.com/v5/market/kline"
                f"?category=spot&symbol={raw}&interval={interval_minutes}&start={cursor}&end={chunk_end}&limit=1000"
            )
            rows = (await self._get_json(url)).get("result", {}).get("list", [])
            parsed = [_parse_array_kline(row, 0, 4, 6, 5) for row in rows if isinstance(row, list)]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                cursor = chunk_end + 1
                continue
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            next_cursor = max(_to_ms(point.bucket_at) for point in parsed) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_gate_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        contract = _gate_contract(symbol)
        end_sec = int(end.timestamp())
        interval_seconds = interval_minutes * 60
        cursor = int(start.timestamp())
        points: list[PairSpreadKlinePoint] = []
        while cursor < end_sec:
            chunk_end = min(end_sec, cursor + 1800 * interval_seconds)
            url = (
                "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
                f"?contract={contract}&interval={interval_minutes}m&from={cursor}&to={chunk_end}"
            )
            rows = await self._get_json(url)
            parsed = [_parse_gate_kline(row) for row in rows if isinstance(row, (dict, list))]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                cursor = chunk_end + interval_seconds
                continue
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            next_cursor = max(int(point.bucket_at.timestamp()) for point in parsed) + interval_seconds
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_gate_spot_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        pair = _gate_contract(symbol)
        end_sec = int(end.timestamp())
        interval_seconds = interval_minutes * 60
        cursor = int(start.timestamp())
        points: list[PairSpreadKlinePoint] = []
        while cursor < end_sec:
            chunk_end = min(end_sec, cursor + 1000 * interval_seconds)
            url = (
                "https://api.gateio.ws/api/v4/spot/candlesticks"
                f"?currency_pair={pair}&interval={interval_minutes}m&from={cursor}&to={chunk_end}"
            )
            rows = await self._get_json(url)
            parsed = [_parse_gate_spot_kline(row) for row in rows if isinstance(row, (dict, list))]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                cursor = chunk_end + interval_seconds
                continue
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            next_cursor = max(int(point.bucket_at.timestamp()) for point in parsed) + interval_seconds
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_bitget_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        raw = _compact_symbol(symbol)
        end_ms = _to_ms(end)
        interval_ms = _interval_ms(interval_minutes)
        cursor = _to_ms(start)
        points: list[PairSpreadKlinePoint] = []
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + 1000 * interval_ms - 1)
            url = (
                "https://api.bitget.com/api/v2/mix/market/candles"
                f"?symbol={raw}&productType=USDT-FUTURES&granularity={interval_minutes}m"
                f"&startTime={cursor}&endTime={chunk_end}&limit=1000"
            )
            rows = (await self._get_json(url)).get("data", [])
            parsed = [_parse_array_kline(row, 0, 4, 6, 5) for row in rows if isinstance(row, list)]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                cursor = chunk_end + 1
                continue
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            next_cursor = max(_to_ms(point.bucket_at) for point in parsed) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_bitget_spot_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        raw = _compact_symbol(symbol)
        end_ms = _to_ms(end)
        interval_ms = _interval_ms(interval_minutes)
        cursor = _to_ms(start)
        granularity = f"{interval_minutes}min"
        points: list[PairSpreadKlinePoint] = []
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + 1000 * interval_ms - 1)
            url = (
                "https://api.bitget.com/api/v2/spot/market/candles"
                f"?symbol={raw}&granularity={granularity}"
                f"&startTime={cursor}&endTime={chunk_end}&limit=1000"
            )
            rows = (await self._get_json(url)).get("data", [])
            parsed = [_parse_array_kline(row, 0, 4, 6, 5) for row in rows if isinstance(row, list)]
            parsed = [point for point in parsed if point is not None]
            if not parsed:
                cursor = chunk_end + 1
                continue
            points.extend(point for point in parsed if start <= point.bucket_at <= end)
            next_cursor = max(_to_ms(point.bucket_at) for point in parsed) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_hyperliquid_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PairSpreadKlinePoint]:
        raw_coin, _ = await self._resolve_hyperliquid_coin(symbol)
        payload = await self._post_json(
            "https://api.hyperliquid.xyz/info",
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": raw_coin,
                    "interval": f"{interval_minutes}m",
                    "startTime": _to_ms(start),
                    "endTime": _to_ms(end),
                },
            },
        )
        rows = payload if isinstance(payload, list) else []
        return _dedupe_sorted(
            [
                point
                for row in rows
                if isinstance(row, dict)
                if (
                    (
                        (
                            point := _parse_dict_kline(
                                row,
                                ("t", "T", "time"),
                                ("c", "close"),
                                base_volume_keys=("v", "volume"),
                            )
                        )
                        is not None
                    )
                    and start <= point.bucket_at <= end
                )
            ]
        )

    async def _fetch_binance_like_current(
        self,
        base_url: str,
        exchange: str,
        symbol: str,
    ) -> PairSpreadCurrentLeg:
        raw = _compact_symbol(symbol)
        (
            premium,
            book,
            funding_info,
            ticker_payload,
            open_interest_payload,
            account_ratio_payload,
        ) = await asyncio.gather(
            self._get_json(f"{base_url}/fapi/v1/premiumIndex?symbol={raw}"),
            self._get_json(f"{base_url}/fapi/v1/ticker/bookTicker?symbol={raw}"),
            self._fetch_binance_like_funding_info(base_url, raw),
            self._get_json_optional(f"{base_url}/fapi/v1/ticker/24hr?symbol={raw}"),
            self._get_json_optional(f"{base_url}/fapi/v1/openInterest?symbol={raw}"),
            self._get_json_optional(
                f"{base_url}/futures/data/globalLongShortAccountRatio?symbol={raw}&period=5m&limit=1"
            ),
        )
        mark = _positive(parse_float(premium.get("markPrice"))) if isinstance(premium, dict) else None
        index = _positive(parse_float(premium.get("indexPrice"))) if isinstance(premium, dict) else None
        bid = parse_float(book.get("bidPrice")) if isinstance(book, dict) else None
        ask = parse_float(book.get("askPrice")) if isinstance(book, dict) else None
        ticker = ticker_payload if isinstance(ticker_payload, dict) else {}
        mid = _mid_price(bid, ask)
        account_row = (
            _first_row(account_ratio_payload)
            if isinstance(account_ratio_payload, list)
            else account_ratio_payload if isinstance(account_ratio_payload, dict) else {}
        )
        open_interest_contracts = (
            _nonnegative(parse_float(open_interest_payload.get("openInterest")))
            if isinstance(open_interest_payload, dict)
            else None
        )
        price_for_oi = mark or mid or index
        funding = parse_float(premium.get("lastFundingRate")) if isinstance(premium, dict) else None
        funding_interval_hours = _positive(parse_float(funding_info.get("fundingIntervalHours"))) or 8
        funding_next_time = (
            parse_datetime_ms(premium.get("nextFundingTime"))
            if isinstance(premium, dict)
            else None
        ) or _next_aligned_funding_time_from_hours(utc_now(), funding_interval_hours)
        return _current_leg(
            exchange=exchange,
            symbol=symbol,
            raw_symbol=raw,
            mark_price=mark,
            index_price=index,
            mid_price=mid,
            last_price=None,
            volume_24h_usdt=_nonnegative(parse_float(ticker.get("quoteVolume"))),
            open_interest_usdt=_open_interest_usdt(open_interest_contracts, price_for_oi),
            open_interest_contracts=open_interest_contracts,
            long_account_pct=parse_float(account_row.get("longAccount")),
            short_account_pct=parse_float(account_row.get("shortAccount")),
            long_short_ratio=parse_float(account_row.get("longShortRatio")),
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=None,
            funding_next_time=funding_next_time,
            funding_interval_hours=funding_interval_hours,
            funding_rate_upper_pct=_rate_pct_from_row(
                funding_info,
                "adjustedFundingRateCap",
                "fundingRateCap",
                "upperFundingRate",
            ),
            funding_rate_lower_pct=_rate_pct_from_row(
                funding_info,
                "adjustedFundingRateFloor",
                "fundingRateFloor",
                "lowerFundingRate",
            ),
        )

    async def _fetch_binance_spot_current(self, symbol: str) -> PairSpreadCurrentLeg:
        raw = _compact_symbol(symbol)
        book, last_payload, ticker_payload = await asyncio.gather(
            self._get_json(f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={raw}"),
            self._get_json(f"https://api.binance.com/api/v3/ticker/price?symbol={raw}"),
            self._get_json_optional(f"https://api.binance.com/api/v3/ticker/24hr?symbol={raw}"),
        )
        bid = parse_float(book.get("bidPrice")) if isinstance(book, dict) else None
        ask = parse_float(book.get("askPrice")) if isinstance(book, dict) else None
        last = parse_float(last_payload.get("price")) if isinstance(last_payload, dict) else None
        ticker = ticker_payload if isinstance(ticker_payload, dict) else {}
        return _current_leg(
            exchange="binance",
            symbol=symbol,
            market_type=MarketType.SPOT,
            raw_symbol=raw,
            mark_price=None,
            index_price=None,
            mid_price=_mid_price(bid, ask),
            last_price=_positive(last),
            volume_24h_usdt=_nonnegative(parse_float(ticker.get("quoteVolume"))),
            funding_rate_pct=None,
            funding_next_rate_pct=None,
            funding_next_time=None,
        )

    async def _fetch_binance_alpha_spot_current(self, symbol: str) -> PairSpreadCurrentLeg:
        raw = normalize_binance_alpha_symbol(symbol)
        payload = await self._get_binance_alpha_payload(f"{BINANCE_ALPHA_TICKER_URL}?symbol={raw}")
        ticker = payload if isinstance(payload, dict) else {}
        last = parse_float(ticker.get("lastPrice"))
        return _current_leg(
            exchange="binance_alpha",
            symbol=raw,
            market_type=MarketType.SPOT,
            raw_symbol=raw,
            mark_price=None,
            index_price=None,
            mid_price=None,
            last_price=_positive(last),
            volume_24h_usdt=_nonnegative(parse_float(ticker.get("quoteVolume"))),
            funding_rate_pct=None,
            funding_next_rate_pct=None,
            funding_next_time=None,
        )

    async def _fetch_binance_like_funding_info(self, base_url: str, raw_symbol: str) -> dict[str, Any]:
        try:
            rows = await self._get_json(f"{base_url}/fapi/v1/fundingInfo")
        except Exception:  # noqa: BLE001 - interval/caps are useful but should not block current prices.
            return {}
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("symbol") == raw_symbol:
                return row
        return {}

    async def _fetch_okx_current(self, symbol: str) -> PairSpreadCurrentLeg:
        inst_id = _okx_inst_id(symbol)
        _, base, _ = normalize_usdt_symbol(symbol)
        end_ms = _to_ms(utc_now())
        begin_ms = end_ms - 10 * MINUTE_MS
        ticker_payload, funding_payload, open_interest_payload, account_ratio_payload = await asyncio.gather(
            self._get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"),
            self._get_json(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"),
            self._get_json_optional(
                f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst_id}"
            ),
            self._get_json_optional(
                "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
                f"?ccy={base}&period=5m&begin={begin_ms}&end={end_ms}"
            ),
        )
        ticker = _first_row(ticker_payload.get("data", [])) if isinstance(ticker_payload, dict) else {}
        funding_row = _first_row(funding_payload.get("data", [])) if isinstance(funding_payload, dict) else {}
        mid = _mid_price(parse_float(ticker.get("bidPx")), parse_float(ticker.get("askPx")))
        last = _positive(parse_float(ticker.get("last")))
        open_interest_row = (
            _first_row(open_interest_payload.get("data", []))
            if isinstance(open_interest_payload, dict)
            else {}
        )
        open_interest_contracts = _nonnegative(
            parse_float(open_interest_row.get("oiCcy") or open_interest_row.get("oi"))
        )
        open_interest_usdt = _nonnegative(parse_float(open_interest_row.get("oiUsd"))) or _open_interest_usdt(
            open_interest_contracts,
            mid or last,
        )
        funding = parse_float(funding_row.get("fundingRate"))
        next_funding = parse_float(funding_row.get("nextFundingRate"))
        funding_next_time = parse_datetime_ms(funding_row.get("nextFundingTime")) or parse_datetime_ms(
            funding_row.get("fundingTime")
        )
        account_ratio = _okx_rubik_latest_ratio(account_ratio_payload)
        account_long_pct = account_ratio / (1 + account_ratio) * 100 if account_ratio is not None else None
        account_short_pct = 100 / (1 + account_ratio) if account_ratio is not None else None
        return _current_leg(
            exchange="okx",
            symbol=symbol,
            raw_symbol=inst_id,
            mark_price=None,
            index_price=None,
            mid_price=mid,
            last_price=last,
            volume_24h_usdt=_nonnegative(parse_float(ticker.get("volCcy24h"))),
            open_interest_usdt=open_interest_usdt,
            open_interest_contracts=open_interest_contracts,
            long_account_pct=account_long_pct,
            short_account_pct=account_short_pct,
            long_short_ratio=account_ratio,
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=next_funding * 100 if next_funding is not None else None,
            funding_next_time=funding_next_time,
            funding_interval_hours=_funding_interval_hours_from_row(funding_row),
            funding_rate_upper_pct=_rate_pct_from_row(
                funding_row,
                "maxFundingRate",
                "fundingRateCap",
                "upperFundingRate",
            ),
            funding_rate_lower_pct=_rate_pct_from_row(
                funding_row,
                "minFundingRate",
                "fundingRateFloor",
                "lowerFundingRate",
            ),
        )

    async def _fetch_okx_spot_current(self, symbol: str) -> PairSpreadCurrentLeg:
        inst_id = _okx_spot_inst_id(symbol)
        ticker_payload = await self._get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}")
        ticker = _first_row(ticker_payload.get("data", [])) if isinstance(ticker_payload, dict) else {}
        return _current_leg(
            exchange="okx",
            symbol=symbol,
            market_type=MarketType.SPOT,
            raw_symbol=inst_id,
            mark_price=None,
            index_price=None,
            mid_price=_mid_price(parse_float(ticker.get("bidPx")), parse_float(ticker.get("askPx"))),
            last_price=_positive(parse_float(ticker.get("last"))),
            volume_24h_usdt=_nonnegative(parse_float(ticker.get("volCcy24h"))),
            funding_rate_pct=None,
            funding_next_rate_pct=None,
            funding_next_time=None,
        )

    async def _fetch_bybit_current(self, symbol: str) -> PairSpreadCurrentLeg:
        raw = _compact_symbol(symbol)
        payload, instrument_payload, open_interest_payload, account_ratio_payload = await asyncio.gather(
            self._get_json(
                f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={raw}"
            ),
            self._get_json(
                f"https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={raw}"
            ),
            self._get_json_optional(
                "https://api.bybit.com/v5/market/open-interest"
                f"?category=linear&symbol={raw}&intervalTime=5min&limit=1"
            ),
            self._get_json_optional(
                f"https://api.bybit.com/v5/market/account-ratio?category=linear&symbol={raw}&period=5min&limit=1"
            ),
        )
        row = _first_row(payload.get("result", {}).get("list", [])) if isinstance(payload, dict) else {}
        instrument_row = (
            _first_row(instrument_payload.get("result", {}).get("list", []))
            if isinstance(instrument_payload, dict)
            else {}
        )
        open_interest_row = (
            _first_row(open_interest_payload.get("result", {}).get("list", []))
            if isinstance(open_interest_payload, dict)
            else {}
        )
        account_row = (
            _first_row(account_ratio_payload.get("result", {}).get("list", []))
            if isinstance(account_ratio_payload, dict)
            else {}
        )
        mark = _positive(parse_float(row.get("markPrice")))
        index = _positive(parse_float(row.get("indexPrice")))
        mid = _mid_price(parse_float(row.get("bid1Price")), parse_float(row.get("ask1Price")))
        last = _positive(parse_float(row.get("lastPrice")))
        open_interest_contracts = _nonnegative(
            parse_float(open_interest_row.get("openInterest") or row.get("openInterest"))
        )
        open_interest_usdt = _nonnegative(
            parse_float(row.get("openInterestValue") or row.get("openInterestValue24h"))
        ) or _open_interest_usdt(open_interest_contracts, mark or mid or last or index)
        funding = parse_float(row.get("fundingRate"))
        funding_interval_minutes = _positive(parse_float(instrument_row.get("fundingInterval")))
        return _current_leg(
            exchange="bybit",
            symbol=symbol,
            raw_symbol=raw,
            mark_price=mark,
            index_price=index,
            mid_price=mid,
            last_price=last,
            volume_24h_usdt=_nonnegative(parse_float(row.get("turnover24h"))),
            open_interest_usdt=open_interest_usdt,
            open_interest_contracts=open_interest_contracts,
            long_account_pct=parse_float(account_row.get("buyRatio")),
            short_account_pct=parse_float(account_row.get("sellRatio")),
            long_short_ratio=_long_short_ratio(
                parse_float(account_row.get("buyRatio")),
                parse_float(account_row.get("sellRatio")),
            ),
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=None,
            funding_next_time=parse_datetime_ms(row.get("nextFundingTime")),
            funding_interval_hours=funding_interval_minutes / 60 if funding_interval_minutes is not None else None,
            funding_rate_upper_pct=_ratio_to_pct(parse_float(instrument_row.get("upperFundingRate"))),
            funding_rate_lower_pct=_ratio_to_pct(parse_float(instrument_row.get("lowerFundingRate"))),
        )

    async def _fetch_bybit_spot_current(self, symbol: str) -> PairSpreadCurrentLeg:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(
            f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={raw}"
        )
        row = _first_row(payload.get("result", {}).get("list", [])) if isinstance(payload, dict) else {}
        return _current_leg(
            exchange="bybit",
            symbol=symbol,
            market_type=MarketType.SPOT,
            raw_symbol=raw,
            mark_price=None,
            index_price=None,
            mid_price=_mid_price(parse_float(row.get("bid1Price")), parse_float(row.get("ask1Price"))),
            last_price=_positive(parse_float(row.get("lastPrice"))),
            volume_24h_usdt=_nonnegative(parse_float(row.get("turnover24h"))),
            funding_rate_pct=None,
            funding_next_rate_pct=None,
            funding_next_time=None,
        )

    async def _fetch_gate_current(self, symbol: str) -> PairSpreadCurrentLeg:
        contract = _gate_contract(symbol)
        rows = await self._get_json(
            f"https://api.gateio.ws/api/v4/futures/usdt/tickers?contract={contract}"
        )
        try:
            contract_row = await self._get_json(
                f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{contract}"
            )
        except Exception:  # noqa: BLE001 - ticker still carries the critical current fields.
            contract_row = {}
        if not isinstance(contract_row, dict):
            contract_row = {}
        row = _first_row(rows if isinstance(rows, list) else [])
        stats_payload = await self._get_json_optional(
            f"https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract={contract}&interval=5m&limit=1"
        )
        stats_row = (
            stats_payload
            if isinstance(stats_payload, dict)
            else _first_row(stats_payload if isinstance(stats_payload, list) else [])
        )
        mark = _positive(parse_float(row.get("mark_price") or stats_row.get("mark_price")))
        index = _positive(parse_float(row.get("index_price")))
        mid = _mid_price(parse_float(row.get("highest_bid")), parse_float(row.get("lowest_ask")))
        last = _positive(parse_float(row.get("last")))
        long_count = _nonnegative(parse_float(stats_row.get("long_users")))
        short_count = _nonnegative(parse_float(stats_row.get("short_users")))
        account_ratio = _nonnegative(parse_float(stats_row.get("lsr_account")))
        account_total = long_count + short_count if long_count is not None and short_count is not None else None
        long_pct = long_count / account_total * 100 if account_total and account_total > 0 else None
        short_pct = short_count / account_total * 100 if account_total and account_total > 0 else None
        if long_pct is None and account_ratio is not None:
            long_pct = account_ratio / (1 + account_ratio) * 100
            short_pct = 100 / (1 + account_ratio)
        open_interest_contracts = _nonnegative(parse_float(stats_row.get("open_interest")))
        open_interest_usdt = _nonnegative(parse_float(stats_row.get("open_interest_usd"))) or _open_interest_usdt(
            open_interest_contracts,
            mark or mid or last,
        )
        funding = parse_float(row.get("funding_rate"))
        next_funding = parse_float(row.get("funding_rate_indicative"))
        interval_seconds = parse_float(row.get("funding_interval")) or parse_float(contract_row.get("funding_interval"))
        funding_interval_hours = interval_seconds / 3600 if interval_seconds and interval_seconds > 0 else None
        funding_next_time = parse_datetime_seconds(row.get("funding_next_apply")) or parse_datetime_seconds(
            contract_row.get("funding_next_apply")
        )
        symmetric_limit = _symmetric_rate_limit_pct_from_row(
            contract_row,
            "funding_rate_limit",
            "fundingRateLimit",
            "funding_rate_cap",
            "fundingRateCap",
        )
        return _current_leg(
            exchange="gate",
            symbol=symbol,
            raw_symbol=contract,
            mark_price=mark,
            index_price=index,
            mid_price=mid,
            last_price=last,
            volume_24h_usdt=_nonnegative(parse_float(row.get("volume_24h_quote"))),
            open_interest_usdt=open_interest_usdt,
            open_interest_contracts=open_interest_contracts,
            long_account_pct=long_pct,
            short_account_pct=short_pct,
            long_account_count=long_count,
            short_account_count=short_count,
            long_short_ratio=account_ratio,
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=next_funding * 100 if next_funding is not None else None,
            funding_next_time=funding_next_time,
            funding_interval_hours=funding_interval_hours,
            funding_rate_upper_pct=_rate_pct_from_row(
                contract_row,
                "funding_rate_upper_limit",
                "fundingRateUpperLimit",
                "max_funding_rate",
                "maxFundingRate",
                "funding_rate_max",
                "fundingRateMax",
            )
            or symmetric_limit,
            funding_rate_lower_pct=_rate_pct_from_row(
                contract_row,
                "funding_rate_lower_limit",
                "fundingRateLowerLimit",
                "min_funding_rate",
                "minFundingRate",
                "funding_rate_min",
                "fundingRateMin",
            )
            or (-symmetric_limit if symmetric_limit is not None else None),
        )

    async def _fetch_gate_spot_current(self, symbol: str) -> PairSpreadCurrentLeg:
        pair = _gate_contract(symbol)
        rows = await self._get_json(
            f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={pair}"
        )
        row = _first_row(rows if isinstance(rows, list) else [])
        return _current_leg(
            exchange="gate",
            symbol=symbol,
            market_type=MarketType.SPOT,
            raw_symbol=pair,
            mark_price=None,
            index_price=None,
            mid_price=_mid_price(parse_float(row.get("highest_bid")), parse_float(row.get("lowest_ask"))),
            last_price=_positive(parse_float(row.get("last"))),
            volume_24h_usdt=_nonnegative(parse_float(row.get("quote_volume"))),
            funding_rate_pct=None,
            funding_next_rate_pct=None,
            funding_next_time=None,
        )

    async def _fetch_bitget_current(self, symbol: str) -> PairSpreadCurrentLeg:
        raw = _compact_symbol(symbol)
        ticker_payload, funding_payload, account_ratio_payload = await asyncio.gather(
            self._get_json(
                "https://api.bitget.com/api/v2/mix/market/ticker"
                f"?symbol={raw}&productType=USDT-FUTURES"
            ),
            self._get_json(
                "https://api.bitget.com/api/v2/mix/market/current-fund-rate"
                f"?symbol={raw}&productType=USDT-FUTURES"
            ),
            self._get_json_optional(
                "https://api.bitget.com/api/v2/mix/market/account-long-short"
                f"?symbol={raw}&productType=USDT-FUTURES&period=5m"
            ),
        )
        ticker = _payload_data_row(ticker_payload)
        funding_row = _payload_data_row(funding_payload)
        account_row = _payload_data_latest_row(account_ratio_payload, "ts", "timestamp")
        mark = _positive(parse_float(ticker.get("markPrice")))
        index = _positive(parse_float(ticker.get("indexPrice")))
        mid = _mid_price(
            parse_float(ticker.get("bidPr") or ticker.get("bid")),
            parse_float(ticker.get("askPr") or ticker.get("ask")),
        )
        last = _positive(parse_float(ticker.get("lastPr") or ticker.get("last")))
        open_interest_contracts = _nonnegative(parse_float(ticker.get("holdingAmount") or ticker.get("openInterest")))
        open_interest_usdt = _nonnegative(
            parse_float(ticker.get("openInterestUsd") or ticker.get("openInterestUSDT"))
        ) or _open_interest_usdt(open_interest_contracts, mark or mid or last or index)
        funding = parse_float(funding_row.get("fundingRate") or ticker.get("fundingRate"))
        return _current_leg(
            exchange="bitget",
            symbol=symbol,
            raw_symbol=raw,
            mark_price=mark,
            index_price=index,
            mid_price=mid,
            last_price=last,
            volume_24h_usdt=_nonnegative(parse_float(ticker.get("quoteVolume") or ticker.get("usdtVolume"))),
            open_interest_usdt=open_interest_usdt,
            open_interest_contracts=open_interest_contracts,
            long_account_pct=parse_float(account_row.get("longAccountRatio")),
            short_account_pct=parse_float(account_row.get("shortAccountRatio")),
            long_short_ratio=parse_float(account_row.get("longShortAccountRatio")),
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=None,
            funding_next_time=parse_datetime_ms(funding_row.get("nextUpdate") or ticker.get("nextUpdate")),
        )

    async def _fetch_bitget_spot_current(self, symbol: str) -> PairSpreadCurrentLeg:
        raw = _compact_symbol(symbol)
        ticker_payload = await self._get_json(
            f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={raw}"
        )
        ticker = _payload_data_row(ticker_payload)
        return _current_leg(
            exchange="bitget",
            symbol=symbol,
            market_type=MarketType.SPOT,
            raw_symbol=raw,
            mark_price=None,
            index_price=None,
            mid_price=_mid_price(
                parse_float(ticker.get("bidPr") or ticker.get("bid")),
                parse_float(ticker.get("askPr") or ticker.get("ask")),
            ),
            last_price=_positive(parse_float(ticker.get("lastPr") or ticker.get("last"))),
            volume_24h_usdt=_nonnegative(parse_float(ticker.get("quoteVolume") or ticker.get("usdtVolume"))),
            funding_rate_pct=None,
            funding_next_rate_pct=None,
            funding_next_time=None,
        )

    async def _fetch_hyperliquid_current(self, symbol: str) -> PairSpreadCurrentLeg:
        resolved_coin, dex = await self._resolve_hyperliquid_coin(symbol)
        meta, contexts = await self._fetch_hyperliquid_meta_contexts(dex)
        universe = meta.get("universe", [])
        for asset, context in zip(universe, contexts):
            if not isinstance(asset, dict) or not isinstance(context, dict):
                continue
            raw_coin = str(asset.get("name", "")).strip()
            if raw_coin.upper() != resolved_coin.upper():
                continue
            funding = parse_float(context.get("funding"))
            now = utc_now()
            mark = _positive(parse_float(context.get("markPx")))
            index = _positive(parse_float(context.get("oraclePx")))
            mid = _positive(parse_float(context.get("midPx")))
            open_interest_contracts = _nonnegative(parse_float(context.get("openInterest")))
            return _current_leg(
                exchange="hyperliquid",
                symbol=symbol,
                raw_symbol=raw_coin,
                mark_price=mark,
                index_price=index,
                mid_price=mid,
                last_price=None,
                volume_24h_usdt=_nonnegative(parse_float(context.get("dayNtlVlm"))),
                open_interest_usdt=_open_interest_usdt(open_interest_contracts, mark or mid or index),
                open_interest_contracts=open_interest_contracts,
                funding_rate_pct=funding * 100 if funding is not None else None,
                funding_next_rate_pct=None,
                funding_next_time=next_aligned_funding_time(now, 1),
                funding_interval_hours=1,
                funding_rate_upper_pct=4.0,
                funding_rate_lower_pct=-4.0,
            )
        raise RuntimeError(f"hyperliquid symbol not found: {symbol}")

    async def _fetch_hyperliquid_dex_names(self) -> list[str]:
        if self._hyperliquid_dex_names is not None:
            return self._hyperliquid_dex_names

        dex_names = [""]
        payload = await self._post_json("https://api.hyperliquid.xyz/info", {"type": "perpDexs"})
        if isinstance(payload, list):
            for dex in payload:
                if not isinstance(dex, dict):
                    continue
                name = str(dex.get("name", "")).strip()
                if name and name not in dex_names:
                    dex_names.append(name)
        self._hyperliquid_dex_names = dex_names
        return dex_names

    async def _fetch_hyperliquid_meta_contexts(self, dex: str = "") -> tuple[dict[str, Any], list[Any]]:
        if dex in self._hyperliquid_meta_contexts_by_dex:
            return self._hyperliquid_meta_contexts_by_dex[dex]

        body: dict[str, Any] = {"type": "metaAndAssetCtxs"}
        if dex:
            body["dex"] = dex
        payload = await self._post_json("https://api.hyperliquid.xyz/info", body)
        if not isinstance(payload, list) or len(payload) < 2:
            raise RuntimeError("unexpected hyperliquid metaAndAssetCtxs payload")
        meta = payload[0] if isinstance(payload[0], dict) else {}
        contexts = payload[1] if isinstance(payload[1], list) else []
        self._hyperliquid_meta_contexts_by_dex[dex] = (meta, contexts)
        self._index_hyperliquid_meta_coins(dex, meta)
        return self._hyperliquid_meta_contexts_by_dex[dex]

    async def _resolve_hyperliquid_coin(self, symbol: str) -> tuple[str, str]:
        requested_coin = _hyperliquid_coin(symbol).upper()
        cached = self._hyperliquid_coin_by_base.get(requested_coin)
        if cached is not None:
            return cached

        for dex in await self._fetch_hyperliquid_dex_names():
            await self._fetch_hyperliquid_meta_contexts(dex)
            resolved = self._hyperliquid_coin_by_base.get(requested_coin)
            if resolved is not None:
                return resolved

        raise RuntimeError(f"hyperliquid symbol not found: {symbol}")

    def _index_hyperliquid_meta_coins(self, dex: str, meta: dict[str, Any]) -> None:
        for asset in meta.get("universe", []):
            if not isinstance(asset, dict):
                continue
            raw_coin = str(asset.get("name", "")).strip()
            if not raw_coin:
                continue
            resolved = (raw_coin, dex)
            self._hyperliquid_coin_by_base.setdefault(raw_coin.upper(), resolved)
            self._hyperliquid_coin_by_base.setdefault(_hyperliquid_base_from_raw(raw_coin).upper(), resolved)

    async def _require_hyperliquid_coin(self, symbol: str) -> str:
        raw_coin, _ = await self._resolve_hyperliquid_coin(symbol)
        if not raw_coin:
            raise RuntimeError(f"hyperliquid symbol not found: {symbol}")
        return raw_coin

    async def _fetch_binance_like_funding(
        self,
        base_url: str,
        exchange: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PairSpreadFundingPoint]:
        raw = _compact_symbol(symbol)
        rows = await self._get_json(
            f"{base_url}/fapi/v1/fundingRate?symbol={raw}"
            f"&startTime={_to_ms(start)}&endTime={_to_ms(end)}&limit=1000"
        )
        return [
            point
            for row in rows if isinstance(row, dict)
            if (
                point := _funding_point(
                    exchange,
                    symbol,
                    parse_datetime_ms(row.get("fundingTime")),
                    parse_float(row.get("fundingRate")),
                )
            )
            is not None
        ]

    async def _fetch_okx_funding(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PairSpreadFundingPoint]:
        inst_id = _okx_inst_id(symbol)
        payload = await self._get_json(
            f"https://www.okx.com/api/v5/public/funding-rate-history?instId={inst_id}&limit=100"
        )
        return [
            point
            for row in payload.get("data", []) if isinstance(row, dict)
            if (
                point := _funding_point(
                    "okx",
                    symbol,
                    parse_datetime_ms(row.get("fundingTime")),
                    parse_float(row.get("fundingRate")),
                )
            )
            is not None
            and start <= point.funding_time <= end
        ]

    async def _fetch_bybit_funding(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PairSpreadFundingPoint]:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(
            "https://api.bybit.com/v5/market/funding/history"
            f"?category=linear&symbol={raw}&startTime={_to_ms(start)}&endTime={_to_ms(end)}&limit=200"
        )
        return [
            point
            for row in payload.get("result", {}).get("list", []) if isinstance(row, dict)
            if (
                point := _funding_point(
                    "bybit",
                    symbol,
                    parse_datetime_ms(row.get("fundingRateTimestamp")),
                    parse_float(row.get("fundingRate")),
                )
            )
            is not None
        ]

    async def _fetch_gate_funding(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PairSpreadFundingPoint]:
        contract = _gate_contract(symbol)
        rows = await self._get_json(
            "https://api.gateio.ws/api/v4/futures/usdt/funding_rate"
            f"?contract={contract}&from={int(start.timestamp())}&to={int(end.timestamp())}&limit=1000"
        )
        points: list[PairSpreadFundingPoint] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            timestamp = (
                parse_datetime_seconds(row.get("t"))
                or parse_datetime_seconds(row.get("time"))
                or parse_datetime_ms(row.get("funding_time"))
            )
            rate = parse_float(row.get("r") or row.get("rate") or row.get("funding_rate"))
            point = _funding_point("gate", symbol, timestamp, rate)
            if point is not None:
                points.append(point)
        return points

    async def _fetch_bitget_funding(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PairSpreadFundingPoint]:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(
            "https://api.bitget.com/api/v2/mix/market/history-fund-rate"
            f"?symbol={raw}&productType=USDT-FUTURES&pageSize=100&pageNo=1"
        )
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        points: list[PairSpreadFundingPoint] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            timestamp = parse_datetime_ms(row.get("fundingTime") or row.get("settleTime"))
            rate = parse_float(row.get("fundingRate"))
            point = _funding_point("bitget", symbol, timestamp, rate)
            if point is not None and start <= point.funding_time <= end:
                points.append(point)
        return points

    async def _fetch_hyperliquid_funding(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PairSpreadFundingPoint]:
        raw_coin = await self._require_hyperliquid_coin(symbol)
        payload = await self._post_json(
            "https://api.hyperliquid.xyz/info",
            {
                "type": "fundingHistory",
                "coin": raw_coin,
                "startTime": _to_ms(start),
                "endTime": _to_ms(end),
            },
        )
        rows = payload if isinstance(payload, list) else []
        points: list[PairSpreadFundingPoint] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            point = _funding_point(
                "hyperliquid",
                symbol,
                parse_datetime_ms(row.get("time")),
                parse_float(row.get("fundingRate")),
            )
            if point is not None:
                points.append(point)
        return points


def _exception_text(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown error"
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        reason = exc.response.reason_phrase or "HTTP error"
        return f"HTTP {status_code} {reason}"
    text = str(exc).strip()
    return f"{exc.__class__.__name__}: {text}" if text else exc.__class__.__name__


def _endpoint_label(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.netloc:
        return url
    return f"{parsed.netloc}{parsed.path}"


def _market_data_error_text(exchange: str, exc: BaseException) -> str:
    text = _exception_text(exc)
    if exchange == "hyperliquid" and "HTTP 500" in text:
        return "Hyperliquid 接口返回 HTTP 500，可能是该合约未上线、名称不匹配，或接口临时异常"
    return text


def _array_value(row: list[Any], index: int | None) -> Any:
    if index is None or len(row) <= index:
        return None
    return row[index]


def _dict_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row:
            return row.get(key)
    return None


def _volume_usdt_from_values(
    close: float,
    quote_volume_value: Any,
    base_volume_value: Any,
) -> float | None:
    quote_volume = _nonnegative(parse_float(quote_volume_value))
    if quote_volume is not None:
        return quote_volume
    base_volume = _nonnegative(parse_float(base_volume_value))
    if base_volume is None:
        return None
    return base_volume * close


def _parse_array_kline(
    row: list[Any],
    time_index: int,
    close_index: int,
    quote_volume_index: int | None = None,
    base_volume_index: int | None = None,
) -> PairSpreadKlinePoint | None:
    if len(row) <= max(time_index, close_index):
        return None
    bucket_at = _bucket_datetime_ms(row[time_index])
    close = _positive(parse_float(row[close_index]))
    if bucket_at is None or close is None:
        return None
    return PairSpreadKlinePoint(
        bucket_at=bucket_at,
        close=close,
        volume_usdt=_volume_usdt_from_values(
            close,
            _array_value(row, quote_volume_index),
            _array_value(row, base_volume_index),
        ),
    )


def _parse_dict_kline(
    row: dict[str, Any],
    time_keys: tuple[str, ...],
    close_keys: tuple[str, ...],
    quote_volume_keys: tuple[str, ...] = (),
    base_volume_keys: tuple[str, ...] = (),
) -> PairSpreadKlinePoint | None:
    bucket_at: datetime | None = None
    for key in time_keys:
        if key in row:
            bucket_at = _bucket_datetime_ms(row.get(key))
            if bucket_at is not None:
                break
    close: float | None = None
    for key in close_keys:
        if key in row:
            close = _positive(parse_float(row.get(key)))
            if close is not None:
                break
    if bucket_at is None or close is None:
        return None
    return PairSpreadKlinePoint(
        bucket_at=bucket_at,
        close=close,
        volume_usdt=_volume_usdt_from_values(
            close,
            _dict_value(row, quote_volume_keys),
            _dict_value(row, base_volume_keys),
        ),
    )


def _parse_gate_kline(row: dict[str, Any] | list[Any]) -> PairSpreadKlinePoint | None:
    if isinstance(row, list):
        if len(row) >= 5:
            return _parse_array_kline(row, 0, 4)
        return None
    bucket_at = (
        _bucket_datetime_seconds(row.get("t"))
        or _bucket_datetime_seconds(row.get("time"))
        or _bucket_datetime_ms(row.get("timestamp"))
    )
    close = _positive(parse_float(row.get("c") or row.get("close")))
    if bucket_at is None or close is None:
        return None
    return PairSpreadKlinePoint(
        bucket_at=bucket_at,
        close=close,
        volume_usdt=_volume_usdt_from_values(
            close,
            _dict_value(row, ("sum", "quote_volume", "quoteVolume", "amount", "turnover")),
            _dict_value(row, ("v", "volume", "base_volume", "baseVolume")),
        ),
    )


def _parse_gate_spot_kline(row: dict[str, Any] | list[Any]) -> PairSpreadKlinePoint | None:
    if isinstance(row, list):
        if len(row) >= 3:
            bucket_at = _bucket_datetime_seconds(row[0]) or _bucket_datetime_ms(row[0])
            close = _positive(parse_float(row[2]))
            if bucket_at is not None and close is not None:
                return PairSpreadKlinePoint(
                    bucket_at=bucket_at,
                    close=close,
                    volume_usdt=_volume_usdt_from_values(
                        close,
                        _array_value(row, 1),
                        _array_value(row, 6),
                    ),
                )
        return None
    bucket_at = (
        _bucket_datetime_seconds(row.get("t"))
        or _bucket_datetime_seconds(row.get("time"))
        or _bucket_datetime_ms(row.get("timestamp"))
    )
    close = _positive(parse_float(row.get("c") or row.get("close")))
    if bucket_at is None or close is None:
        return None
    return PairSpreadKlinePoint(
        bucket_at=bucket_at,
        close=close,
        volume_usdt=_volume_usdt_from_values(
            close,
            _dict_value(row, ("quote_volume", "quoteVolume", "amount", "sum", "turnover")),
            _dict_value(row, ("v", "volume", "base_volume", "baseVolume")),
        ),
    )


def _first_row(rows: Any) -> dict[str, Any]:
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


def _payload_data_row(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", {})
    if isinstance(data, list):
        return _first_row(data)
    if isinstance(data, dict):
        return data
    return {}


def _payload_data_latest_row(payload: Any, *timestamp_keys: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", {})
    if isinstance(data, dict):
        return data
    if not isinstance(data, list):
        return {}
    rows = [row for row in data if isinstance(row, dict)]
    if not rows:
        return {}
    if not timestamp_keys:
        return rows[-1]
    return max(rows, key=lambda row: max((parse_float(row.get(key)) or 0 for key in timestamp_keys), default=0))


def _okx_rubik_latest_ratio(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        return None
    latest_time: float | None = None
    latest_ratio: float | None = None
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        row_time = parse_float(row[0])
        row_ratio = _nonnegative(parse_float(row[1]))
        if row_ratio is None:
            continue
        if latest_time is None or (row_time is not None and row_time > latest_time):
            latest_time = row_time
            latest_ratio = row_ratio
    return latest_ratio


def _current_leg(
    *,
    exchange: str,
    symbol: str,
    market_type: MarketType = MarketType.FUTURE,
    raw_symbol: str,
    mark_price: float | None,
    index_price: float | None,
    mid_price: float | None,
    last_price: float | None,
    funding_rate_pct: float | None,
    funding_next_rate_pct: float | None,
    funding_next_time: datetime | None,
    funding_interval_hours: float | None = None,
    funding_rate_upper_pct: float | None = None,
    funding_rate_lower_pct: float | None = None,
    volume_24h_usdt: float | None = None,
    open_interest_usdt: float | None = None,
    open_interest_contracts: float | None = None,
    long_account_pct: float | None = None,
    short_account_pct: float | None = None,
    long_account_count: float | None = None,
    short_account_count: float | None = None,
    long_short_ratio: float | None = None,
) -> PairSpreadCurrentLeg:
    candidates = (
        (mark_price, PairSpreadPriceField.MARK_PRICE),
        (mid_price, PairSpreadPriceField.MID_PRICE),
        (index_price, PairSpreadPriceField.INDEX_PRICE),
        (last_price, PairSpreadPriceField.LAST_PRICE),
    )
    for value, field in candidates:
        resolved = _positive(value)
        if resolved is not None:
            return PairSpreadCurrentLeg(
                exchange=exchange,
                symbol=(
                    normalize_binance_alpha_symbol(symbol)
                    if exchange == "binance_alpha"
                    else _compact_symbol(symbol)
                ),
                market_type=market_type,
                raw_symbol=raw_symbol,
                price=resolved,
                price_field=field,
                mark_price=mark_price,
                index_price=index_price,
                mid_price=mid_price,
                last_price=last_price,
                volume_24h_usdt=_nonnegative(volume_24h_usdt),
                open_interest_usdt=_nonnegative(open_interest_usdt),
                open_interest_contracts=_nonnegative(open_interest_contracts),
                long_account_pct=_account_ratio_to_pct(long_account_pct),
                short_account_pct=_account_ratio_to_pct(short_account_pct),
                long_account_count=_nonnegative(long_account_count),
                short_account_count=_nonnegative(short_account_count),
                long_short_ratio=_nonnegative(long_short_ratio)
                or _long_short_ratio(long_account_pct, short_account_pct)
                or _long_short_ratio(long_account_count, short_account_count),
                funding_rate_pct=funding_rate_pct,
                funding_next_rate_pct=funding_next_rate_pct,
                funding_next_time=funding_next_time,
                funding_interval_hours=funding_interval_hours,
                funding_rate_upper_pct=funding_rate_upper_pct,
                funding_rate_lower_pct=funding_rate_lower_pct,
                timestamp=utc_now(),
            )
    raise RuntimeError(f"no usable current price for {exchange}:{symbol}")


def _scale_current_leg(leg: PairSpreadCurrentLeg, divisor: float) -> PairSpreadCurrentLeg:
    if divisor == 1:
        return leg

    def scale(value: float | None) -> float | None:
        return value / divisor if value is not None else None

    return leg.model_copy(
        update={
            "price": leg.price / divisor,
            "mark_price": scale(leg.mark_price),
            "index_price": scale(leg.index_price),
            "mid_price": scale(leg.mid_price),
            "last_price": scale(leg.last_price),
        }
    )


def _funding_point(
    exchange: str,
    symbol: str,
    funding_time: datetime | None,
    funding_rate: float | None,
) -> PairSpreadFundingPoint | None:
    if funding_time is None or funding_rate is None or not isfinite(funding_rate):
        return None
    return PairSpreadFundingPoint(
        exchange=exchange,
        symbol=_compact_symbol(symbol),
        funding_time=funding_time,
        funding_rate_pct=funding_rate * 100,
    )
