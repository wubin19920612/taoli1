"""Fourth-round PM requirements; keep failures visible until implementation."""

import pytest
from copy import deepcopy
from datetime import timedelta

from app.core.config import Settings
from app.db.database import connect_database
from app.db.repositories import ShadowTradeRepository
from app.db.schema import initialize_shadow_schema
from app.services.shadow_ledger import ShadowLedger
from shadow_helpers import NOW, pair


def ready_book(**settings):
    book = ShadowLedger(Settings(shadow_enabled=True, **settings))
    book.process([], [], NOW - timedelta(seconds=1))
    return book


def tick(book, seconds, ops, markets=None, sell=None):
    now = NOW + timedelta(seconds=seconds)
    if markets is None:
        markets, _ = pair()
    quotes = [m.model_copy(update={"timestamp": now}) for m in markets]
    if sell is not None:
        quotes[1] = quotes[1].model_copy(update={"bid": sell, "ask": sell})
    book.process(quotes, ops, now)


def test_crossing_baseline_absence_and_sixty_second_rearm():
    book = ShadowLedger(Settings(shadow_enabled=True))
    _, op = pair()
    tick(book, 0, [op])
    tick(book, 1, [])
    tick(book, 59, [op])
    assert book.open_count == 0
    assert book.baseline_skipped == 1
    tick(book, 60, [op.model_copy(update={"open_spread_pct": .1})])
    tick(book, 118, [])
    tick(book, 119, [op])
    assert book.open_count == 2
    assert all(r["strategy_version"] == "shadow-v2" for r in book.pending.values())


def test_cooldown_expiry_requires_a_new_crossing():
    book = ready_book()
    _, op = pair()
    tick(book, 0, [op])
    tick(book, 1, [], sell=100)
    assert book.open_count == 0
    for second in range(2, 603, 10):
        tick(book, second, [op])
    tick(book, 603, [op])
    assert book.open_count == 0
    tick(book, 604, [])
    tick(book, 663, [op])
    assert book.open_count == 2


@pytest.mark.parametrize("cancel", [False, True])
def test_stale_crossing_retries_but_below_threshold_cancels(cancel):
    book = ready_book()
    markets, op = pair()
    book.process(markets[:1], [op], NOW)
    assert book.open_count == 0
    if cancel:
        tick(book, 1, [op.model_copy(update={"open_spread_pct": .1})])
    else:
        tick(book, 1, [])  # A missing observation is not a below-threshold quote.
    tick(book, 2, [op])
    assert book.open_count == (0 if cancel else 2)
    if cancel:
        tick(book, 62, [op])
        assert book.open_count == 2


@pytest.mark.parametrize("kind,maximum,allowed,reason", [
    ("capacity", 4, 2, "capacity"),
    ("lt500", 10, 1, "liquidity"),
    ("unknown", 30, 1, "liquidity"),
    ("spot", 15, 1, "market_type"),
    ("symbol", 100, 3, "symbol"),
])
def test_quotas_count_rows_restore_release_and_drop_once(kind, maximum, allowed, reason):
    book = ready_book(shadow_max_open=maximum)
    markets, base = pair()
    updates = {}
    if kind in {"lt500", "unknown"}:
        updates["buy_volume_24h_usdt"] = 100 if kind == "lt500" else None
    if kind == "spot":
        markets = [m.model_copy(update={"market_type": "spot"}) for m in markets]
        updates.update(buy_market_type="spot", sell_market_type="spot")
    ops = [base.model_copy(update={**updates, "id": str(i),
            "symbol": "SAME" if kind == "symbol" else f"COIN{i}"}) for i in range(allowed + 1)]
    tick(book, 0, ops, markets)
    assert book.open_count == 2 * allowed
    assert book.dropped == 1
    assert book.dropped_by_reason == {k: int(k == reason) for k in
                                      ("capacity", "liquidity", "market_type", "symbol")}
    for second in (1, 10, 30, 59):
        tick(book, second, ops, markets)
    assert book.dropped == 1
    # A real restart reconstructs quotas from rows; event diagnostics reset.
    restored = ShadowLedger(Settings(shadow_enabled=True, shadow_max_open=maximum))
    restored.restore(deepcopy(list(book.pending.values())), {})
    tick(restored, 60, ops, markets)
    assert restored.open_count == 2 * allowed
    new = ops[-1].model_copy(update={"id": "new"})
    tick(restored, 61, [new], markets)
    assert restored.open_count == 2 * allowed and restored.dropped == 1
    # Close just the half-convergence rows, verifying row (not pair) release.
    tick(restored, 62, [], markets, sell=100.4)
    assert restored.open_count == allowed
    tick(restored, 63, [], markets, sell=100)
    assert restored.open_count == 0
    tick(restored, 121, [new], markets)
    assert restored.open_count == 2 and restored.dropped == 1


def test_rejected_event_stays_consumed_after_capacity_frees():
    book = ready_book(shadow_max_open=2)
    _, a = pair()
    b = a.model_copy(update={"id": "B"})
    tick(book, 0, [a, b])
    tick(book, 1, [b], sell=100)
    tick(book, 2, [b])
    assert book.open_count == 0 and book.dropped == 1
    tick(book, 62, [b])
    assert book.open_count == 2 and book.dropped == 1


def test_expired_crossing_metadata_does_not_accumulate():
    book = ShadowLedger(Settings(shadow_enabled=True))
    _, op = pair()
    book.process([], [op.model_copy(update={"id": str(i)}) for i in range(10000)], NOW)
    assert book.baseline_skipped == 10000
    book.process([], [], NOW + timedelta(seconds=60))
    assert not book.last_above
    assert not book.retry_events


def test_startup_registers_existing_spread_without_opening():
    book = ShadowLedger(Settings(shadow_enabled=True))
    markets, opportunity = pair()

    book.process(markets, [opportunity], NOW)

    assert book.open_count == 0
    assert not book.positions
    assert not book.pending
    assert book.baseline_skipped == 1


@pytest.mark.asyncio
async def test_restore_retires_v1_without_deleting_history(tmp_path):
    db = await connect_database(str(tmp_path / "round4-retirement.db"))
    try:
        await initialize_shadow_schema(db)
        repo = ShadowTradeRepository(db)
        legacy = {
            "id": "legacy-row", "opportunity_id": "legacy-op",
            "variant": "conv-0.1", "symbol": "BTCUSDT",
            "opened_at": NOW.isoformat(), "closed_at": None,
            "fake_flag": False, "strategy_version": "shadow-v1",
            "exit_reason": None, "net_bps": None,
        }
        await repo.save_batch([legacy])

        restored, _ = await repo.restore(NOW)

        assert restored == []
        history = await repo.list_trades()
        assert len(history) == 1
        assert history[0]["id"] == legacy["id"]
        assert history[0]["exit_reason"] == "retired"
        assert history[0]["closed_at"] == NOW.isoformat()
        assert history[0]["net_bps"] is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_retirement_preserves_history_excludes_stats_and_never_cools_down(tmp_path):
    from app.services.shadow_stats import statistics, buckets
    db = await connect_database(str(tmp_path / "versions.db"))
    try:
        await initialize_shadow_schema(db)
        repo = ShadowTradeRepository(db)
        book = ready_book()
        markets, op = pair()
        tick(book, 0, [op])
        rows = deepcopy(list(book.pending.values()))
        legacy = {**rows[0], "id": "v1-open", "strategy_version": "shadow-v1"}
        real_close = {**rows[1], "id": "v1-close", "opportunity_id": "real-close",
                      "strategy_version": "shadow-v1", "closed_at": NOW.isoformat(),
                      "exit_reason": "converged", "net_bps": 5, "spread_bps": 47}
        current_close = {**real_close, "id": "v2-close", "strategy_version": "shadow-v2"}
        await repo.save_batch([legacy, real_close, current_close])
        restored, cooldowns = await repo.restore(NOW)
        assert restored == []
        assert op.id not in cooldowns
        assert cooldowns["real-close"] == NOW.timestamp()  # Real exits keep the original cooldown.
        history = await repo.list_trades()
        retired = next(r for r in history if r["id"] == "v1-open")
        assert retired["exit_reason"] == "retired" and retired["net_bps"] is None
        assert len(history) == 3
        # Repeated restore must neither change retirement time nor manufacture cooldown.
        later = NOW + timedelta(seconds=61)
        restored, cooldowns = await repo.restore(later)
        assert op.id not in cooldowns
        assert next(r for r in await repo.list_trades() if r["id"] == "v1-open") == retired
        events = await repo.closed_events(NOW, later)
        assert [r["id"] for r in events] == ["v2-close"]
        old = await repo.closed_events(NOW, later, strategy_version="shadow-v1")
        assert [r["id"] for r in old] == ["v1-close"]
        assert statistics([{**retired, "net_bps": 999}])["n_evt"] == 0
        assert buckets([{**retired, "net_bps": 999}], ["variant"], True) == []
        recovered = ShadowLedger(Settings(shadow_enabled=True))
        recovered.restore(restored, cooldowns)
        tick(recovered, 61, [op])
        assert recovered.open_count == 0 and recovered.baseline_skipped == 1
        tick(recovered, 62, [])
        tick(recovered, 121, [op])
        assert recovered.open_count == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_status_and_bucket_api_strategy_selection(tmp_path):
    import httpx
    from app.main import create_app
    from auth_helpers import IsolatedSessionLookup, TEST_TOTP_SECRET, session_headers

    settings = Settings(shadow_enabled=True, database_url="sqlite:///" + str(tmp_path / "api.db"),
                        dashboard_password="test-only", dashboard_totp_secret=TEST_TOTP_SECRET)
    app = create_app(settings=settings, start_background_workers=False)
    app.state.auth_store = IsolatedSessionLookup()
    rt = app.state.shadow_runtime
    db = await connect_database(settings.sqlite_path)
    try:
        await rt.initialize(db)
        book = ready_book()
        _, op = pair()
        tick(book, 0, [op])
        tick(book, 1, [], sell=100)
        row = list(book.pending.values())[0]
        await rt.repo.save_batch([{**row, "id": "current"},
                                 {**row, "id": "old", "strategy_version": "shadow-v1"},
                                 {**row, "id": "retired", "exit_reason": "retired"}])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers=session_headers("test-password")) as client:
            state = (await client.get("/api/shadow/status")).json()
            assert state["baseline_skipped"] == 0
            assert state["dropped_by_reason"] == dict.fromkeys(
                ("capacity", "liquidity", "market_type", "symbol"), 0)
            params = {"since": NOW.isoformat(), "until": (NOW + timedelta(seconds=2)).isoformat()}
            current = await client.get("/api/shadow/buckets", params=params)
            old = await client.get("/api/shadow/buckets", params={**params, "strategy_version": "shadow-v1"})
            assert current.status_code == old.status_code == 200
            assert sum(b["n_evt"] for b in current.json()) == 1
            assert sum(b["n_evt"] for b in old.json()) == 1
    finally:
        await rt.aclose()
        await db.close()
