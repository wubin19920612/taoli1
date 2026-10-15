"""Compare complete historical statistics with the frozen pre-optimization lookup."""
import random
import sys
from datetime import timedelta

import pytest
from instrument_statistics_reference import ReferenceStatisticsService
from test_instrument_statistics import NOW, instrument, market

from app.services.instrument_spreads import instrument_market_id
from app.services.instrument_statistics import InstrumentStatisticsService


def scenario(count=8, selection="dense", seed=0, coverage="full"):
    rng = random.Random(seed)
    markets = [market("hyperliquid", dex=f"dex{i}", raw_symbol=f"dex{i}:BTC",
                      symbol_alias_price_multiplier=0.001 if i % 2 else 1,
                      contract_size_multiplier=10 if i % 3 else 1)
               for i in range(count)]
    lookup = instrument(markets)
    if selection == "sparse":
        lookup.spreads = lookup.spreads[:2]
    elif selection == "none":
        lookup.spreads = []
    histories = {}
    for i, target in enumerate(markets):
        minutes = list(range(-1, 1445))
        rng.shuffle(minutes)
        if coverage == "missing":
            minutes = [minute for minute in minutes if rng.random() > 0.3]
        elif coverage == "disjoint":
            minutes = [minute for minute in minutes if minute % count == i]
        elif coverage == "empty":
            minutes = []
        prices = {NOW - timedelta(minutes=minute):
                  (100 + i if seed == 0 else rng.uniform(10, 500))
                  for minute in minutes}
        if coverage == "extreme":
            prices = {bucket: (sys.float_info.max if j % 3 == i % 3 else value)
                      for j, (bucket, value) in enumerate(prices.items())}
        histories[instrument_market_id(target)] = (NOW, prices, "upstream" if i == 2 else None)
    return lookup, histories


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["dense", "sparse", "none"])
@pytest.mark.parametrize("coverage", ["full", "missing", "disjoint", "empty", "extreme"])
@pytest.mark.parametrize("seed", [0, 73])
async def test_complete_output_matches_reference(selection, coverage, seed):
    lookup, histories = scenario(selection=selection, coverage=coverage, seed=seed)
    original = lookup.model_dump_json()
    before, after = ReferenceStatisticsService(), InstrumentStatisticsService()
    before._cache.update(histories)
    after._cache.update(histories)
    for now in (NOW, NOW + timedelta(minutes=1)):
        expected = await before.lookup(lookup, now=now)
        actual = await after.lookup(lookup, now=now)
        # JSON handles non-finite legacy results consistently (null); also
        # compares insertion order, timestamps, coverage, errors and estimates.
        assert actual.model_dump_json() == expected.model_dump_json()
    assert lookup.model_dump_json() == original
    assert list(before._cache.items()) == list(after._cache.items())


@pytest.mark.asyncio
async def test_ties_choose_latest_minute_and_complete_window_excludes_edges():
    lookup, histories = scenario(count=2)
    service = InstrumentStatisticsService()
    service._cache.update(histories)
    result = await service.lookup(lookup, now=NOW)
    assert len(result.spreads) == 2
    for stats in result.spreads.values():
        assert stats.point_count == 1440
        assert stats.complete
        assert stats.max_at == NOW
        assert stats.first_seen_at == NOW - timedelta(minutes=1439)
        assert stats.last_seen_at == NOW
        assert stats.is_estimated
