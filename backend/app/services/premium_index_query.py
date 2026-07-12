from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from math import ceil, isfinite
from typing import Any

from app.exchanges.base import parse_datetime_ms, parse_datetime_seconds, parse_float, utc_now
from app.models.premium_index import (
    PremiumIndexCurrentSnapshot,
    PremiumIndexMarketQuery,
    PremiumIndexPoint,
    PremiumIndexQueryResult,
    PremiumIndexValueStats,
)
from app.services.pair_spread_query import (
    PairSpreadQueryService,
    _append_unique,
    _bucket_datetime_ms,
    _bucket_datetime_seconds,
    _compact_symbol,
    _display_time,
    _duration_text,
    _exception_text,
    _first_row,
    _floor_minute,
    _gate_contract,
    _interval_ms,
    _market_data_error_text,
    _okx_inst_id,
    _positive,
    _query_window_hours,
    _to_ms,
)


class PremiumIndexQueryError(RuntimeError):
    pass


def _premium_pct(price: float | None, index_price: float | None) -> float | None:
    price = _positive(price)
    index_price = _positive(index_price)
    if price is None or index_price is None:
        return None
    return (price - index_price) / index_price * 100


def _ratio_to_pct(value: float | None) -> float | None:
    if value is None or not isfinite(value):
        return None
    return value * 100 if abs(value) <= 2 else value


def _stats(values: list[float]) -> PremiumIndexValueStats:
    finite_values = [value for value in values if isfinite(value)]
    if not finite_values:
        return PremiumIndexValueStats()
    return PremiumIndexValueStats(
        min=min(finite_values),
        max=max(finite_values),
        mean=sum(finite_values) / len(finite_values),
        current=finite_values[-1],
    )


def _dedupe_sorted(points: list[PremiumIndexPoint]) -> list[PremiumIndexPoint]:
    by_bucket: dict[datetime, PremiumIndexPoint] = {}
    for point in points:
        by_bucket[point.bucket_at] = point
    return [by_bucket[key] for key in sorted(by_bucket)]


def _interpolate_optional_number(left: float | None, right: float | None, ratio: float) -> float | None:
    if left is None or right is None or not isfinite(left) or not isfinite(right):
        return None
    return left + (right - left) * ratio


def densify_premium_points(
    points: list[PremiumIndexPoint],
    *,
    interval_minutes: int,
) -> list[PremiumIndexPoint]:
    if interval_minutes <= 0 or len(points) < 2:
        return _dedupe_sorted(points)

    sorted_points = _dedupe_sorted(points)
    interval = timedelta(minutes=interval_minutes)
    dense_points: list[PremiumIndexPoint] = []

    for left, right in zip(sorted_points, sorted_points[1:], strict=False):
        dense_points.append(left)
        gap = right.bucket_at - left.bucket_at
        if gap <= interval:
            continue

        step_count = int(gap.total_seconds() // interval.total_seconds())
        if step_count <= 1:
            continue

        for step_index in range(1, step_count):
            bucket_at = left.bucket_at + interval * step_index
            if bucket_at >= right.bucket_at:
                break
            ratio = (bucket_at - left.bucket_at).total_seconds() / gap.total_seconds()
            dense_points.append(
                PremiumIndexPoint(
                    bucket_at=bucket_at,
                    premium_pct=left.premium_pct + (right.premium_pct - left.premium_pct) * ratio,
                    mark_price=_interpolate_optional_number(left.mark_price, right.mark_price, ratio),
                    index_price=_interpolate_optional_number(left.index_price, right.index_price, ratio),
                    source=f"{left.source}_interpolated" if left.source == right.source else "interpolated",
                )
            )

    dense_points.append(sorted_points[-1])
    return _dedupe_sorted(dense_points)


def build_premium_points_from_mark_index(
    mark_points: list[tuple[datetime, float]],
    index_points: list[tuple[datetime, float]],
    *,
    source: str = "mark_index",
) -> list[PremiumIndexPoint]:
    marks = {bucket_at: close for bucket_at, close in mark_points}
    indexes = {bucket_at: close for bucket_at, close in index_points}
    points: list[PremiumIndexPoint] = []
    for bucket_at in sorted(marks.keys() & indexes.keys()):
        mark_price = marks[bucket_at]
        index_price = indexes[bucket_at]
        premium_pct = _premium_pct(mark_price, index_price)
        if premium_pct is None:
            continue
        points.append(
            PremiumIndexPoint(
                bucket_at=bucket_at,
                premium_pct=premium_pct,
                mark_price=mark_price,
                index_price=index_price,
                source=source,
            )
        )
    return points


class PremiumIndexQueryService(PairSpreadQueryService):
    async def query(
        self,
        market: PremiumIndexMarketQuery,
        *,
        hours: int,
        interval_minutes: int = 1,
        now: datetime | None = None,
    ) -> PremiumIndexQueryResult:
        observed_at = now or utc_now()
        end = _floor_minute(observed_at)
        requested_start = end - timedelta(hours=hours)
        warnings: list[str] = []
        failed_window_warnings: list[str] = []
        points: list[PremiumIndexPoint] = []
        used_start = requested_start

        for window_hours in _query_window_hours(hours):
            window_start = end - timedelta(hours=window_hours)
            window_warnings: list[str] = []
            points = await self._fetch_history_with_warning(
                market.exchange,
                market.symbol,
                window_start,
                end,
                interval_minutes,
                window_warnings,
            )
            if points:
                used_start = window_start
                if window_hours != hours:
                    _append_unique(
                        warnings,
                        f"请求{_duration_text(hours)}没有拿到溢价指数历史，已自动改查最近{_duration_text(window_hours)}。",
                    )
                warnings.extend(window_warnings)
                break
            failed_window_warnings.extend(window_warnings)

        raw_point_count = len(points)
        points = densify_premium_points(points, interval_minutes=interval_minutes)
        if len(points) > raw_point_count:
            _append_unique(
                warnings,
                f"原始溢价指数历史粒度较粗，已按{interval_minutes}分钟线性补点用于画图。",
            )

        current = await self.current(market)
        if not points and current.premium_pct is not None:
            points = [
                PremiumIndexPoint(
                    bucket_at=current.observed_at,
                    premium_pct=current.premium_pct,
                    mark_price=current.mark_price,
                    index_price=current.index_price,
                    source=current.source,
                )
            ]
            _append_unique(warnings, "没有拿到历史溢价指数，已从当前点开始绘图。")

        if not points:
            suffix = f": {'; '.join(dict.fromkeys(failed_window_warnings))}" if failed_window_warnings else ""
            raise PremiumIndexQueryError(f"没有拿到可用的溢价指数{suffix}")

        earliest_expected = used_start + timedelta(minutes=interval_minutes)
        if points[0].bucket_at > earliest_expected:
            warnings.insert(
                0,
                f"请求{_duration_text(hours)}，最早可用数据为{_display_time(points[0].bucket_at)}，已按可获取数据展示。",
            )

        return PremiumIndexQueryResult(
            exchange=market.exchange,
            symbol=market.symbol,
            hours=hours,
            interval_minutes=interval_minutes,
            observed_at=observed_at,
            point_count=len(points),
            first_seen_at=points[0].bucket_at,
            last_seen_at=points[-1].bucket_at,
            premium_pct=_stats([point.premium_pct for point in points]),
            current=current,
            points=points,
            warnings=list(dict.fromkeys(warnings)),
        )

    async def current(self, market: PremiumIndexMarketQuery) -> PremiumIndexCurrentSnapshot:
        if market.exchange == "okx":
            return await self._fetch_okx_current_premium(market.symbol)
        leg = await self._fetch_current_leg(market.exchange, market.symbol)
        premium = _premium_pct(leg.mark_price, leg.index_price)
        mid_premium = _premium_pct(leg.mid_price, leg.index_price)
        return PremiumIndexCurrentSnapshot(
            observed_at=utc_now(),
            exchange=market.exchange,
            symbol=market.symbol,
            raw_symbol=leg.raw_symbol,
            mark_price=leg.mark_price,
            index_price=leg.index_price,
            mid_price=leg.mid_price,
            last_price=leg.last_price,
            premium_pct=premium,
            mid_premium_pct=mid_premium,
            funding_rate_pct=leg.funding_rate_pct,
            funding_next_rate_pct=leg.funding_next_rate_pct,
            funding_next_time=leg.funding_next_time,
            source="mark_index" if premium is not None else "unavailable",
        )

    async def _fetch_history_with_warning(
        self,
        exchange: str,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
        warnings: list[str],
    ) -> list[PremiumIndexPoint]:
        try:
            return await self._fetch_history(exchange, symbol, start, end, interval_minutes)
        except Exception as exc:  # noqa: BLE001 - return current point when history is unavailable.
            _append_unique(warnings, f"{exchange}:{symbol} 溢价指数历史失败: {_market_data_error_text(exchange, exc)}")
            return []

    async def _fetch_history(
        self,
        exchange: str,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PremiumIndexPoint]:
        handlers: dict[str, Callable[[str, datetime, datetime, int], Awaitable[list[PremiumIndexPoint]]]] = {
            "binance": lambda s, a, b, i: self._fetch_binance_like_premium(
                "https://fapi.binance.com",
                s,
                a,
                b,
                i,
                "binance_premium_index",
            ),
            "aster": lambda s, a, b, i: self._fetch_binance_like_premium(
                "https://fapi.asterdex.com",
                s,
                a,
                b,
                i,
                "aster_premium_index",
            ),
            "bybit": self._fetch_bybit_premium,
            "gate": self._fetch_gate_premium,
            "bitget": self._fetch_bitget_mark_index_premium,
            "okx": self._fetch_okx_mark_index_premium,
            "hyperliquid": self._fetch_hyperliquid_premium,
        }
        return await handlers[exchange](symbol, start, end, interval_minutes)

    async def _fetch_binance_like_premium(
        self,
        base_url: str,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
        source: str,
    ) -> list[PremiumIndexPoint]:
        raw = _compact_symbol(symbol)
        interval_ms = _interval_ms(interval_minutes)
        cursor = _to_ms(start)
        end_ms = _to_ms(end)
        points: list[PremiumIndexPoint] = []
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + 1500 * interval_ms - 1)
            rows = await self._get_json(
                f"{base_url}/fapi/v1/premiumIndexKlines?symbol={raw}&interval={interval_minutes}m"
                f"&startTime={cursor}&endTime={chunk_end}&limit=1500"
            )
            parsed = [
                point
                for row in rows if isinstance(row, list)
                if (point := _parse_premium_row(row, source=source)) is not None
                and start <= point.bucket_at <= end
            ]
            if not parsed:
                cursor = chunk_end + 1
                continue
            points.extend(parsed)
            next_cursor = max(_to_ms(point.bucket_at) for point in parsed) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_bybit_premium(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PremiumIndexPoint]:
        raw = _compact_symbol(symbol)
        interval_ms = _interval_ms(interval_minutes)
        cursor = _to_ms(start)
        end_ms = _to_ms(end)
        points: list[PremiumIndexPoint] = []
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + 1000 * interval_ms - 1)
            payload = await self._get_json(
                "https://api.bybit.com/v5/market/premium-index-price-kline"
                f"?category=linear&symbol={raw}&interval={interval_minutes}"
                f"&start={cursor}&end={chunk_end}&limit=1000"
            )
            rows = payload.get("result", {}).get("list", []) if isinstance(payload, dict) else []
            parsed = [
                point
                for row in rows if isinstance(row, list)
                if (point := _parse_premium_row(row, source="bybit_premium_index")) is not None
                and start <= point.bucket_at <= end
            ]
            if not parsed:
                cursor = chunk_end + 1
                continue
            points.extend(parsed)
            next_cursor = max(_to_ms(point.bucket_at) for point in parsed) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_sorted(points)

    async def _fetch_gate_premium(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PremiumIndexPoint]:
        contract = _gate_contract(symbol)
        expected = max(10, ceil((end - start).total_seconds() / 60 / interval_minutes) + 5)
        limit = min(1000, expected)
        rows = await self._get_json(
            "https://api.gateio.ws/api/v4/futures/usdt/premium_index"
            f"?contract={contract}&interval={interval_minutes}m&limit={limit}"
        )
        return _dedupe_sorted(
            [
                point
                for row in rows if isinstance(row, (dict, list))
                if (point := _parse_premium_row(row, source="gate_premium_index")) is not None
                and start <= point.bucket_at <= end
            ]
        )

    async def _fetch_bitget_mark_index_premium(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PremiumIndexPoint]:
        mark_points, index_points = await asyncio.gather(
            self._fetch_bitget_price_klines("history-mark-candles", symbol, start, end, interval_minutes),
            self._fetch_bitget_price_klines("history-index-candles", symbol, start, end, interval_minutes),
        )
        return build_premium_points_from_mark_index(mark_points, index_points, source="bitget_mark_index")

    async def _fetch_bitget_price_klines(
        self,
        endpoint: str,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[tuple[datetime, float]]:
        raw = _compact_symbol(symbol)
        interval_ms = _interval_ms(interval_minutes)
        cursor = _to_ms(start)
        end_ms = _to_ms(end)
        points: list[tuple[datetime, float]] = []
        while cursor < end_ms:
            chunk_end = min(end_ms, cursor + 200 * interval_ms - 1)
            payload = await self._get_json(
                f"https://api.bitget.com/api/v2/mix/market/{endpoint}"
                f"?symbol={raw}&productType=USDT-FUTURES&granularity={interval_minutes}m"
                f"&startTime={cursor}&endTime={chunk_end}&limit=200"
            )
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            parsed = [
                point
                for row in rows if isinstance(row, list)
                if (point := _parse_price_kline(row)) is not None
                and start <= point[0] <= end
            ]
            if not parsed:
                cursor = chunk_end + 1
                continue
            points.extend(parsed)
            next_cursor = max(_to_ms(bucket_at) for bucket_at, _ in parsed) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return _dedupe_price_points(points)

    async def _fetch_okx_mark_index_premium(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PremiumIndexPoint]:
        inst_id = _okx_inst_id(symbol)
        index_id = inst_id.removesuffix("-SWAP")
        mark_points, index_points = await asyncio.gather(
            self._fetch_okx_price_klines("history-mark-price-candles", inst_id, start, end, interval_minutes),
            self._fetch_okx_price_klines("history-index-candles", index_id, start, end, interval_minutes),
        )
        return build_premium_points_from_mark_index(mark_points, index_points, source="okx_mark_index")

    async def _fetch_okx_price_klines(
        self,
        path: str,
        inst_id: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[tuple[datetime, float]]:
        cursor = _to_ms(end)
        start_ms = _to_ms(start)
        points: list[tuple[datetime, float]] = []
        while cursor > start_ms:
            payload = await self._get_json(
                f"https://www.okx.com/api/v5/market/{path}"
                f"?instId={inst_id}&bar={interval_minutes}m&after={cursor}&limit=100"
            )
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            parsed = [
                point
                for row in rows if isinstance(row, list)
                if (point := _parse_price_kline(row)) is not None
                and start <= point[0] <= end
            ]
            if not parsed:
                break
            points.extend(parsed)
            oldest = min(_to_ms(bucket_at) for bucket_at, _ in parsed)
            if oldest >= cursor:
                break
            cursor = oldest
        return _dedupe_price_points(points)

    async def _fetch_hyperliquid_premium(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PremiumIndexPoint]:
        del interval_minutes
        raw_coin, _ = await self._resolve_hyperliquid_coin(symbol)
        rows = await self._post_json(
            "https://api.hyperliquid.xyz/info",
            {
                "type": "fundingHistory",
                "coin": raw_coin,
                "startTime": _to_ms(start),
                "endTime": _to_ms(end),
            },
        )
        return _dedupe_sorted(
            [
                point
                for row in rows if isinstance(row, dict)
                if (point := _parse_premium_row(row, source="hyperliquid_funding_premium")) is not None
                and start <= point.bucket_at <= end
            ]
        )

    async def _fetch_okx_current_premium(self, symbol: str) -> PremiumIndexCurrentSnapshot:
        inst_id = _okx_inst_id(symbol)
        index_id = inst_id.removesuffix("-SWAP")
        mark_payload, index_payload, ticker_payload, funding_payload = await asyncio.gather(
            self._get_json(f"https://www.okx.com/api/v5/public/mark-price?instType=SWAP&instId={inst_id}"),
            self._get_json(f"https://www.okx.com/api/v5/market/index-tickers?instId={index_id}"),
            self._get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"),
            self._get_json(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"),
        )
        mark_row = _first_row(mark_payload.get("data", [])) if isinstance(mark_payload, dict) else {}
        index_row = _first_row(index_payload.get("data", [])) if isinstance(index_payload, dict) else {}
        ticker_row = _first_row(ticker_payload.get("data", [])) if isinstance(ticker_payload, dict) else {}
        funding_row = _first_row(funding_payload.get("data", [])) if isinstance(funding_payload, dict) else {}
        mark = _positive(parse_float(mark_row.get("markPx")))
        index = _positive(parse_float(index_row.get("idxPx")))
        mid = _mid(parse_float(ticker_row.get("bidPx")), parse_float(ticker_row.get("askPx")))
        funding = parse_float(funding_row.get("fundingRate"))
        next_funding = parse_float(funding_row.get("nextFundingRate"))
        return PremiumIndexCurrentSnapshot(
            observed_at=utc_now(),
            exchange="okx",
            symbol=_compact_symbol(symbol),
            raw_symbol=inst_id,
            mark_price=mark,
            index_price=index,
            mid_price=mid,
            last_price=_positive(parse_float(ticker_row.get("last"))),
            premium_pct=_premium_pct(mark, index),
            mid_premium_pct=_premium_pct(mid, index),
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=next_funding * 100 if next_funding is not None else None,
            funding_next_time=parse_datetime_ms(funding_row.get("nextFundingTime"))
            or parse_datetime_ms(funding_row.get("fundingTime")),
            source="mark_index",
        )


def _parse_price_kline(row: list[Any]) -> tuple[datetime, float] | None:
    if len(row) <= 4:
        return None
    bucket_at = _bucket_datetime_ms(row[0])
    close = _positive(parse_float(row[4]))
    if bucket_at is None or close is None:
        return None
    return bucket_at, close


def _dedupe_price_points(points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    by_bucket: dict[datetime, float] = {}
    for bucket_at, close in points:
        by_bucket[bucket_at] = close
    return [(bucket_at, by_bucket[bucket_at]) for bucket_at in sorted(by_bucket)]


def _parse_premium_row(row: dict[str, Any] | list[Any], *, source: str) -> PremiumIndexPoint | None:
    if isinstance(row, list):
        if len(row) <= 4:
            return None
        bucket_at = _bucket_datetime_ms(row[0])
        premium_pct = _ratio_to_pct(parse_float(row[4]))
    else:
        bucket_at = (
            _bucket_datetime_ms(row.get("time"))
            or _bucket_datetime_ms(row.get("timestamp"))
            or _bucket_datetime_ms(row.get("ts"))
            or _bucket_datetime_seconds(row.get("t"))
            or _bucket_datetime_seconds(row.get("funding_time"))
        )
        premium_pct = _ratio_to_pct(
            parse_float(
                row.get("premium")
                or row.get("premiumIndex")
                or row.get("premium_index")
                or row.get("p")
                or row.get("close")
                or row.get("c")
            )
        )
    if bucket_at is None or premium_pct is None:
        return None
    return PremiumIndexPoint(bucket_at=bucket_at, premium_pct=premium_pct, source=source)


def _mid(bid: float | None, ask: float | None) -> float | None:
    bid = _positive(bid)
    ask = _positive(ask)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2
