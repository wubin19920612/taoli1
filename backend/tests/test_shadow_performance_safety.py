"""Offline ownership, single-flight, incremental-time and bounded-write regressions."""
import asyncio
import threading
from datetime import timedelta

import pytest

from app.core.config import Settings, get_settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.services.fake_opportunity import FakeOpportunityScorer, opportunity_keys
from app.services.shadow_ledger import ShadowLedger
from shadow_worker_harness import ShadowRuntime, freeze_snapshot
from app.services.snapshot_consumers import PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_directional_opportunity
from shadow_helpers import NOW, market, pair, empty_baseline


async def wait_started(event):
    async with asyncio.timeout(5):
        while not event.is_set():
            await asyncio.sleep(.001)


@pytest.mark.asyncio
async def test_cancelled_consumer_keeps_guard_and_status_until_worker_finishes():
    runtime = ShadowRuntime(Settings(shadow_enabled=True, database_url="sqlite:///:memory:"), SnapshotStore())
    db = await connect_database(":memory:")
    entered, release = threading.Event(), threading.Event()
    try:
        await initialize_schema(db)
        await runtime.initialize(db)
        empty_baseline(runtime.ledger)
        markets, op = pair()
        runtime.store.mark_published(NOW)
        original_save = runtime.repo.save_batch
        thread_ids = []

        async def slow_save(rows):
            thread_ids.append(threading.get_ident())
            entered.set()
            assert release.wait(5)
            await original_save(rows)

        runtime.repo.save_batch = slow_save
        task = asyncio.create_task(runtime.consume(PublishedSnapshot(1, NOW, tuple(markets), (op,),
            opportunities_version=runtime.store.opportunities_version)))
        await wait_started(entered)
        # Worker has modified ledger but has not published this round's status.
        before = runtime.status()
        assert before["open_count"] == before["pending_writes"] == 0
        before["open_count"] = 99
        assert runtime.status()["open_count"] == 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await runtime.consume(PublishedSnapshot(2, NOW, (), (),
            opportunities_version=runtime.store.opportunities_version))
        assert runtime.status()["skipped_rounds"] == 1
        assert not runtime._active.done()
        closing = asyncio.create_task(runtime.aclose())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
        assert thread_ids and all(i != threading.get_ident() for i in thread_ids)
        assert runtime.status()["open_count"] == 2
        assert runtime.status()["pending_writes"] == 0
        assert await runtime.repo.count_open() == 2
    finally:
        release.set()
        await runtime.aclose()
        await db.close()


@pytest.mark.asyncio
async def test_worker_uses_detached_inputs_and_captured_catalogue():
    runtime = ShadowRuntime(Settings(shadow_enabled=True, database_url="sqlite:///:memory:"), SnapshotStore())
    db = await connect_database(":memory:")
    entered, release = threading.Event(), threading.Event()
    original = runtime._calculate
    seen = []

    def blocked(snapshot, entries):
        seen.append((snapshot, entries, threading.get_ident()))
        entered.set()
        assert release.wait(5)
        return original(snapshot, entries)

    try:
        await initialize_schema(db)
        await runtime.initialize(db)
        empty_baseline(runtime.ledger)
        runtime._calculate = blocked
        markets, op = pair()
        runtime.catalog.entries = {("BINANCE", "FUTURE", "BTC", "")}
        runtime.store.mark_published(NOW)
        task = await runtime.submit(PublishedSnapshot(1, NOW, tuple(markets), (op,),
            opportunities_version=runtime.store.opportunities_version))
        await wait_started(entered)
        markets[0].bid = markets[0].ask = 999
        markets[0].estimated_fields.append("bid")
        op.open_spread_pct = 999
        op.risk_labels.append("changed-after-capture")
        runtime.catalog.entries.clear()
        release.set()
        await task
        rows = await runtime.repo.list_trades()
        assert len(rows) == 2 and all(row["open_spread_pct"] < 1.1 for row in rows)
        shown = runtime.store.get_opportunities()[0]
        assert shown.open_spread_pct < 1.1
        assert "changed-after-capture" not in shown.risk_labels
        assert shown.buy_routes == ["crossex"]
        frozen, entries, worker_id = seen[0]
        assert worker_id != threading.get_ident()
        assert isinstance(entries, frozenset) and len(entries) == 1
        with pytest.raises(TypeError):
            frozen.markets[0] = "mutation"
        with pytest.raises(TypeError):
            frozen.opportunities[0][0] = "mutation"
        # A superseded generation must not publish its presentation models.
        runtime.store.mark_published(NOW)
        sentinel = op.model_copy(update={"fake_score": 123})
        runtime.store.set_opportunities([sentinel])
        await runtime.consume(PublishedSnapshot(1, NOW, (), (),
            opportunities_version=runtime.store.opportunities_version))
        assert runtime.store.get_opportunities()[0] is sentinel
    finally:
        release.set()
        await runtime.aclose()
        await db.close()


def test_capture_cache_detects_in_place_nested_list_and_scalar_changes():
    markets, op = pair()
    snapshot = PublishedSnapshot(1, NOW, tuple(markets), (op,))
    frozen, cache = freeze_snapshot(snapshot)
    same, _ = freeze_snapshot(snapshot, cache)
    assert same.opportunities[0] is frozen.opportunities[0]
    op.risk_labels.append("new")
    op.open_spread_pct = 3
    changed, _ = freeze_snapshot(snapshot, cache)
    assert changed.opportunities[0] is not frozen.opportunities[0]
    assert "new" not in frozen.op_lists[0][2]
    assert "new" in changed.op_lists[0][2]


@pytest.mark.asyncio
async def test_chunks_retry_before_next_generation_and_encode_off_loop(monkeypatch):
    import app.db.repositories as repositories
    runtime = ShadowRuntime(Settings(shadow_enabled=True, database_url="sqlite:///:memory:"), SnapshotStore())
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        await runtime.initialize(db)
        empty_baseline(runtime.ledger)
        markets, ops = [], []
        for i in range(205):
            legs = [market(ex, price, symbol=f"X{i}USDT", raw_symbol=f"X{i}USDT")
                    for ex, price in (("binance", 100), ("bitget", 101))]
            markets.extend(legs)
            ops.append(build_directional_opportunity(*legs, mode="FF"))
        batches, json_threads = [], []
        save, dumps = runtime.repo.save_batch, repositories.json.dumps
        failed = False

        async def fail_second(rows):
            nonlocal failed
            batches.append(len(rows))
            if len(batches) == 2:
                failed = True
                raise RuntimeError("synthetic second chunk failure")
            await save(rows)

        def checked_dumps(*args, **kwargs):
            json_threads.append(threading.get_ident())
            return dumps(*args, **kwargs)

        monkeypatch.setattr(repositories.json, "dumps", checked_dumps)
        runtime.repo.save_batch = fail_second
        await runtime.consume(PublishedSnapshot(1, NOW, tuple(markets), tuple(ops),
            opportunities_version=runtime.store.opportunities_version))
        assert failed and batches == [200, 200]
        assert runtime.status()["pending_writes"] == 210
        assert await runtime.repo.count_open() == 200
        # The failed remainder must persist before a closing generation is applied.
        now = NOW + timedelta(seconds=1)
        for quote in markets:
            quote.timestamp, quote.bid, quote.ask = now, 100, 100
        await runtime.consume(PublishedSnapshot(2, now, tuple(markets), (),
            opportunities_version=runtime.store.opportunities_version))
        assert batches == [200, 200, 200, 10, 200, 200, 10]
        assert runtime.status()["pending_writes"] == runtime.status()["open_count"] == 0
        assert len(await runtime.repo.list_trades(limit=500, status="closed")) == 410
        assert json_threads and all(i != threading.get_ident() for i in json_threads)
    finally:
        await runtime.aclose()
        await db.close()


def test_incremental_ledger_handles_funding_stale_and_checkpoint_without_new_quotes():
    ledger = ShadowLedger(Settings(shadow_enabled=True))
    empty_baseline(ledger)
    markets, op = pair()
    for quote, rate in zip(markets, (.01, .03)):
        quote.funding_next_time = NOW + timedelta(seconds=5)
        quote.funding_rate_pct = rate
        quote.funding_interval_hours = 1
    ledger.process(markets, [op], NOW)
    ledger.process(markets, [], NOW + timedelta(seconds=1))
    assert ledger.recomputed == 0
    ledger.process(markets, [], NOW + timedelta(seconds=5))
    assert ledger.recomputed == 1
    assert all(row["funding_bps"] == pytest.approx(2) for p in ledger.positions.values() for row in p.rows)
    ledger.process(markets, [], NOW + timedelta(seconds=6))
    assert ledger.recomputed == 0
    ledger.process(markets, [], NOW + timedelta(seconds=61))
    assert len(ledger.pending) == 2  # checkpoint still queues unchanging rows
    ledger.process(markets, [], NOW + timedelta(seconds=600))
    assert ledger.open_count == 0
    assert all(row["exit_reason"] == "stale" for row in ledger.pending.values())
    assert not ledger.by_market and not ledger.deadlines


def test_market_index_uses_exact_dex_and_timers_stay_bounded():
    ledger = ShadowLedger(Settings(shadow_enabled=True))
    empty_baseline(ledger)
    buy = market("binance")
    xyz = market("hyperliquid", 101, raw_symbol="xyz:BTC", dex="xyz")
    abc = market("hyperliquid", 101, raw_symbol="abc:BTC", dex="abc")
    ops = [build_directional_opportunity(buy, sell, mode="FF") for sell in (xyz, abc)]
    ledger.process([buy, xyz, abc], ops, NOW)
    for i in range(1, 101):
        xyz.timestamp = NOW + timedelta(milliseconds=i)
        ledger.process([buy, xyz, abc], [], xyz.timestamp)
        assert ledger.recomputed == 1
    assert len(ledger.timers) <= 64
    assert opportunity_keys(ops[0])[1] != opportunity_keys(ops[1])[1]


def test_minute_history_only_for_large_spreads_and_fresh_market_updates():
    scorer = FakeOpportunityScorer()
    markets, op = pair(open_spread_pct=.5)
    scorer.score(markets, [op], NOW)
    states = list(scorer.prices.values())
    scorer.score(markets, [op], NOW + timedelta(seconds=1))
    assert all(s.seen == NOW.timestamp() for s in states)
    assert not scorer.spreads
    op.open_spread_pct = 1
    scorer.score(markets, [op], NOW + timedelta(seconds=2))
    assert len(scorer.spreads[op.id]) == 1
    op.open_spread_pct = .99
    scorer.score(markets, [op], NOW + timedelta(seconds=3))
    assert not scorer.spreads
    scorer.score(markets, [op], NOW + timedelta(seconds=11))
    assert not scorer.groups and not scorer.assets


def test_default_open_limit_is_1500(monkeypatch):
    monkeypatch.delenv("SHADOW_MAX_OPEN", raising=False)
    assert Settings().shadow_max_open == 1500
    assert get_settings().shadow_max_open == 1500
