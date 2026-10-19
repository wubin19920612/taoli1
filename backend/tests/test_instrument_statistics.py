import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models.instrument import InstrumentLookupResult, InstrumentMarketCandidate
from app.models.market import MarketType
from app.models.pair_spread import PairSpreadKlinePoint
from app.services.instrument_spreads import build_instrument_spreads, instrument_market_id
from app.services.instrument_statistics import InstrumentStatisticsService
from app.services.pair_spread_query import PairSpreadQueryService, PartialKlineHistoryError

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


def market(exchange: str, **updates) -> InstrumentMarketCandidate:
    values = {
        "symbol": "BTCUSDT", "base": "BTC", "exchange": exchange,
        "market_type": MarketType.FUTURE, "bid": 100, "ask": 101, "timestamp": NOW,
        "raw_symbol": "BTCUSDT", "data_status": "live", "age_seconds": 0,
        "stale_after_seconds": 30, "error": None,
    }
    values.update(updates)
    return InstrumentMarketCandidate(**values)


def instrument(markets: list[InstrumentMarketCandidate]) -> InstrumentLookupResult:
    return InstrumentLookupResult(
        query="BTC", symbol="BTCUSDT", base="BTC", markets=markets,
        spreads=build_instrument_spreads(markets, now=NOW),
    )


def point(minutes_ago: int, close: float) -> PairSpreadKlinePoint:
    return PairSpreadKlinePoint(bucket_at=NOW - timedelta(minutes=minutes_ago), close=close)


@pytest.mark.asyncio
async def test_changes_and_directional_max_use_only_aligned_complete_candles() -> None:
    first, second = market("binance"), market("okx")
    first_points = [point(1441, 50), point(61, 80), point(2, 100), point(1, 110), point(0, 9999)]
    second_points = [point(1441, 50), point(61, 80), point(2, 120), point(1, 99), point(3, 999)]
    query = AsyncMock()
    query.fetch_market_klines.side_effect = [first_points, second_points]
    service = InstrumentStatisticsService(query)
    result = await service.lookup(instrument([first, second]), now=NOW)

    first_id, second_id = instrument_market_id(first), instrument_market_id(second)
    changes = result.markets[first_id]
    assert changes.change_1h_pct == pytest.approx(37.5)
    assert changes.change_24h_pct == pytest.approx(120)
    assert changes.observed_at == NOW
    forward = result.spreads[f"{first_id}->{second_id}"]
    reverse = result.spreads[f"{second_id}->{first_id}"]
    assert forward.max_spread_pct == pytest.approx(2000 / 110)
    assert reverse.max_spread_pct == pytest.approx(2200 / 209)
    assert forward.min_spread_pct == pytest.approx(-2200 / 209)
    assert reverse.min_spread_pct == pytest.approx(-2000 / 110)
    assert forward.max_at == NOW - timedelta(minutes=1)
    assert forward.min_at == NOW
    assert forward.point_count == 3
    assert forward.complete is False
    assert forward.is_estimated is True
    assert query.fetch_market_klines.await_args_list[0].kwargs["end"] < NOW


@pytest.mark.asyncio
async def test_full_day_coverage_and_negative_max_are_not_absolute_values() -> None:
    first, second = market("binance"), market("gate")
    query = AsyncMock()
    query.fetch_market_klines.side_effect = [
        [point(minutes, 100) for minutes in range(1, 1442)],
        [point(minutes, 90) for minutes in range(1, 1442)],
    ]
    result = await InstrumentStatisticsService(query).lookup(instrument([first, second]), now=NOW)
    history = result.spreads[f"{instrument_market_id(first)}->{instrument_market_id(second)}"]
    assert history.max_spread_pct == pytest.approx(-2000 / 190)
    assert history.min_spread_pct == pytest.approx(-2000 / 190)
    assert history.point_count == 1440
    assert history.complete is True
    assert history.first_seen_at == NOW - timedelta(hours=24) + timedelta(minutes=1)
    assert history.last_seen_at == NOW


@pytest.mark.asyncio
async def test_missing_and_stale_baselines_do_not_become_zero_changes() -> None:
    first, second = market("binance"), market("gate")
    query = AsyncMock()
    query.fetch_market_klines.side_effect = [[point(1, 100)], [point(10, 99), point(70, 90)]]
    result = await InstrumentStatisticsService(query).lookup(instrument([first, second]), now=NOW)
    assert result.markets[instrument_market_id(first)].change_1h_pct is None
    assert result.markets[instrument_market_id(first)].change_24h_pct is None
    assert result.markets[instrument_market_id(second)].change_1h_pct is None
    assert "已过期" in result.markets[instrument_market_id(second)].error
    assert all(stats.max_spread_pct is None and stats.min_spread_pct is None for stats in result.spreads.values())


@pytest.mark.asyncio
async def test_alias_multiplier_dex_and_contract_size_are_isolated_and_cached() -> None:
    first = market("hyperliquid", raw_symbol="xyz:BTC", dex="xyz", symbol_alias_price_multiplier=0.01)
    second = market("hyperliquid", raw_symbol="cash:BTC", dex="cash", contract_size_multiplier=10)
    query = AsyncMock()
    query.fetch_market_klines.side_effect = [[point(1, 10000)], [point(1, 100)]]
    service = InstrumentStatisticsService(query)
    lookup = instrument([first, second])
    result = await service.lookup(lookup, now=NOW)
    assert len(result.markets) == 2
    assert all(stats.max_spread_pct == 0 for stats in result.spreads.values())
    await service.lookup(lookup, now=NOW + timedelta(minutes=1))
    assert query.fetch_market_klines.await_count == 2
    query.fetch_market_klines.side_effect = [[point(1, 10000)], [point(1, 100)]]
    await service.lookup(lookup, now=NOW + timedelta(minutes=2))
    assert query.fetch_market_klines.await_count == 4


@pytest.mark.asyncio
async def test_concurrent_queries_share_fetch_and_one_market_error_does_not_break_others() -> None:
    first, second = market("binance"), market("gate")
    gate = asyncio.Event()

    async def fetch(target, **kwargs):
        await gate.wait()
        if target.exchange == "gate":
            raise RuntimeError("upstream unavailable")
        return [point(1, 100), point(61, 90)]

    query = AsyncMock()
    query.fetch_market_klines.side_effect = fetch
    service = InstrumentStatisticsService(query)
    lookup = instrument([first, second])
    tasks = [asyncio.create_task(service.lookup(lookup, now=NOW)) for _ in range(2)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert query.fetch_market_klines.await_count == 2
    assert results[0].markets[instrument_market_id(first)].change_1h_pct == pytest.approx(100 / 9)
    assert results[0].markets[instrument_market_id(second)].error == "upstream unavailable"
    assert all(stats.max_spread_pct is None and stats.min_spread_pct is None for stats in results[0].spreads.values())


@pytest.mark.asyncio
async def test_hyperliquid_history_sends_exact_raw_coin_without_ticker_resolution() -> None:
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=[{"t": int(point(1, 100).bucket_at.timestamp() * 1000), "c": "100"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        query = PairSpreadQueryService(client)
        target = market("hyperliquid", raw_symbol="xyz:BTC", dex="xyz")
        points = await query.fetch_market_klines(target, start=NOW - timedelta(hours=24), end=NOW)
        assert points[0].close == 100
        assert requests[0]["req"]["coin"] == "xyz:BTC"
        spot = target.model_copy(update={"market_type": MarketType.SPOT, "raw_symbol": "@707"})
        await query.fetch_market_klines(spot, start=NOW - timedelta(hours=24), end=NOW)
        assert requests[1]["req"]["coin"] == "@707"
        assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("exchange, raw_symbol", [("okx", "1000PEPE-USDT-SWAP"), ("binance", "1000PEPEUSDT"), ("rh-lighter", "1000PEPE")])
async def test_history_uses_original_market_not_canonical_alias(exchange, raw_symbol) -> None:
    query = PairSpreadQueryService()
    query._fetch_klines = AsyncMock(return_value=[])
    try:
        target = market(exchange, raw_symbol=raw_symbol, symbol="PEPEUSDT", symbol_alias_price_multiplier=0.001)
        await query.fetch_market_klines(target, start=NOW - timedelta(hours=24), end=NOW)
        assert query._fetch_klines.await_args.args[1] in {raw_symbol, "1000PEPEUSDT"}
    finally:
        await query.aclose()


@pytest.mark.asyncio
async def test_gate_spot_history_paginates_without_exceeding_inclusive_1000_point_limit() -> None:
    counts = []

    def handler(request):
        start, end = int(request.url.params["from"]), int(request.url.params["to"])
        count = (end - start) // 60 + 1
        counts.append(count)
        if count > 1000:
            return httpx.Response(400, json={"message": "Candlestick range too broad"})
        return httpx.Response(200, json=[
            [str(timestamp), "1000", "100", "100", "100", "100", "10", "true"]
            for timestamp in range(start, end + 1, 60)
        ])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        query = PairSpreadQueryService(client)
        points = await query.fetch_market_klines(
            market("gate", market_type=MarketType.SPOT, raw_symbol="BTC_USDT"),
            start=NOW - timedelta(hours=24, minutes=3), end=NOW - timedelta(milliseconds=1),
        )
    assert counts == [1000, 443]
    assert len(points) == 1443
    assert points[-1].bucket_at == NOW - timedelta(minutes=1)


@pytest.mark.asyncio
async def test_incremental_refresh_keeps_full_window_and_fetches_only_recent_minutes():
    requested = []

    async def fetch(target, *, start, end):
        requested.append((start, end))
        return [PairSpreadKlinePoint(bucket_at=start + timedelta(minutes=i), close=100)
                for i in range(int((end - start).total_seconds() // 60) + 1)]

    query = AsyncMock()
    query.fetch_market_klines.side_effect = fetch
    service = InstrumentStatisticsService(query)
    lookup = instrument([market("binance"), market("okx")])
    await service.lookup(lookup, now=NOW)
    requested.clear()
    result = await service.lookup(lookup, now=NOW + timedelta(minutes=2))
    assert len(requested) == 2
    assert all(end - start < timedelta(minutes=5) for start, end in requested)
    assert all(stats.complete and stats.point_count == 1440 for stats in result.spreads.values())
    assert all(min(prices) >= NOW - timedelta(hours=24, minutes=1)
               for _, prices, _ in service._cache.values())


@pytest.mark.asyncio
async def test_failed_refresh_preserves_valid_window_and_surfaces_warning_until_recovery():
    fail = False

    async def fetch(target, *, start, end):
        if fail and target.exchange == "okx":
            raise RuntimeError("OKX 历史行情接口限流")
        return [PairSpreadKlinePoint(bucket_at=start + timedelta(minutes=i), close=100)
                for i in range(int((end - start).total_seconds() // 60) + 1)]

    query = AsyncMock()
    query.fetch_market_klines.side_effect = fetch
    service = InstrumentStatisticsService(query)
    lookup = instrument([market("binance"), market("okx")])
    await service.lookup(lookup, now=NOW)
    fail = True
    result = await service.lookup(lookup, now=NOW + timedelta(minutes=2))
    for stats in result.spreads.values():
        assert stats.point_count == 1438
        assert stats.max_spread_pct == 0
        assert not stats.complete
        assert stats.last_seen_at == NOW
        assert "限流" in stats.error
    fail = False
    result = await service.lookup(lookup, now=NOW + timedelta(minutes=4))
    assert all(stats.complete and stats.error is None for stats in result.spreads.values())


@pytest.mark.asyncio
async def test_partial_cold_load_resumes_missing_prefix_without_redownloading_valid_pages():
    requested = []
    fail = True

    async def fetch(target, *, start, end):
        requested.append((target.exchange, start, end))
        points = [PairSpreadKlinePoint(bucket_at=start + timedelta(minutes=i), close=100)
                  for i in range(int((end - start).total_seconds() // 60) + 1)]
        if fail and target.exchange == "okx":
            raise PartialKlineHistoryError("OKX 历史行情接口限流", points[-100:],
                                           points[-100].bucket_at, end + timedelta(milliseconds=1))
        return points

    query = AsyncMock()
    query.fetch_market_klines.side_effect = fetch
    service = InstrumentStatisticsService(query)
    lookup = instrument([market("binance"), market("okx")])
    result = await service.lookup(lookup, now=NOW)
    assert all(stats.point_count == 100 and "限流" in stats.error for stats in result.spreads.values())
    fail = False
    requested.clear()
    result = await service.lookup(lookup, now=NOW + timedelta(minutes=2))
    okx_requests = [(start, end) for exchange, start, end in requested if exchange == "okx"]
    assert len(okx_requests) == 2
    assert okx_requests[0][0] >= NOW - timedelta(minutes=3)
    assert okx_requests[1][1] < NOW - timedelta(minutes=100)
    assert all(stats.complete and stats.error is None for stats in result.spreads.values())


@pytest.mark.asyncio
async def test_failed_history_expires_and_cannot_cross_binding_revisions():
    query = AsyncMock()
    query.fetch_market_klines.return_value = [point(1, 100)]
    service = InstrumentStatisticsService(query)
    targets = [market("binance"), market("okx", binding_revision=1)]
    await service.lookup(instrument(targets), now=NOW)
    query.fetch_market_klines.side_effect = RuntimeError("HTTP 429 Too Many Requests")
    changed = targets[1].model_copy(update={"binding_revision": 2})
    result = await service.lookup(instrument([targets[0], changed]), now=NOW)
    assert all(stats.point_count == 0 for stats in result.spreads.values())
    result = await service.lookup(instrument(targets), now=NOW + timedelta(hours=25))
    assert all(stats.point_count == 0 and stats.max_spread_pct is None for stats in result.spreads.values())


@pytest.mark.asyncio
async def test_partial_refresh_after_idle_period_repairs_middle_gap_before_claiming_complete():
    phase = "cold"
    requested = []

    async def fetch(target, *, start, end):
        requested.append((target.exchange, start, end))
        points = [PairSpreadKlinePoint(bucket_at=start + timedelta(minutes=i), close=100)
                  for i in range(int((end - start).total_seconds() // 60) + 1)]
        if phase == "partial" and target.exchange == "okx":
            raise PartialKlineHistoryError("OKX 历史行情接口限流", points[-100:],
                                           points[-100].bucket_at, end + timedelta(milliseconds=1))
        return points

    query = AsyncMock()
    query.fetch_market_klines.side_effect = fetch
    service = InstrumentStatisticsService(query)
    lookup = instrument([market("binance"), market("okx")])
    await service.lookup(lookup, now=NOW)
    phase = "partial"
    result = await service.lookup(lookup, now=NOW + timedelta(hours=2))
    assert all(stats.point_count == 1420 and not stats.complete for stats in result.spreads.values())
    phase = "recovery"
    requested.clear()
    result = await service.lookup(lookup, now=NOW + timedelta(hours=2, minutes=2))
    okx_ranges = [(start, end) for exchange, start, end in requested if exchange == "okx"]
    assert len(okx_ranges) == 2
    assert okx_ranges[1] == (NOW, NOW + timedelta(minutes=20) - timedelta(milliseconds=1))
    assert all(stats.complete and stats.error is None for stats in result.spreads.values())
