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
from app.models.pair_spread import PairSpreadKlinePoint
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


def _filter_interval_points(points: list[PremiumIndexPoint], interval_minutes: int) -> list[PremiumIndexPoint]:
    if interval_minutes <= 1:
        return _dedupe_sorted(points)
    by_bucket: dict[datetime, PremiumIndexPoint] = {}
    for point in sorted(points, key=lambda item: item.bucket_at):
        bucket_minute = point.bucket_at.minute - (point.bucket_at.minute % interval_minutes)
        bucket_at = point.bucket_at.replace(minute=bucket_minute, second=0, microsecond=0)
        by_bucket[bucket_at] = point.model_copy(update={"bucket_at": bucket_at})
    return [by_bucket[key] for key in sorted(by_bucket)]


def build_hyperliquid_candle_premium_points(
    candles: list[PairSpreadKlinePoint],
    premium_anchors: list[PremiumIndexPoint],
    *,
    interval_minutes: int,
) -> list[PremiumIndexPoint]:
    sorted_candles = sorted(candles, key=lambda point: point.bucket_at)
    sorted_anchors = _dedupe_sorted(premium_anchors)
    if len(sorted_candles) < 2 or len(sorted_anchors) < 2:
        return []

    tolerance = timedelta(minutes=max(interval_minutes * 3, 5))
    oracle_anchors: list[tuple[datetime, float]] = []
    for anchor in sorted_anchors:
        close = _nearest_candle_close(sorted_candles, anchor.bucket_at, tolerance=tolerance)
        premium_ratio = anchor.premium_pct / 100
        if close is None or 1 + premium_ratio <= 0:
            continue
        oracle_anchors.append((anchor.bucket_at, close / (1 + premium_ratio)))

    if len(oracle_anchors) < 2:
        return []

    points: list[PremiumIndexPoint] = []
    anchor_index = 0
    for candle in sorted_candles:
        while anchor_index + 1 < len(oracle_anchors) and candle.bucket_at > oracle_anchors[anchor_index + 1][0]:
            anchor_index += 1
        if anchor_index + 1 >= len(oracle_anchors):
            break

        left_at, left_oracle = oracle_anchors[anchor_index]
        right_at, right_oracle = oracle_anchors[anchor_index + 1]
        if candle.bucket_at < left_at or candle.bucket_at > right_at:
            continue

        gap_seconds = (right_at - left_at).total_seconds()
        if gap_seconds <= 0:
            continue
        ratio = (candle.bucket_at - left_at).total_seconds() / gap_seconds
        oracle = left_oracle + (right_oracle - left_oracle) * ratio
        if oracle <= 0:
            continue
        points.append(
            PremiumIndexPoint(
                bucket_at=candle.bucket_at,
                premium_pct=(candle.close - oracle) / oracle * 100,
                mark_price=candle.close,
                index_price=oracle,
                source="hyperliquid_candle_funding_anchor",
            )
        )

    return _dedupe_sorted(points)


def _nearest_candle_close(
    candles: list[PairSpreadKlinePoint],
    target: datetime,
    *,
    tolerance: timedelta,
) -> float | None:
    nearest: PairSpreadKlinePoint | None = None
    nearest_gap: timedelta | None = None
    for candle in candles:
        gap = abs(candle.bucket_at - target)
        if nearest_gap is None or gap < nearest_gap:
            nearest = candle
            nearest_gap = gap
    if nearest is None or nearest_gap is None or nearest_gap > tolerance:
        return None
    return nearest.close


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

        if any(point.source == "hyperliquid_candle_funding_anchor" for point in points):
            _append_unique(
                warnings,
                "Hyperliquid 没有公开分钟级历史 premium 接口，曲线已用分钟K线和小时 premium 锚点估算。",
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
        if market.exchange in {"binance", "aster", "bybit", "gate"}:
            return await self._fetch_current_with_official_premium(market.exchange, market.symbol)
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
            funding_interval_hours=leg.funding_interval_hours,
            funding_rate_upper_pct=leg.funding_rate_upper_pct,
            funding_rate_lower_pct=leg.funding_rate_lower_pct,
            source="mark_index" if premium is not None else "unavailable",
        )

    async def _fetch_current_with_official_premium(
        self,
        exchange: str,
        symbol: str,
    ) -> PremiumIndexCurrentSnapshot:
        observed_at = utc_now()
        leg = await self._fetch_current_leg(exchange, symbol)
        try:
            points = await self._fetch_history(exchange, symbol, observed_at - timedelta(minutes=10), observed_at, 1)
        except Exception:  # noqa: BLE001 - current ticker fields are still useful when official P is unavailable.
            points = []
        latest = points[-1] if points else None
        mark_premium = _premium_pct(leg.mark_price, leg.index_price)
        mid_premium = _premium_pct(leg.mid_price, leg.index_price)
        premium = latest.premium_pct if latest is not None else mark_premium
        return PremiumIndexCurrentSnapshot(
            observed_at=observed_at,
            exchange=exchange,
            symbol=leg.symbol,
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
            funding_interval_hours=leg.funding_interval_hours,
            funding_rate_upper_pct=leg.funding_rate_upper_pct,
            funding_rate_lower_pct=leg.funding_rate_lower_pct,
            source=latest.source if latest is not None else "mark_index_fallback",
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
            "okx": self._fetch_okx_premium_history,
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

    async def _fetch_okx_premium_history(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int,
    ) -> list[PremiumIndexPoint]:
        try:
            points = await self._fetch_okx_official_premium_history(symbol, start, end)
        except Exception:  # noqa: BLE001 - fall back to mark/index deviation when OKX premium history is unavailable.
            points = []
        if points:
            return _filter_interval_points(points, interval_minutes)
        return await self._fetch_okx_mark_index_premium(symbol, start, end, interval_minutes)

    async def _fetch_okx_official_premium_history(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PremiumIndexPoint]:
        inst_id = _okx_inst_id(symbol)
        start_ms = _to_ms(start)
        cursor: int | None = None
        parsed_pairs: list[tuple[int, PremiumIndexPoint]] = []
        seen_cursors: set[int] = set()
        for _ in range(120):
            url = f"https://www.okx.com/api/v5/public/premium-history?instId={inst_id}&limit=100"
            if cursor is not None:
                url = f"{url}&after={cursor}"
            payload = await self._get_json(url)
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if not rows:
                break
            row_timestamps: list[int] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                timestamp = parse_datetime_ms(row.get("ts"))
                premium_pct = _ratio_to_pct(parse_float(row.get("premium")))
                if timestamp is None or premium_pct is None:
                    continue
                timestamp_ms = _to_ms(timestamp)
                row_timestamps.append(timestamp_ms)
                bucket_at = _floor_minute(timestamp)
                if start <= bucket_at <= end:
                    parsed_pairs.append(
                        (
                            timestamp_ms,
                            PremiumIndexPoint(
                                bucket_at=bucket_at,
                                premium_pct=premium_pct,
                                source="okx_premium_index",
                            ),
                        )
                    )
            if not row_timestamps:
                break
            oldest = min(row_timestamps)
            if oldest <= start_ms or oldest in seen_cursors:
                break
            seen_cursors.add(oldest)
            cursor = oldest

        by_bucket: dict[datetime, tuple[int, PremiumIndexPoint]] = {}
        for timestamp_ms, point in parsed_pairs:
            existing = by_bucket.get(point.bucket_at)
            if existing is None or timestamp_ms >= existing[0]:
                by_bucket[point.bucket_at] = (timestamp_ms, point)
        return [by_bucket[key][1] for key in sorted(by_bucket)]

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
        raw_coin, _ = await self._resolve_hyperliquid_coin(symbol)
        anchor_start = start - timedelta(hours=1)
        rows, candles = await asyncio.gather(
            self._post_json(
                "https://api.hyperliquid.xyz/info",
                {
                    "type": "fundingHistory",
                    "coin": raw_coin,
                    "startTime": _to_ms(anchor_start),
                    "endTime": _to_ms(end),
                },
            ),
            self._fetch_hyperliquid_klines(symbol, start, end, interval_minutes),
        )
        anchors = [
            point
            for row in rows if isinstance(row, dict)
            if (point := _parse_premium_row(row, source="hyperliquid_funding_premium")) is not None
            and anchor_start <= point.bucket_at <= end
        ]
        try:
            current = await self._fetch_hyperliquid_current(symbol)
        except Exception:  # noqa: BLE001 - historical candles and funding anchors can still be useful.
            current = None
        if current is not None:
            current_premium = _premium_pct(current.mark_price, current.index_price)
            if current_premium is not None:
                anchors.append(
                    PremiumIndexPoint(
                        bucket_at=end,
                        premium_pct=current_premium,
                        mark_price=current.mark_price,
                        index_price=current.index_price,
                        source="hyperliquid_current_anchor",
                    )
                )
        anchors = _dedupe_sorted(anchors)
        candle_points = build_hyperliquid_candle_premium_points(
            candles,
            anchors,
            interval_minutes=interval_minutes,
        )
        return candle_points or _dedupe_sorted(
            [
                point
                for point in anchors
                if start <= point.bucket_at <= end
            ]
        )

    async def _fetch_okx_current_premium(self, symbol: str) -> PremiumIndexCurrentSnapshot:
        observed_at = utc_now()
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
        try:
            premium_points = await self._fetch_okx_official_premium_history(
                symbol,
                observed_at - timedelta(minutes=10),
                observed_at,
            )
        except Exception:  # noqa: BLE001 - mark/index deviation is still useful as a fallback.
            premium_points = []
        latest_premium = premium_points[-1] if premium_points else None
        mark_premium = _premium_pct(mark, index)
        return PremiumIndexCurrentSnapshot(
            observed_at=observed_at,
            exchange="okx",
            symbol=_compact_symbol(symbol),
            raw_symbol=inst_id,
            mark_price=mark,
            index_price=index,
            mid_price=mid,
            last_price=_positive(parse_float(ticker_row.get("last"))),
            premium_pct=latest_premium.premium_pct if latest_premium is not None else mark_premium,
            mid_premium_pct=_premium_pct(mid, index),
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_next_rate_pct=next_funding * 100 if next_funding is not None else None,
            funding_next_time=parse_datetime_ms(funding_row.get("nextFundingTime"))
            or parse_datetime_ms(funding_row.get("fundingTime")),
            funding_interval_hours=None,
            funding_rate_upper_pct=None,
            funding_rate_lower_pct=None,
            source=latest_premium.source if latest_premium is not None else "mark_index_fallback",
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
