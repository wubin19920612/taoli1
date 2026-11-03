from datetime import timedelta

import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.db.repositories import ShadowTradeRepository
from app.db.schema import initialize_schema, initialize_shadow_schema
from app.services.shadow_ledger import ShadowLedger, liquidity_tier, spread_tier
from app.services.spread_engine import midpoint_spread_pct
from shadow_helpers import NOW, pair


def ledger(**kwargs):
    return ShadowLedger(Settings(shadow_enabled=True, **kwargs))


def fresh(markets, now, *, sell_price=None):
    result = [m.model_copy(update={"timestamp": now}) for m in markets]
    if sell_price is not None:
        result[1] = result[1].model_copy(update={"bid": sell_price, "ask": sell_price})
    return result


@pytest.mark.parametrize("case", ["low", "old", "future", "estimated", "not_book", "missing", "multiplier", "disabled"])
def test_open_gates(case):
    book = ledger()
    markets, op = pair()
    if case == "low":
        op.open_spread_pct = 0.199
    if case == "old":
        markets[0].timestamp = NOW - timedelta(seconds=10.001)
    if case == "future":
        markets[0].timestamp = NOW + timedelta(seconds=1)
    if case == "estimated":
        markets[0].estimated_fields = ["bid"]
    if case == "not_book":
        markets[0].quote_kind = "reference"
    if case == "missing":
        markets = markets[:1]
    if case == "multiplier":
        markets[0].symbol_alias_price_multiplier = 1000
    if case == "disabled":
        book.enabled = False
    book.process(markets, [op], NOW)
    assert not book.positions


def test_open_boundaries_duplicates_fake_non_executable_and_cap():
    book = ledger(shadow_max_open=2)
    markets, op = pair(open_spread_pct=0.2, fake_flag=True)
    markets[0].timestamp = NOW - timedelta(seconds=10)
    book.process(markets, [op, op], NOW)
    assert book.open_count == 2
    rows = list(book.pending.values())
    assert all(row["fake_flag"] and not row["executable"] for row in rows)
    assert rows[0]["buy_market_key"]["raw_symbol"] == "BTCUSDT"
    book.process(markets, [op.model_copy(update={"id": "other"})], NOW)
    assert book.dropped == 1 and book.open_count == 2


def test_variants_close_independently_with_current_books_and_correct_sign():
    book = ledger()
    markets, op = pair()
    book.process(markets, [op], NOW)
    now = NOW + timedelta(seconds=20)
    books = fresh(markets, now, sell_price=100.4)
    book.process(books, [], now)  # Not an opportunity anymore, still follows books.
    assert book.open_count == 1
    closed = [r for r in book.pending.values() if r["closed_at"]]
    assert closed[0]["variant"] == "conv-half"
    assert closed[0]["close_spread_pct"] == pytest.approx(midpoint_spread_pct(*books)[1])
    now += timedelta(seconds=5)
    book.process(fresh(markets, now, sell_price=99.9), [], now)
    assert book.open_count == 0
    row = next(r for r in book.pending.values() if r["variant"] == "conv-0.1")
    assert row["close_spread_pct"] < 0 and row["spread_bps"] > op.open_spread_pct * 100
    assert row["fees_bps"] == pytest.approx(22)
    assert row["slippage_bps"] == 20
    assert row["net_bps"] == pytest.approx(row["spread_bps"] - 42)
    book.process(fresh(markets, now), [op], now)
    assert book.open_count == 0
    now += timedelta(seconds=600)
    book.process(fresh(markets, now), [op], now)
    assert book.open_count == 2


@pytest.mark.parametrize("reason,seconds,sell", [("stop", 5, 104), ("converged", 5, 100),
                                                  ("timeout", 86400, 100.4), ("timeout_hard", 172800, 101)])
def test_exit_reasons(reason, seconds, sell):
    book = ledger()
    markets, op = pair()
    book.process(markets, [op], NOW)
    now = NOW + timedelta(seconds=seconds)
    book.process(fresh(markets, now, sell_price=sell), [], now)
    row = next(r for r in book.pending.values() if r["variant"] == "conv-0.1")
    assert row["exit_reason"] == reason


@pytest.mark.parametrize("missing", [True, False])
def test_stale_boundary_last_available_prices_and_priority(missing):
    book = ledger()
    markets, op = pair()
    book.process(markets, [op], NOW)
    last = op.close_spread_pct
    # An invalid book must not manufacture a convergence/stop at 599 seconds.
    invalid = [] if missing else [m.model_copy(update={"bid": 1, "ask": 1}) for m in markets]
    book.process(invalid, [], NOW + timedelta(seconds=599))
    assert book.open_count == 2
    book.process(invalid, [], NOW + timedelta(seconds=600))
    assert book.open_count == 0
    assert all(r["exit_reason"] == "stale" and r["close_spread_pct"] == last for r in book.pending.values())


@pytest.mark.asyncio
async def test_batch_restore_funding_no_duplicate_settlement_retention_and_cooldown(tmp_path):
    db = await connect_database(str(tmp_path / "shadow.db"))
    try:
        await initialize_schema(db)
        await initialize_shadow_schema(db)
        repo = ShadowTradeRepository(db)
        book = ledger()
        markets, op = pair()
        for m, rate in zip(markets, [0.01, 0.03]):
            m.funding_rate_pct = rate
            m.funding_interval_hours = 1
            m.funding_next_time = NOW + timedelta(seconds=30)
        book.process(markets, [op], NOW)
        await book.flush(repo)
        restored = ledger()
        restored.restore(*(await repo.restore(NOW)))
        assert restored.open_count == 2
        now = NOW + timedelta(seconds=31)
        books = fresh(markets, now)
        # Post-settlement rates belong to the next cycle, not the crossed one.
        for m in books:
            m.funding_rate_pct = 99
            m.funding_next_time += timedelta(hours=1)
        restored.process(books, [], now)
        await restored.flush(repo)
        rows = await repo.list_trades(status="open")
        assert all(r["funding_bps"] == pytest.approx(2) for r in rows)
        again = ledger()
        again.restore(*(await repo.restore(now)))
        again.process(books, [], now)
        now += timedelta(seconds=5)
        again.process(fresh(books, now, sell_price=100), [], now)
        await again.flush(repo)
        assert all(r["funding_bps"] == pytest.approx(2) for r in await repo.list_trades(status="closed"))
        final = ledger()
        final.restore(*(await repo.restore(now)))
        final.process(fresh(markets, now), [op], now)
        assert final.open_count == 0
        await repo.prune(now + timedelta(days=31))
        assert await repo.list_trades() == []
    finally:
        await db.close()


@pytest.mark.parametrize("volume,tier", [(None, "unknown"), (499999, "lt500"), (500000, "500-5k"),
                                        (5e6, "5k-50k"), (50e6, "5k-50k"), (50e6 + 1, "gt50k")])
def test_liquidity_boundaries(volume, tier):
    assert liquidity_tier(volume, 1e9) == tier


def test_spread_boundaries():
    assert [spread_tier(v) for v in [0.1, 0.2, 0.3, 0.5, 1, 2, 5, 5.01]] == [
        "<0.2%", "0.2–0.3%", "0.3–0.5%", "0.5–1%", "1–2%", "2–5%", "2–5%", ">5%"]


def test_funding_separate_cycles_signs_and_settlement_time_rate():
    book = ledger()
    markets, op = pair()
    for m, rate, seconds, interval in zip(markets, [0.01, 0.03], [30, 60], [1, 8]):
        m.funding_rate_pct = rate
        m.funding_next_time = NOW + timedelta(seconds=seconds)
        m.funding_interval_hours = interval
    book.process(markets, [op], NOW)
    now = NOW + timedelta(seconds=30)
    books = fresh(markets, now)
    books[0].funding_rate_pct = 0.02  # actual observation for this exact settlement
    book.process(books, [], now)
    assert all(r["funding_bps"] == -2 for p in book.positions.values() for r in p.rows)
    now += timedelta(seconds=30)
    books = fresh(books, now)
    book.process(books, [], now)
    assert all(r["funding_bps"] == 1 for p in book.positions.values() for r in p.rows)


def test_open_on_settlement_excludes_it_but_keeps_next_cycle():
    book = ledger()
    markets, op = pair()
    for m in markets:
        m.funding_next_time = NOW
        m.funding_rate_pct = 0.01
        m.funding_interval_hours = 1
    markets[1].funding_rate_pct = 0.03
    book.process(markets, [op], NOW)
    book.process(markets, [], NOW)
    now = NOW + timedelta(hours=1)
    book.process(fresh(markets, now), [], now)
    assert all(r["funding_bps"] == pytest.approx(2) for p in book.positions.values() for r in p.rows)


@pytest.mark.asyncio
async def test_batch_failure_is_atomic_and_preserves_prior_writes():
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        await initialize_shadow_schema(db)
        repo = ShadowTradeRepository(db)
        await db.execute("CREATE TABLE unrelated (value TEXT)")
        await db.execute("INSERT INTO unrelated VALUES ('keep')")
        book = ledger()
        markets, op = pair()
        book.process(markets, [op], NOW)
        rows = list(book.pending.values())
        # Duplicate open opportunity/variant with a distinct id fails midway.
        bad = {**rows[0], "id": "conflicting-id"}
        with pytest.raises(Exception):
            await repo.save_batch([rows[0], bad])
        assert await repo.list_trades() == []
        async with db.execute("SELECT value FROM unrelated") as cursor:
            assert (await cursor.fetchone())[0] == "keep"
        await book.flush(repo)
        assert len(await repo.list_trades()) == 2
    finally:
        await db.close()
