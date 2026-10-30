from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadKlinePoint,
    PairSpreadLegQuery,
    PairSpreadPriceField,
)
from app.runtime.cache_census import build_census_targets, run_census
from app.services import pair_spread_query as query
from app.services.realtime_series_cache import (
    REALTIME_CACHE_IDLE_TTL_SECONDS,
    REALTIME_CACHE_MAX_EVICTION_MARKERS,
    REALTIME_CACHE_MAX_KEYS,
    REALTIME_CACHE_MAX_TOTAL_POINTS,
    RealtimeSeriesBudget,
    RealtimeSeriesCache,
)

CACHE_NAMES = (
    "_REALTIME_PAIR_SPREAD_CACHE",
    "_REALTIME_PAIR_FUNDING_CACHE",
    "_REALTIME_PAIR_OPEN_INTEREST_CACHE",
    "_REALTIME_SYMBOL_SPREAD_CACHE",
)
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


@pytest.mark.parametrize("points_per_key", [1, 4000])
def test_ten_thousand_keys_per_cache_stay_within_shared_budget(points_per_key):
    budget = RealtimeSeriesBudget(clock=lambda: 0)
    caches = [RealtimeSeriesCache(budget) for _ in CACHE_NAMES]
    for key in range(10_000):
        for index, cache in enumerate(caches):
            value = [None] * points_per_key
            if index == 3:
                value = {"okx": value[:points_per_key // 2],
                         "bybit": value[points_per_key // 2:]}
            cache[str(key)] = value
            assert len(cache) <= REALTIME_CACHE_MAX_KEYS
            assert budget.total_points <= REALTIME_CACHE_MAX_TOTAL_POINTS
    actual_points = sum(len(points) for cache in caches[:3] for points in cache.values())
    actual_points += sum(len(points) for series in caches[3].values() for points in series.values())
    assert actual_points == budget.total_points
    assert len(budget._entries) == sum(map(len, caches))
    assert all(len(cache._evicted) <= REALTIME_CACHE_MAX_EVICTION_MARKERS for cache in caches)
    if points_per_key == 1:
        assert all(len(cache) == REALTIME_CACHE_MAX_KEYS for cache in caches)
    else:
        assert actual_points == REALTIME_CACHE_MAX_TOTAL_POINTS


@pytest.mark.parametrize("operation", ["get", "set"])
def test_idle_ttl_expires_across_instances_and_reads_refresh_access(operation):
    tick = [0.0]
    budget = RealtimeSeriesBudget(clock=lambda: tick[0])
    left, right = RealtimeSeriesCache(budget), RealtimeSeriesCache(budget)
    left["hot"] = [1]
    right["idle"] = [2]
    tick[0] = REALTIME_CACHE_IDLE_TTL_SECONDS - 1
    assert left.get("hot") == [1]
    tick[0] = REALTIME_CACHE_IDLE_TTL_SECONDS
    if operation == "get":
        assert left.get("missing") is None
    else:
        left["new"] = []
    assert right.get("idle") is None
    assert left.get("hot") == [1]
    assert budget.total_points == 1
    tick[0] += REALTIME_CACHE_IDLE_TTL_SECONDS
    assert left.get("hot") is None
    assert budget.total_points == 0


def test_local_and_global_lru_use_access_order_even_with_equal_timestamps():
    budget = RealtimeSeriesBudget(max_points=5, clock=lambda: 0)
    left, right = RealtimeSeriesCache(budget, max_keys=2), RealtimeSeriesCache(budget)
    left["a"] = [1]
    left["b"] = [2]
    assert left["a"] == [1]
    left["c"] = [3]
    assert left.get("b") is None
    right["d"] = [4, 5]
    assert left.get("a") == [1]
    right["e"] = [6, 7, 8]
    # Global LRU drops c, then d (whole keys), retaining recently read a.
    assert left.get("c") is None
    assert right.get("d") is None
    assert left.get("a") == [1]
    assert right.get("e") == [6, 7, 8]
    assert budget.total_points == 4


def test_replace_trim_nested_growth_and_clear_update_accounting():
    budget = RealtimeSeriesBudget(max_points=6)
    cache = RealtimeSeriesCache(budget)
    original = {"okx": [1], "bybit": [2]}
    cache["a"] = original
    original["okx"].append(99)
    fetched = cache["a"]
    fetched["okx"].extend([3, 4])
    assert budget.total_points == 2
    assert cache["a"] == {"okx": [1], "bybit": [2]}
    cache["a"] = fetched
    assert budget.total_points == 4
    cache["b"] = {"gate": [5, 6, 7]}
    assert cache.get("a") is None
    assert budget.total_points == 3
    cache["b"] = {"gate": [7]}
    assert budget.total_points == 1
    cache["empty"] = {}
    assert len(cache) == 2
    cache.clear()
    cache.clear()
    assert budget.total_points == 0
    assert not budget._entries


def test_rewrite_refreshes_ttl_and_lru_without_double_counting():
    tick = [0.0]
    budget = RealtimeSeriesBudget(clock=lambda: tick[0])
    cache = RealtimeSeriesCache(budget, max_keys=2)
    cache["a"] = [1, 2]
    cache["b"] = [3]
    tick[0] = 1700
    cache["a"] = [4]
    cache["c"] = [5]
    assert cache.get("b") is None
    assert budget.total_points == 2
    tick[0] = 1800
    assert cache.get("a") == [4]
    assert cache.restart_status("a") is None


def test_oversize_entry_and_zero_capacity_cannot_exceed_limits():
    budget = RealtimeSeriesBudget(max_points=2)
    cache = RealtimeSeriesCache(budget)
    cache["a"] = [1]
    cache["oversize"] = [1, 2, 3]
    assert len(cache) == 0
    assert budget.total_points == 0
    disabled = RealtimeSeriesCache(budget, max_keys=0)
    disabled["empty"] = []
    assert len(disabled) == 0
    assert not budget._entries


def test_eviction_markers_are_bounded_and_old_misses_still_explain_restart():
    cache = RealtimeSeriesCache(RealtimeSeriesBudget(), max_keys=1)
    warnings = []
    assert query._cached_realtime_series(cache, "first", [], warnings) == []
    assert warnings == []
    cache["first"] = [1]
    for index in range(REALTIME_CACHE_MAX_EVICTION_MARKERS + 2):
        cache[str(index)] = [index]
    assert len(cache._evicted) == REALTIME_CACHE_MAX_EVICTION_MARKERS
    assert query._cached_realtime_series(cache, "first", [], warnings) == []
    assert warnings == [query.REALTIME_CACHE_MISS_WARNING]
    warnings.clear()
    assert query._cached_realtime_series(cache, "1", [], warnings) == []
    assert warnings == [query.REALTIME_CACHE_RESTART_WARNING]
    cache.clear()
    assert not cache._evicted
    assert cache.restart_status("first") is None


def install_caches(monkeypatch, *, max_keys=128):
    tick = [0.0]
    budget = RealtimeSeriesBudget(clock=lambda: tick[0])
    caches = {}
    for name in CACHE_NAMES:
        cache = RealtimeSeriesCache(budget, max_keys=max_keys)
        monkeypatch.setattr(query, name, cache)
        caches[name] = cache
    return tick, budget, caches


class OfflineService(query.PairSpreadQueryService):
    async def _fetch_current_with_warning(self, leg, warnings):
        price = 100 if leg.exchange == "binance" else (102 if leg.dex == "io" else 104)
        return PairSpreadCurrentLeg(
            exchange=leg.exchange, symbol=leg.symbol, market_type=leg.market_type,
            dex=leg.dex, raw_symbol=f"{leg.dex}:{leg.symbol}" if leg.dex else leg.symbol,
            price=price, mid_price=price, bid_price=price - 0.01, ask_price=price + 0.01,
            price_field=PairSpreadPriceField.MID_PRICE, timestamp=NOW,
            funding_rate_pct=0.01, open_interest_usdt=price * 1000,
        )


async def test_historical_query_reports_expired_funding_and_oi_without_losing_klines(monkeypatch):
    tick, _, _ = install_caches(monkeypatch)

    class HistoricalService(OfflineService):
        async def _fetch_klines(self, exchange, symbol, start, end, interval_minutes):
            return [PairSpreadKlinePoint(bucket_at=end - timedelta(minutes=offset), close=100)
                    for offset in (2, 1)]

        async def _fetch_funding_history(self, exchange, symbol, start, end):
            return []

        async def _fetch_open_interest_history(self, *args, **kwargs):
            return {}

    service = HistoricalService()
    try:
        first = await pair_result(service, interval=60, exchange="okx")
        tick[0] += REALTIME_CACHE_IDLE_TTL_SECONDS + 1
        restarted = await pair_result(
            service, interval=60, exchange="okx", now=NOW + timedelta(minutes=1),
        )
        assert query.REALTIME_CACHE_RESTART_WARNING not in first.warnings
        assert restarted.warnings.count(query.REALTIME_CACHE_RESTART_WARNING) == 1
        assert first.point_count == restarted.point_count == 2
        assert len(restarted.realtime_funding) == 1
        assert restarted.realtime_funding[0].bucket_at == NOW + timedelta(minutes=1)
        assert len(restarted.open_interest) == 1
        assert restarted.open_interest[0].bucket_at == NOW + timedelta(minutes=1)
    finally:
        await service.aclose()


async def pair_result(service, *, now=NOW, dex="io", multiplier=1, interval=5,
                      exchange="hyperliquid", symbol="BTC", market_type="future"):
    return await service.query(
        PairSpreadLegQuery(exchange="binance", symbol=symbol),
        PairSpreadLegQuery(exchange=exchange, symbol=symbol, market_type=market_type,
                           dex=dex if exchange == "hyperliquid" else None),
        hours=1, interval_seconds=interval, leg2_multiplier=multiplier, now=now,
    )


async def symbol_result(service, *, now=NOW, dex="io", multiplier=1, interval=5,
                        exchange="hyperliquid"):
    return await service.query_symbol_spreads(
        "BTC", base_exchange="binance", exchanges=["binance", exchange],
        hours=1, interval_seconds=interval, now=now,
        legs_by_exchange={exchange: PairSpreadLegQuery(
            exchange=exchange, symbol="BTC", dex=dex if exchange == "hyperliquid" else None,
        )},
        price_multipliers_by_exchange={exchange: multiplier},
    )


@pytest.mark.parametrize("name", CACHE_NAMES)
@pytest.mark.parametrize("reason", ["keys", "ttl", "points"])
async def test_query_restarts_evicted_series_with_response_warning(monkeypatch, name, reason):
    tick, budget, caches = install_caches(monkeypatch, max_keys=1)
    is_symbol = name == CACHE_NAMES[3]
    request = symbol_result if is_symbol else pair_result
    service = OfflineService()
    try:
        first = await request(service)
        second = await request(service, now=NOW + timedelta(seconds=5))
        assert second.point_count == 2
        assert query.REALTIME_CACHE_RESTART_WARNING not in second.warnings
        if reason == "ttl":
            tick[0] += REALTIME_CACHE_IDLE_TTL_SECONDS + 1
        else:
            # With max_keys=1 this evicts only the selected series class.
            if reason == "keys":
                caches[name]["other-market"] = {} if is_symbol else []
            else:
                # A different cache exhausts the shared budget, evicting all
                # older series, without changing any instance's key cap.
                other = RealtimeSeriesCache(budget)
                other["pressure"] = [None] * REALTIME_CACHE_MAX_TOTAL_POINTS
        restarted = await request(service, now=NOW + timedelta(seconds=10))
        assert restarted.warnings.count(query.REALTIME_CACHE_RESTART_WARNING) == 1
        if is_symbol:
            points = restarted.series[0].points
        else:
            points = getattr(restarted, {
                CACHE_NAMES[0]: "points", CACHE_NAMES[1]: "realtime_funding",
                CACHE_NAMES[2]: "open_interest",
            }[name])
        assert len(points) == 1
        assert points[0].bucket_at == NOW + timedelta(seconds=10)
        # Copies prevent later mutations/evictions from rewriting old responses.
        assert first.point_count == 1
        assert second.point_count == 2
        assert budget.total_points <= REALTIME_CACHE_MAX_TOTAL_POINTS
    finally:
        await service.aclose()


@pytest.mark.parametrize("mode", ["pair", "symbol"])
@pytest.mark.parametrize("variant", [
    {"dex": "xyz"}, {"multiplier": 10}, {"interval": 10}, {"exchange": "okx"},
])
async def test_query_cache_identity_does_not_mix_dex_multiplier_interval_exchange(
    monkeypatch, mode, variant,
):
    install_caches(monkeypatch)
    request = pair_result if mode == "pair" else symbol_result
    service = OfflineService()
    try:
        first = await request(service)
        different = await request(service, now=NOW + timedelta(seconds=10), **variant)
        resumed = await request(service, now=NOW + timedelta(seconds=20))
        assert first.point_count == different.point_count == 1
        assert resumed.point_count == 2
        points = resumed.points if mode == "pair" else resumed.series[0].points
        assert [point.bucket_at for point in points] == [NOW, NOW + timedelta(seconds=20)]
        assert [point.spread_abs for point in points] == [2, 2]
        if mode == "pair":
            assert len(resumed.realtime_funding) == len(resumed.open_interest) == 2
            assert len(different.realtime_funding) == len(different.open_interest) == 1
    finally:
        await service.aclose()


def test_diagnostics_preserve_fields_without_touching_ttl_or_crossing_cache_graph(monkeypatch):
    tick, budget, caches = install_caches(monkeypatch)
    for name in CACHE_NAMES[:3]:
        caches[name]["private-key"] = [None, None]
    caches[CACHE_NAMES[3]]["private-symbol"] = {"okx": [None], "bybit": [None, None]}
    tick[0] = REALTIME_CACHE_IDLE_TTL_SECONDS + 1
    expected = {name: {"keys": 1, "points": 2} for name in CACHE_NAMES[:3]}
    expected[CACHE_NAMES[3]] = {"keys": 1, "points": 3}
    assert query.realtime_cache_stats() == expected
    targets = [target for target in build_census_targets(SimpleNamespace())
               if target.name.startswith("pair_spread_query.")]
    report = run_census(targets)
    assert not report.get("census_errors")
    assert len(report["cache_census"]) == 4
    assert all(row["entries"] == 1 for row in report["cache_census"])
    assert "private" not in str(report)
    assert budget.total_points == 9  # Diagnostics neither expire nor refresh.
    assert caches[CACHE_NAMES[0]].get("private-key") is None
    assert budget.total_points == 0


def test_production_caches_share_one_budget():
    caches = [getattr(query, name) for name in CACHE_NAMES]
    assert all(cache.budget is query._REALTIME_SERIES_BUDGET for cache in caches)
    assert all(cache.max_keys == 128 and cache.idle_ttl_seconds == 1800 for cache in caches)
    assert query._REALTIME_SERIES_BUDGET.max_points == 200_000
