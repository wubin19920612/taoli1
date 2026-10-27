"""Offline cache contracts and read-only reproductions for M2-1."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.models.hyperliquid_trade_status import (
    HyperliquidMarketTradeStatus,
    HyperliquidTradeActionStatus,
    HyperliquidTradeStatusResult,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.trade_availability import TradeAvailabilityWatch
from app.services import trade_availability as module
from app.services.snapshot_store import SnapshotStore
from app.services.trade_availability import TradeAvailabilityService


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 10, 25, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            assert tz == UTC
            return cls.current

        @classmethod
        def advance(cls, seconds):
            cls.current += timedelta(seconds=seconds)

    monkeypatch.setattr(module, "datetime", Clock)
    return Clock


@pytest.fixture
async def cache_factory(clock):
    clients = []

    def make(handler=None, **options):
        calls = []

        def transport(request):
            calls.append(str(request.url))
            if handler is not None:
                return handler(request)
            return httpx.Response(200, json={"request": len(calls)})

        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        clients.append(client)
        service = TradeAvailabilityService(SnapshotStore(), [], None, client, **options)
        return service, calls

    yield make
    for client in clients:
        await client.aclose()


async def test_hit_returns_same_object_without_request(cache_factory, clock):
    service, calls = cache_factory()
    first = await service._get_json("https://cache.invalid/a")
    clock.advance(19.999)
    assert await service._get_json("https://cache.invalid/a") is first
    assert len(calls) == 1


async def test_ttl_boundary_refetches(cache_factory, clock):
    service, calls = cache_factory()
    first = await service._get_json("https://cache.invalid/a")
    clock.advance(20)
    assert await service._get_json("https://cache.invalid/a") is not first
    assert len(calls) == 2


async def test_next_write_prunes_all_expired_entries(cache_factory, clock):
    service, _ = cache_factory()
    for key in ("a", "b", "c"):
        await service._get_json(f"https://cache.invalid/{key}")
    clock.advance(119)
    await service._get_json("https://cache.invalid/live")
    # LRU order and expiration order differ after this hit.
    await service._get_json("https://cache.invalid/a", cache_seconds=120)
    assert service.cache_stats()["entries"] == 4
    clock.advance(1)
    await service._get_json("https://cache.invalid/new")
    assert service.cache_stats()["entries"] == 2
    assert set(service._cache) == {"https://cache.invalid/live", "https://cache.invalid/new"}


@pytest.mark.parametrize("reads", [
    [(0, 20), (30, 60), (0, 20), (19, 20), (1, 20)],
    [(0, 60), (20, 20), (30, 60), (30, 60)],
    [(0, 0), (0, 0), (0, -1), (0, 20)],
    [(0, 20), (121, 180)],  # Retention expiry alone is not a read TTL.
])
async def test_read_ttl_matches_original_implementation(cache_factory, clock, reads):
    service, calls = cache_factory()
    stored_at = None
    expected_requests = 0
    previous = None
    for advance, ttl in reads:
        clock.advance(advance)
        # Reference predicate copied from the original two-tuple cache.
        hit = stored_at is not None and clock.now(UTC) - stored_at < timedelta(seconds=ttl)
        if not hit:
            stored_at = clock.now(UTC)
            expected_requests += 1
        result = await service._get_json("https://cache.invalid/a", cache_seconds=ttl)
        assert len(calls) == expected_requests
        assert (result is previous) == hit
        previous = result


async def test_retention_floor_preserves_longer_reader_after_other_write(cache_factory, clock):
    service, _ = cache_factory()
    first = await service._get_json("https://cache.invalid/a", cache_seconds=20)
    clock.advance(30)
    await service._get_json("https://cache.invalid/b")
    assert await service._get_json("https://cache.invalid/a", cache_seconds=60) is first


async def test_retention_uses_larger_of_write_ttl_and_configured_floor(cache_factory, clock):
    service, _ = cache_factory(retention_seconds=10)
    short = await service._get_json("https://cache.invalid/short", cache_seconds=2)
    long = await service._get_json("https://cache.invalid/long", cache_seconds=30)
    clock.advance(9)
    await service._get_json("https://cache.invalid/other")
    assert await service._get_json("https://cache.invalid/short", cache_seconds=10) is short
    clock.advance(1)
    await service._get_json("https://cache.invalid/new")
    assert "https://cache.invalid/short" not in service._cache
    assert await service._get_json("https://cache.invalid/long", cache_seconds=30) is long
    clock.advance(20)
    await service._get_json("https://cache.invalid/final")
    assert set(service._cache) == {"https://cache.invalid/final"}


async def test_lru_hit_protects_recent_entry(cache_factory):
    service, calls = cache_factory(max_entries=2)
    first = await service._get_json("https://cache.invalid/a")
    second = await service._get_json("https://cache.invalid/b")
    assert await service._get_json("https://cache.invalid/a") is first
    await service._get_json("https://cache.invalid/c")
    assert service.cache_stats()["entries"] == 2
    assert await service._get_json("https://cache.invalid/a") is first
    assert await service._get_json("https://cache.invalid/b") is not second
    assert len(calls) == 4


async def test_refreshed_entry_is_most_recent(cache_factory, clock):
    service, _ = cache_factory(max_entries=2)
    await service._get_json("https://cache.invalid/a")
    clock.advance(10)
    await service._get_json("https://cache.invalid/b")
    clock.advance(10)
    await service._get_json("https://cache.invalid/a")
    await service._get_json("https://cache.invalid/c")
    assert set(service._cache) == {"https://cache.invalid/a", "https://cache.invalid/c"}


async def test_default_capacity_is_128(cache_factory):
    service, _ = cache_factory()
    for index in range(129):
        await service._get_json(f"https://cache.invalid/{index}")
    assert service.cache_stats()["entries"] == 128
    assert "https://cache.invalid/0" not in service._cache


async def test_zero_capacity_does_not_retain_payload(cache_factory):
    service, calls = cache_factory(max_entries=0)
    await service._get_json("https://cache.invalid/a")
    await service._get_json("https://cache.invalid/a")
    assert len(calls) == 2
    assert service.cache_stats()["entries"] == 0


async def test_stats_only_expose_allowlisted_prefix_counts_and_are_read_only(cache_factory, clock):
    service, _ = cache_factory()
    keys = [None, "binance:spot:BTCUSDT", "binance:future:ETHUSDT", "okx:BTC-USDT",
            "BTCUSDT:private", "opaque-secret", "https://private.invalid/token?coin=BTC"]
    for index, key in enumerate(keys):
        await service._get_json(f"https://cache.invalid/{index}?coin=BTCUSDT", cache_key=key)
    before = list(service._cache.items())
    clock.advance(120)
    stats = service.cache_stats()
    assert stats == {
        "entries": 7,
        "keys_sample": [
            {"prefix": "binance:", "count": 2},
            {"prefix": "okx:", "count": 1},
            {"prefix": "other", "count": 2},
            {"prefix": "url", "count": 2},
        ],
    }
    for secret in ("BTC", "ETH", "http", "invalid", "private", "secret"):
        assert secret not in json.dumps(stats)
    assert list(service._cache.items()) == before
    stats["keys_sample"].clear()
    assert service.cache_stats()["keys_sample"]


@pytest.mark.parametrize("failure", ["http", "transport", "json"])
async def test_retry_success_only_caches_success(cache_factory, monkeypatch, failure):
    attempts = 0
    sleeps = []

    async def sleep(delay):
        assert service.cache_stats()["entries"] == 0
        assert not service._cache_lock.locked()
        sleeps.append(delay)

    def handler(request):
        nonlocal attempts
        attempts += 1
        assert not service._cache_lock.locked()
        if attempts == 1:
            if failure == "transport":
                raise httpx.ConnectError("offline failure", request=request)
            if failure == "json":
                return httpx.Response(200, content=b"not json")
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    service, calls = cache_factory(handler)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    result = await service._get_json("https://cache.invalid/a")
    assert await service._get_json("https://cache.invalid/a") is result
    assert len(calls) == 2
    assert sleeps == [0.2]
    assert service.cache_stats()["entries"] == 1


async def test_failed_refresh_does_not_replace_previous_payload(cache_factory, clock, monkeypatch):
    failing = False

    def handler(request):
        return httpx.Response(503) if failing else httpx.Response(200, json={"ok": True})

    async def sleep(delay):
        pass

    service, calls = cache_factory(handler)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    first = await service._get_json("https://cache.invalid/a")
    clock.advance(20)
    failing = True
    with pytest.raises(RuntimeError, match="HTTPStatusError") as error:
        await service._get_json("https://cache.invalid/a")
    assert isinstance(error.value.__cause__, httpx.HTTPStatusError)
    assert len(calls) == 3
    assert await service._get_json("https://cache.invalid/a", cache_seconds=60) is first


async def test_slow_request_does_not_hold_cache_lock(clock):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        assert not service._cache_lock.locked()
        if request.url.path == "/slow":
            entered.set()
            await release.wait()
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = TradeAvailabilityService(SnapshotStore(), [], None, client)
        first = await service._get_json("https://cache.invalid/cached")
        slow = asyncio.create_task(service._get_json("https://cache.invalid/slow"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert await asyncio.wait_for(
                service._get_json("https://cache.invalid/cached"), timeout=2,
            ) is first
            clock.advance(30)
        finally:
            release.set()
            result = await asyncio.wait_for(slow, timeout=2)
        # TTL starts on successful response, not request start.
        assert await service._get_json("https://cache.invalid/slow") is result


@pytest.mark.parametrize("case,expected_markets,expected_error", [
    ("baseline", 1, False),
    ("missing_snapshot", 0, True),
    ("alias_changed", 0, True),
    ("alias_current", 1, False),
    ("raw_case", 1, False),
    ("raw_whitespace", 1, True),
    ("wrong_raw", 0, True),
    ("exchange_case", 1, True),
    ("wrong_exchange", 0, True),
    ("wrong_market_type", 0, True),
    ("empty_dex", 1, False),
    ("hl_main_none", 1, True),
    ("hl_main_empty", 1, True),
    ("hl_main_explicit", 1, False),
    ("hl_dex_case", 1, True),
    ("hl_dex_explicit", 1, False),
    ("hl_wrong_dex", 0, True),
    ("hl_upstream_missing", 0, True),
    ("hl_delisted_present", 1, False),
    ("public_metadata_missing", 1, False),
])
async def test_runtime_error_read_only_reproduction(
    cache_factory, clock, caplog, case, expected_markets, expected_error,
):
    """Exercise real matching, diagnosis and monitor; replace only upstream data/storage."""
    is_hl = case.startswith("hl_")
    is_builder = case in {"hl_dex_case", "hl_dex_explicit", "hl_wrong_dex"}
    raw = "xyz:BTC" if is_builder else "BTC" if is_hl else "BTCUSDT"
    snapshot = MarketSnapshot(
        symbol="BTCUSDT", base="BTC", exchange="hyperliquid" if is_hl else "aster",
        market_type=MarketType.FUTURE, raw_symbol=raw, bid=100, ask=101,
        timestamp=clock.now(UTC), dex="xyz" if is_builder else None,
    )
    watch = TradeAvailabilityWatch(
        symbol="BTCUSDT", exchange=snapshot.exchange, market_type=MarketType.FUTURE,
        raw_symbol=raw, dex="xyz" if is_builder else "main" if is_hl else None,
        created_at=clock.now(UTC), updated_at=clock.now(UTC),
    )
    if case in {"alias_changed", "alias_current"}:
        snapshot = snapshot.model_copy(update={
            "symbol": "XBTUSDT", "symbol_alias_original_symbol": "BTCUSDT",
            "symbol_alias_price_multiplier": 1000,
        })
        if case == "alias_current":
            watch = watch.model_copy(update={"symbol": "XBTUSDT"})
    updates = {
        "raw_case": {"raw_symbol": "btcusdt"},
        "raw_whitespace": {"raw_symbol": " BTCUSDT "},
        "wrong_raw": {"raw_symbol": "1000BTCUSDT"},
        "exchange_case": {"exchange": "ASTER"},
        "wrong_exchange": {"exchange": "binance"},
        "wrong_market_type": {"market_type": MarketType.SPOT},
        "empty_dex": {"dex": ""},
        "hl_main_none": {"dex": None},
        "hl_main_empty": {"dex": ""},
        "hl_dex_case": {"dex": "XYZ"},
        "hl_wrong_dex": {"dex": "other"},
    }
    watch = watch.model_copy(update=updates.get(case, {}))

    def metadata(request):
        symbols = [] if case == "public_metadata_missing" else [
            {"symbol": raw, "status": "TRADING"},
        ]
        return httpx.Response(200, json={"symbols": symbols})

    service, _ = cache_factory(metadata)
    service.store.set_all_markets([] if case == "missing_snapshot" else [snapshot])

    async def hyperliquid_status(symbol, **kwargs):
        action = HyperliquidTradeActionStatus(
            state="unknown", reason_code="FAKE", reason="offline fixture",
        )
        market = HyperliquidMarketTradeStatus(
            symbol="BTCUSDT", dex="xyz" if is_builder else "main", raw_symbol=raw,
            observed_at=clock.now(UTC), buy_open=action, sell_open=action,
            buy_reduce_only=action, sell_reduce_only=action,
            is_delisted=case == "hl_delisted_present",
        )
        return HyperliquidTradeStatusResult(
            query=symbol, observed_at=clock.now(UTC),
            markets=[] if case == "hl_upstream_missing" else [market],
        )

    service.hyperliquid_service = SimpleNamespace(fetch_status=hyperliquid_status)
    result = await service.fetch_status(
        watch.symbol, exchange=watch.exchange, market_type=watch.market_type,
        raw_symbol=watch.raw_symbol, dex=watch.dex,
    )
    assert len(result.markets) == expected_markets
    if case == "hl_upstream_missing":
        assert result.errors == {
            "hyperliquid:future:main:BTC":
                "RuntimeError: Hyperliquid 公开接口未返回指定 DEX 原始市场",
        }
    if case == "public_metadata_missing":
        assert result.markets[0].public_status_code == "NOT_FOUND"
    if case == "hl_delisted_present":
        assert result.markets[0].public_status_code == "DELISTED"

    saved = []

    async def list_watches():
        return [watch]

    async def save(updated):
        saved.append(updated)
        return updated

    repository = SimpleNamespace(list=list_watches, upsert=save)
    monitor = module.TradeAvailabilityMonitor(repository, service)
    assert await monitor.check_once() == []
    assert len(saved) == 1
    # The monitor may append "；诊断：<result.errors>" after the generic reason.
    if expected_error:
        assert saved[0].last_error.startswith("RuntimeError: 聚合行情中未找到指定原始市场")
        assert any(
            record.exc_info and str(record.exc_info[1]).startswith("聚合行情中未找到指定原始市场")
            for record in caplog.records
        )
    else:
        assert saved[0].last_error is None
