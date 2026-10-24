import asyncio
import math
import sys
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from itertools import combinations

from app.models.instrument import (
    InstrumentLookupResult,
    InstrumentPriceChangeStats,
    InstrumentSpread24hStats,
    InstrumentStatisticsResult,
)
from app.models.market import MarketSnapshot
from app.models.pair_spread import PairSpreadKlinePoint
from app.services.instrument_spreads import instrument_market_id
from app.services.pair_spread_query import PairSpreadQueryService, PartialKlineHistoryError

# With positive prices in this range both numerator and denominator stay finite.
_SAFE_SPREAD_PRICE = sys.float_info.max / 4


def _history_cache_key(market: MarketSnapshot) -> str:
    key = instrument_market_id(market)
    if market.binding_revision or market.binding_fingerprint:
        key = f"{key}:{market.binding_revision}:{market.binding_fingerprint}"
    return key


def _close_spread(buy: float, sell: float) -> float:
    return 2 * (sell - buy) / (sell + buy) * 100


def _merge_coverage(
    ranges: list[tuple[datetime, datetime]], start: datetime, end: datetime,
) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for left, right in sorted((max(a, start), min(b, end)) for a, b in ranges):
        if left >= right:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return merged


def _missing_ranges(
    coverage: list[tuple[datetime, datetime]], start: datetime, end: datetime,
) -> list[tuple[datetime, datetime]]:
    # Re-read the latest three completed minutes to tolerate delayed publication.
    known = _merge_coverage(coverage, start, end - timedelta(minutes=3))
    missing = []
    cursor = start
    for left, right in known:
        if cursor < left:
            missing.append((cursor, left))
        cursor = right
    if cursor < end:
        missing.append((cursor, end))
    return list(reversed(missing))  # Current prices before older backfill.


class InstrumentStatisticsService:
    def __init__(self, query_service: PairSpreadQueryService | None = None) -> None:
        self.query_service = query_service
        self._cache: OrderedDict[
            str, tuple[datetime, dict[datetime, float], str | None]
        ] = OrderedDict()
        self._pending: dict[str, asyncio.Task] = {}
        self._coverage: dict[str, list[tuple[datetime, datetime]]] = {}
        self._semaphore = asyncio.Semaphore(4)

    async def aclose(self) -> None:
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.query_service is not None:
            await self.query_service.aclose()

    async def _fetch(
        self, market: MarketSnapshot, end_at: datetime
    ) -> tuple[datetime, dict[datetime, float], str | None]:
        key = _history_cache_key(market)
        start_at = end_at - timedelta(hours=24, minutes=3)
        previous = self._cache.get(key)
        prices = {at: price for at, price in (previous[1] if previous else {}).items()
                  if start_at <= at < end_at}
        coverage = _merge_coverage(self._coverage.get(key, []), start_at, end_at) if prices else []

        def merge_prices(points: list[PairSpreadKlinePoint]) -> None:
            prices.update({
                point.bucket_at: point.close * market.symbol_alias_price_multiplier
                for point in points
                if start_at <= point.bucket_at < end_at
                and point.bucket_at.second == 0
                and point.bucket_at.microsecond == 0
                and math.isfinite(point.close * market.symbol_alias_price_multiplier)
                and point.close * market.symbol_alias_price_multiplier > 0
            })

        error = None
        try:
            async with self._semaphore:
                async with asyncio.timeout(45):
                    if self.query_service is None:
                        self.query_service = PairSpreadQueryService()
                    for left, right in _missing_ranges(coverage, start_at, end_at):
                        try:
                            points = await self.query_service.fetch_market_klines(
                                market, start=left, end=right - timedelta(milliseconds=1),
                            )
                        except PartialKlineHistoryError as exc:
                            merge_prices(exc.points)
                            coverage.append((exc.covered_start, exc.covered_end))
                            raise
                        merge_prices(points)
                        coverage.append((left, right))
        except (TimeoutError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            error = "历史行情请求超时" if isinstance(exc, TimeoutError) else str(exc) or type(exc).__name__
        cached = (end_at, prices, error or (None if prices else "未返回可用分钟K线"))
        self._coverage[key] = _merge_coverage(coverage, start_at, end_at) if prices else []
        self._cache[key] = cached
        self._cache.move_to_end(key)
        while len(self._cache) > 128:
            evicted, _ = self._cache.popitem(last=False)
            self._coverage.pop(evicted, None)
        return cached

    async def _history(
        self, market: MarketSnapshot, end_at: datetime
    ) -> tuple[datetime, dict[datetime, float], str | None]:
        key = _history_cache_key(market)
        cached = self._cache.get(key)
        if cached is not None and timedelta(0) <= end_at - cached[0] < timedelta(minutes=2):
            self._cache.move_to_end(key)
            return cached
        task = self._pending.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch(market, end_at))
            self._pending[key] = task

            def forget(completed: asyncio.Task) -> None:
                if self._pending.get(key) is completed:
                    self._pending.pop(key, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(forget)
        return await asyncio.shield(task)

    async def lookup(
        self, instrument: InstrumentLookupResult, *, now: datetime | None = None
    ) -> InstrumentStatisticsResult:
        end_at = (now or datetime.now(UTC)).replace(second=0, microsecond=0)
        histories = await asyncio.gather(*(
            self._history(market, end_at) for market in instrument.markets
        ))
        result = InstrumentStatisticsResult(symbol=instrument.symbol, observed_at=end_at)
        prices_by_id: dict[str, dict[datetime, float]] = {}
        market_ids: list[str] = []
        finite_spreads: dict[str, bool] = {}
        for market, (_, prices, error) in zip(instrument.markets, histories, strict=True):
            key = instrument_market_id(market)
            market_ids.append(key)
            prices_by_id[key] = prices
            stats = InstrumentPriceChangeStats(error=error)
            if prices:
                latest_at = max(prices)
                if end_at - latest_at <= timedelta(minutes=3):
                    stats.observed_at = latest_at + timedelta(minutes=1)
                    for hours in (1, 24):
                        target = latest_at - timedelta(hours=hours)
                        reference = prices.get(target)
                        if reference is not None:
                            setattr(stats, f"change_{hours}h_pct", (prices[latest_at] / reference - 1) * 100)
                else:
                    stats.error = "; ".join(filter(None, [error, "分钟K线已过期，未计算涨跌幅"]))
            result.markets[key] = stats

        eligible_ids = set()
        for spread in instrument.spreads:
            eligible_ids.add(spread.id)
            buy_id, sell_id = spread.id.split("->", 1)
            eligible_ids.add(f"{sell_id}->{buy_id}")
        start_at = end_at - timedelta(hours=24)
        for first_id, second_id in combinations(market_ids, 2):
            if f"{first_id}->{second_id}" not in eligible_ids:
                continue
            common = [
                bucket for bucket in prices_by_id[first_id].keys() & prices_by_id[second_id].keys()
                if start_at <= bucket < end_at
            ]
            for key in (first_id, second_id):
                if key not in finite_spreads:
                    finite_spreads[key] = all(
                        0 < price <= _SAFE_SPREAD_PRICE for price in prices_by_id[key].values()
                    )
            if not (finite_spreads[first_id] and finite_spreads[second_id]):
                # Preserve the original comparison order for extreme/invalid
                # floats (NaN is not totally ordered). Ordinary finite maxima
                # use (spread, timestamp), so ties pick the latest minute in
                # any traversal order and need no full timestamp sort.
                common.sort()
            first_at = min(common) + timedelta(minutes=1) if common else None
            last_at = max(common) + timedelta(minutes=1) if common else None
            for buy_id, sell_id in ((first_id, second_id), (second_id, first_id)):
                comparison_id = f"{buy_id}->{sell_id}"
                if comparison_id not in eligible_ids:
                    continue
                stats = InstrumentSpread24hStats(point_count=len(common))
                errors = [result.markets[key].error for key in (buy_id, sell_id)]
                stats.error = "; ".join(dict.fromkeys(error for error in errors if error)) or None
                if common:
                    maximum, maximum_at = max(
                        (_close_spread(prices_by_id[buy_id][bucket], prices_by_id[sell_id][bucket]), bucket)
                        for bucket in common
                    )
                    minimum, minimum_at = min(
                        (_close_spread(prices_by_id[buy_id][bucket], prices_by_id[sell_id][bucket]), bucket)
                        for bucket in common
                    )
                    stats.max_spread_pct = maximum
                    stats.max_at = maximum_at + timedelta(minutes=1)
                    stats.min_spread_pct = minimum
                    stats.min_at = minimum_at + timedelta(minutes=1)
                    stats.first_seen_at = first_at
                    stats.last_seen_at = last_at
                    stats.complete = len(common) == 1440
                else:
                    stats.error = stats.error or "双方无同一分钟历史价格"
                result.spreads[comparison_id] = stats
        return result
