from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, timezone
from math import isfinite
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.exchanges.base import (
    DEFAULT_HEADERS,
    DEFAULT_LIMITS,
    normalize_usdt_symbol,
    parse_datetime_ms,
    parse_datetime_seconds,
    parse_float,
    utc_now,
)
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadCurrentSnapshot,
    PairSpreadFundingPoint,
    PairSpreadKlinePoint,
    PairSpreadLegQuery,
    PairSpreadPoint,
    PairSpreadPriceField,
    PairSpreadQueryResult,
    PairSpreadValueStats,
)

MINUTE_MS = 60_000
PAIR_SPREAD_TIMEOUT = httpx.Timeout(18.0, connect=3.0, read=14.0, write=5.0, pool=5.0)
DISPLAY_TZ = timezone(timedelta(hours=8))


class PairSpreadQueryError(RuntimeError):
    pass


def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _interval_ms(interval_minutes: int) -> int:
    return interval_minutes * MINUTE_MS


def _floor_minute(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(second=0, microsecond=0)


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


def _ratio_to_pct(value: float | None) -> float | None:
    if value is None or not isfinite(value):
        return None
    return value * 100


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


def _extend_unique(items: list[str], new_items: list[str]) -> None:
    for item in new_items:
        _append_unique(items, item)


def _warnings_text(items: list[str]) -> str:
    unique_items = list(dict.fromkeys(items))
    return "; ".join(unique_items)


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

    async def query(
        self,
        leg1: PairSpreadLegQuery,
        leg2: PairSpreadLegQuery,
        *,
        hours: int,
        interval_minutes: int = 1,
        leg2_multiplier: float = 1.0,
        now: datetime | None = None,
    ) -> PairSpreadQueryResult:
        if leg2_multiplier <= 0:
            raise PairSpreadQueryError("leg2_multiplier must be positive")
        observed_at = now or utc_now()
        end = _floor_minute(observed_at)
        requested_start = end - timedelta(hours=hours)
        warnings: list[str] = []
        kline_keys = list(dict.fromkeys(((leg1.exchange, leg1.symbol), (leg2.exchange, leg2.symbol))))

        points: list[PairSpreadPoint] = []
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
                        window_start,
                        end,
                        interval_minutes,
                        window_warnings,
                    )
                    for exchange, symbol in kline_keys
                )
            )
            klines_by_key = dict(zip(kline_keys, kline_results, strict=True))
            candidate_leg1_klines = klines_by_key[(leg1.exchange, leg1.symbol)]
            candidate_leg2_klines = klines_by_key[(leg2.exchange, leg2.symbol)]
            candidate_points = build_pair_spread_points(
                candidate_leg1_klines,
                candidate_leg2_klines,
                leg2_multiplier=leg2_multiplier,
            )
            if candidate_points:
                points = candidate_points
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
            warnings.insert(
                0,
                f"请求{_duration_text(hours)}，最早可对齐K线为{_display_time(points[0].bucket_at)}，已按可获取数据展示。",
            )

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

        return PairSpreadQueryResult(
            leg1=leg1,
            leg2=leg2,
            hours=hours,
            interval_minutes=interval_minutes,
            leg2_multiplier=leg2_multiplier,
            observed_at=observed_at,
            point_count=len(points),
            first_seen_at=points[0].bucket_at,
            last_seen_at=points[-1].bucket_at,
            spread_abs=_stats([point.spread_abs for point in points]),
            spread_pct=_stats([point.spread_pct for point in points]),
            current=current,
            points=points,
            funding_history=sorted(funding1 + funding2, key=lambda item: item.funding_time),
            warnings=warnings,
        )

    async def _fetch_klines_with_warning(
        self,
        exchange: str,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
        warnings: list[str],
    ) -> list[PairSpreadKlinePoint]:
        try:
            return await self._fetch_klines(exchange, symbol, start, end, interval_minutes)
        except Exception as exc:  # noqa: BLE001 - keep pair query error actionable.
            _append_unique(warnings, f"{exchange}:{symbol} 分钟K线失败: {_market_data_error_text(exchange, exc)}")
            return []

    async def _fetch_current_with_warning(
        self,
        leg: PairSpreadLegQuery,
        warnings: list[str],
    ) -> PairSpreadCurrentLeg | None:
        try:
            return await self._fetch_current_leg(leg.exchange, leg.symbol)
        except Exception as exc:  # noqa: BLE001 - current snapshot should not block chart.
            _append_unique(warnings, f"{leg.exchange}:{leg.symbol} 当前价格/资金失败: {_exception_text(exc)}")
            return None

    async def _fetch_funding_with_warning(
        self,
        leg: PairSpreadLegQuery,
        start: datetime,
        end: datetime,
        warnings: list[str],
    ) -> list[PairSpreadFundingPoint]:
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
    ) -> list[PairSpreadKlinePoint]:
        handlers: dict[str, Callable[[str, datetime, datetime, int], Awaitable[list[PairSpreadKlinePoint]]]] = {
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
        return await handlers[exchange](symbol, start, end, interval_minutes)

    async def _fetch_current_leg(self, exchange: str, symbol: str) -> PairSpreadCurrentLeg:
        handlers: dict[str, Callable[[str], Awaitable[PairSpreadCurrentLeg]]] = {
            "binance": lambda s: self._fetch_binance_like_current("https://fapi.binance.com", "binance", s),
            "aster": lambda s: self._fetch_binance_like_current("https://fapi.asterdex.com", "aster", s),
            "okx": self._fetch_okx_current,
            "bybit": self._fetch_bybit_current,
            "gate": self._fetch_gate_current,
            "bitget": self._fetch_bitget_current,
            "hyperliquid": self._fetch_hyperliquid_current,
        }
        return await handlers[exchange](symbol)

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
            parsed = [_parse_array_kline(row, 0, 4) for row in rows if isinstance(row, list)]
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
            parsed = [_parse_array_kline(row, 0, 4) for row in rows if isinstance(row, list)]
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
            parsed = [_parse_array_kline(row, 0, 4) for row in rows if isinstance(row, list)]
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
            parsed = [_parse_array_kline(row, 0, 4) for row in rows if isinstance(row, list)]
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
            parsed = [_parse_array_kline(row, 0, 4) for row in rows if isinstance(row, list)]
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
                if (point := _parse_dict_kline(row, ("t", "T", "time"), ("c", "close"))) is not None
                and start <= point.bucket_at <= end
            ]
        )

    async def _fetch_binance_like_current(
        self,
        base_url: str,
        exchange: str,
        symbol: str,
    ) -> PairSpreadCurrentLeg:
        raw = _compact_symbol(symbol)
        premium, book = await asyncio.gather(
            self._get_json(f"{base_url}/fapi/v1/premiumIndex?symbol={raw}"),
            self._get_json(f"{base_url}/fapi/v1/ticker/bookTicker?symbol={raw}"),
        )
        mark = _positive(parse_float(premium.get("markPrice"))) if isinstance(premium, dict) else None
        index = _positive(parse_float(premium.get("indexPrice"))) if isinstance(premium, dict) else None
        bid = parse_float(book.get("bidPrice")) if isinstance(book, dict) else None
        ask = parse_float(book.get("askPrice")) if isinstance(book, dict) else None
        mid = _mid_price(bid, ask)
        funding = parse_float(premium.get("lastFundingRate")) if isinstance(premium, dict) else None
        return _current_leg(
            exchange=exchange,
            symbol=symbol,
            raw_symbol=raw,
            mark_price=mark,
            index_price=index,
            mid_price=mid,
            last_price=None,
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=None,
            funding_next_time=parse_datetime_ms(premium.get("nextFundingTime"))
            if isinstance(premium, dict)
            else None,
        )

    async def _fetch_okx_current(self, symbol: str) -> PairSpreadCurrentLeg:
        inst_id = _okx_inst_id(symbol)
        ticker_payload, funding_payload = await asyncio.gather(
            self._get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"),
            self._get_json(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"),
        )
        ticker = _first_row(ticker_payload.get("data", [])) if isinstance(ticker_payload, dict) else {}
        funding_row = _first_row(funding_payload.get("data", [])) if isinstance(funding_payload, dict) else {}
        mid = _mid_price(parse_float(ticker.get("bidPx")), parse_float(ticker.get("askPx")))
        funding = parse_float(funding_row.get("fundingRate"))
        next_funding = parse_float(funding_row.get("nextFundingRate"))
        return _current_leg(
            exchange="okx",
            symbol=symbol,
            raw_symbol=inst_id,
            mark_price=None,
            index_price=None,
            mid_price=mid,
            last_price=_positive(parse_float(ticker.get("last"))),
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=next_funding * 100 if next_funding is not None else None,
            funding_next_time=parse_datetime_ms(funding_row.get("nextFundingTime"))
            or parse_datetime_ms(funding_row.get("fundingTime")),
        )

    async def _fetch_bybit_current(self, symbol: str) -> PairSpreadCurrentLeg:
        raw = _compact_symbol(symbol)
        payload, instrument_payload = await asyncio.gather(
            self._get_json(
                f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={raw}"
            ),
            self._get_json(
                f"https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={raw}"
            ),
        )
        row = _first_row(payload.get("result", {}).get("list", [])) if isinstance(payload, dict) else {}
        instrument_row = (
            _first_row(instrument_payload.get("result", {}).get("list", []))
            if isinstance(instrument_payload, dict)
            else {}
        )
        funding = parse_float(row.get("fundingRate"))
        funding_interval_minutes = _positive(parse_float(instrument_row.get("fundingInterval")))
        return _current_leg(
            exchange="bybit",
            symbol=symbol,
            raw_symbol=raw,
            mark_price=_positive(parse_float(row.get("markPrice"))),
            index_price=_positive(parse_float(row.get("indexPrice"))),
            mid_price=_mid_price(parse_float(row.get("bid1Price")), parse_float(row.get("ask1Price"))),
            last_price=_positive(parse_float(row.get("lastPrice"))),
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=None,
            funding_next_time=parse_datetime_ms(row.get("nextFundingTime")),
            funding_interval_hours=funding_interval_minutes / 60 if funding_interval_minutes is not None else None,
            funding_rate_upper_pct=_ratio_to_pct(parse_float(instrument_row.get("upperFundingRate"))),
            funding_rate_lower_pct=_ratio_to_pct(parse_float(instrument_row.get("lowerFundingRate"))),
        )

    async def _fetch_gate_current(self, symbol: str) -> PairSpreadCurrentLeg:
        contract = _gate_contract(symbol)
        rows = await self._get_json(
            f"https://api.gateio.ws/api/v4/futures/usdt/tickers?contract={contract}"
        )
        row = _first_row(rows if isinstance(rows, list) else [])
        funding = parse_float(row.get("funding_rate"))
        next_funding = parse_float(row.get("funding_rate_indicative"))
        return _current_leg(
            exchange="gate",
            symbol=symbol,
            raw_symbol=contract,
            mark_price=_positive(parse_float(row.get("mark_price"))),
            index_price=_positive(parse_float(row.get("index_price"))),
            mid_price=_mid_price(parse_float(row.get("highest_bid")), parse_float(row.get("lowest_ask"))),
            last_price=_positive(parse_float(row.get("last"))),
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=next_funding * 100 if next_funding is not None else None,
            funding_next_time=parse_datetime_seconds(row.get("funding_next_apply")),
        )

    async def _fetch_bitget_current(self, symbol: str) -> PairSpreadCurrentLeg:
        raw = _compact_symbol(symbol)
        ticker_payload, funding_payload = await asyncio.gather(
            self._get_json(
                "https://api.bitget.com/api/v2/mix/market/ticker"
                f"?symbol={raw}&productType=USDT-FUTURES"
            ),
            self._get_json(
                "https://api.bitget.com/api/v2/mix/market/current-fund-rate"
                f"?symbol={raw}&productType=USDT-FUTURES"
            ),
        )
        ticker = _payload_data_row(ticker_payload)
        funding_row = _payload_data_row(funding_payload)
        funding = parse_float(funding_row.get("fundingRate") or ticker.get("fundingRate"))
        return _current_leg(
            exchange="bitget",
            symbol=symbol,
            raw_symbol=raw,
            mark_price=_positive(parse_float(ticker.get("markPrice"))),
            index_price=_positive(parse_float(ticker.get("indexPrice"))),
            mid_price=_mid_price(
                parse_float(ticker.get("bidPr") or ticker.get("bid")),
                parse_float(ticker.get("askPr") or ticker.get("ask")),
            ),
            last_price=_positive(parse_float(ticker.get("lastPr") or ticker.get("last"))),
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=None,
            funding_next_time=parse_datetime_ms(funding_row.get("nextUpdate") or ticker.get("nextUpdate")),
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
            return _current_leg(
                exchange="hyperliquid",
                symbol=symbol,
                raw_symbol=raw_coin,
                mark_price=_positive(parse_float(context.get("markPx"))),
                index_price=_positive(parse_float(context.get("oraclePx"))),
                mid_price=_positive(parse_float(context.get("midPx"))),
                last_price=None,
                funding_rate_pct=funding * 100 if funding is not None else None,
                funding_next_rate_pct=None,
                funding_next_time=None,
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


def _parse_array_kline(row: list[Any], time_index: int, close_index: int) -> PairSpreadKlinePoint | None:
    if len(row) <= max(time_index, close_index):
        return None
    bucket_at = _bucket_datetime_ms(row[time_index])
    close = _positive(parse_float(row[close_index]))
    if bucket_at is None or close is None:
        return None
    return PairSpreadKlinePoint(bucket_at=bucket_at, close=close)


def _parse_dict_kline(
    row: dict[str, Any],
    time_keys: tuple[str, ...],
    close_keys: tuple[str, ...],
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
    return PairSpreadKlinePoint(bucket_at=bucket_at, close=close)


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
    return PairSpreadKlinePoint(bucket_at=bucket_at, close=close)


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


def _current_leg(
    *,
    exchange: str,
    symbol: str,
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
                symbol=_compact_symbol(symbol),
                raw_symbol=raw_symbol,
                price=resolved,
                price_field=field,
                mark_price=mark_price,
                index_price=index_price,
                mid_price=mid_price,
                last_price=last_price,
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
