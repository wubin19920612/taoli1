"""Frozen lookup calculation from 5a3bc91; fetching/cache behavior is unchanged."""
import asyncio
from datetime import UTC, datetime, timedelta
from itertools import combinations

from app.models.instrument import (
    InstrumentLookupResult,
    InstrumentPriceChangeStats,
    InstrumentSpread24hStats,
    InstrumentStatisticsResult,
)
from app.services.instrument_spreads import instrument_market_id
from app.services.instrument_statistics import InstrumentStatisticsService, _close_spread


class ReferenceStatisticsService(InstrumentStatisticsService):
    async def lookup(
        self, instrument: InstrumentLookupResult, *, now: datetime | None = None
    ) -> InstrumentStatisticsResult:
        end_at = (now or datetime.now(UTC)).replace(second=0, microsecond=0)
        histories = await asyncio.gather(*(
            self._history(market, end_at) for market in instrument.markets
        ))
        result = InstrumentStatisticsResult(symbol=instrument.symbol, observed_at=end_at)
        prices_by_id: dict[str, dict[datetime, float]] = {}
        for market, (_, prices, error) in zip(instrument.markets, histories, strict=True):
            key = instrument_market_id(market)
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
                    stats.error = "分钟K线已过期，未计算涨跌幅"
            result.markets[key] = stats

        eligible_ids = set()
        for spread in instrument.spreads:
            eligible_ids.add(spread.id)
            buy_id, sell_id = spread.id.split("->", 1)
            eligible_ids.add(f"{sell_id}->{buy_id}")
        start_at = end_at - timedelta(hours=24)
        for first, second in combinations(instrument.markets, 2):
            first_id, second_id = instrument_market_id(first), instrument_market_id(second)
            common = sorted(
                bucket for bucket in prices_by_id[first_id].keys() & prices_by_id[second_id].keys()
                if start_at <= bucket < end_at
            )
            for buy_id, sell_id in ((first_id, second_id), (second_id, first_id)):
                comparison_id = f"{buy_id}->{sell_id}"
                if comparison_id not in eligible_ids:
                    continue
                stats = InstrumentSpread24hStats(point_count=len(common))
                if common:
                    maximum, maximum_at = max(
                        (_close_spread(prices_by_id[buy_id][bucket], prices_by_id[sell_id][bucket]), bucket)
                        for bucket in common
                    )
                    stats.max_spread_pct = maximum
                    stats.max_at = maximum_at + timedelta(minutes=1)
                    stats.first_seen_at = common[0] + timedelta(minutes=1)
                    stats.last_seen_at = common[-1] + timedelta(minutes=1)
                    stats.complete = len(common) == 1440
                else:
                    errors = [result.markets[key].error for key in (buy_id, sell_id)]
                    stats.error = "; ".join(error for error in errors if error) or "双方无同一分钟历史价格"
                result.spreads[comparison_id] = stats
        return result
