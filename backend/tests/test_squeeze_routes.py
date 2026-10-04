from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.services.squeeze_arbitrage.route_engine import (
    BookLevel,
    LegSnapshot,
    evaluate_route,
    lsk_research_route,
    rounded_base_quantity,
    validate_route,
    vwap,
)
from app.services.squeeze_arbitrage.route_provider import PublicRouteProvider
from app.services.squeeze_arbitrage.route_repository import (
    SqueezeRouteRepository,
    initialize_route_schema,
    migrate_legacy_route_data,
)
from app.services.squeeze_arbitrage.route_runner import (
    PendingSourceError,
    RouteMonitorConfig,
    SqueezeRouteMonitor,
)
from app.services.squeeze_arbitrage.route_tracker import RouteTracker

D = Decimal
NOW = datetime(2026, 9, 25, 17, tzinfo=UTC)
ROUTE = lsk_research_route()


def books(
    at: datetime = NOW, *, high: str = "1.2", low: str = "1",
    high_seq: int = 1, low_seq: int = 1, quantity: str = "2000",
) -> tuple[LegSnapshot, LegSnapshot]:
    def snapshot(leg, bid, ask, seq):
        return LegSnapshot(
            leg.key, (BookLevel(D(bid), D(quantity)),),
            (BookLevel(D(ask), D(quantity)),), at, at, seq,
            at - timedelta(seconds=2), D(100),
            D("-0.001"), 1, at + timedelta(hours=1), at,
            at - timedelta(minutes=1), D(1_000_000),
            "bybit_current_public_estimate" if leg.exchange == "bybit"
            else "binance_last_public_rate_proxy",
        )

    return (
        snapshot(ROUTE.expensive, high, str(D(high) + D("0.001")), high_seq),
        snapshot(ROUTE.cheap, str(D(low) - D("0.001")), low, low_seq),
    )


def test_exact_identity_and_multiplier_are_required() -> None:
    assert validate_route(ROUTE) == ()
    wrong = replace(ROUTE, cheap=replace(
        ROUTE.cheap, asset_id="giants:GIANTS", dex="other", quote_asset="USDC"
    ))
    assert {"asset_identity_mismatch", "quote_asset_mismatch"}.issubset(
        validate_route(wrong)
    )
    scaled = replace(ROUTE, cheap=replace(
        ROUTE.cheap, contract_base_qty=D(1000), quantity_step=D("0.001")
    ))
    high, low = books()
    scaled_low = replace(low, bids=(BookLevel(D(1000), D(2)),),
                         asks=(BookLevel(D(1001), D(2)),))
    fill = vwap(scaled_low, scaled.cheap, "buy", D(100))
    assert fill is not None and fill.unit_price == D("1.001")
    assert fill.base_quantity == D(100)
    assert rounded_base_quantity(ROUTE, D(100), D("1.01")) == D(99)
    assert high.market_key != scaled.cheap.key
    high, low = books()
    invalid_step = evaluate_route(
        ROUTE, high, low, NOW, target_residual=D(0),
        quantity_overrides=(D("100.5"), D("500"), D("1000")),
    )
    assert "cheap_quantity_step_mismatch" in invalid_step.capacities[0].blockers


def test_four_vwaps_same_base_quantity_and_all_costs() -> None:
    high, low = books()
    evaluation = evaluate_route(ROUTE, high, low, NOW, target_residual=D("0.01"))
    assert evaluation.quality == "research_only"
    cap = evaluation.capacities[0]
    assert cap.base_quantity == D(100)
    assert {fill.base_quantity for fill in (
        cap.expensive_open_sell, cap.expensive_close_buy,
        cap.cheap_open_buy, cap.cheap_close_sell,
    )} == {D(100)}
    assert cap.open_difference == D("0.2")
    assert cap.entry_fee > 0 and cap.estimated_exit_fee > 0
    assert cap.estimated_borrow == 0
    assert cap.latency_buffer > 0
    assert cap.estimated_net == (
        cap.base_quantity * (cap.open_difference - cap.target_residual)
        - cap.entry_fee - cap.estimated_exit_fee
        + cap.estimated_funding - cap.latency_buffer
    )


def test_different_funding_intervals_and_missing_borrow_block() -> None:
    high, low = books()
    four_hour_low = replace(
        low, funding_interval_hours=4, next_funding_at=NOW + timedelta(hours=4)
    )
    evaluation = evaluate_route(
        ROUTE, high, four_hour_low, NOW, target_residual=D(0)
    )
    assert evaluation.capacities[0].estimated_funding == D("-0.24")
    spot_short = replace(
        ROUTE, expensive=replace(ROUTE.expensive, market_type="spot")
    )
    high_spot = replace(high, market_key=spot_short.expensive.key)
    blocked = evaluate_route(
        spot_short, high_spot, low, NOW, target_residual=D(0)
    )
    assert "non_linear_route_requires_borrow_or_inventory" in blocked.blockers
    assert "borrow_unknown" in blocked.capacities[0].blockers
    assert blocked.capacities[0].estimated_borrow is None


def test_stale_missing_trade_crossed_book_and_depth_block() -> None:
    high, low = books()
    stale = evaluate_route(ROUTE, replace(high, source_at=NOW - timedelta(seconds=2)),
                           low, NOW, target_residual=D(0))
    assert "expensive_source_stale" in stale.blockers
    no_trade = evaluate_route(ROUTE, replace(high, last_trade_notional=D(0)),
                              low, NOW, target_residual=D(0))
    assert "expensive_recent_trade_zero" in no_trade.blockers
    crossed = evaluate_route(ROUTE, replace(high, asks=(BookLevel(D("1.19"), D(2000)),)),
                             low, NOW, target_residual=D(0))
    assert "expensive_book_crossed" in crossed.blockers
    thin = evaluate_route(ROUTE, replace(high, bids=(BookLevel(D("1.2"), D(1)),)),
                          low, NOW, target_residual=D(0))
    assert "expensive_open_depth_insufficient" in thin.capacities[0].blockers


def test_absolute_peak_does_not_contract_when_both_prices_rise() -> None:
    tracker = RouteTracker(ROUTE.route_id, baseline=D(0), baseline_at=NOW)
    high, low = books()
    _, transition = tracker.advance(ROUTE, high, low, NOW)
    assert transition == "dislocation"
    high2, low2 = books(NOW + timedelta(seconds=5), high="1.3", low="1.1",
                        high_seq=2, low_seq=2)
    _, transition = tracker.advance(ROUTE, high2, low2, NOW + timedelta(seconds=5))
    assert transition is None
    assert tracker.phase == "dislocation"
    assert tracker.confirmation_count == 0


def test_confirm_needs_three_distinct_snapshots_and_three_seconds() -> None:
    tracker = RouteTracker(ROUTE.route_id, baseline=D(0), baseline_at=NOW)
    high, low = books()
    tracker.advance(ROUTE, high, low, NOW)
    for seconds, seq in ((5, 2), (7, 3), (9, 4)):
        at = NOW + timedelta(seconds=seconds)
        high, low = books(at, high="1.15", high_seq=seq, low_seq=seq)
        _, transition = tracker.advance(ROUTE, high, low, at)
    assert transition == "confirmed"
    assert tracker.phase == "confirmed"
    assert tracker.frozen_quantities[0] == D(100)


@pytest.mark.asyncio
async def test_state_survives_restart_and_candidate_keeps_inputs() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SqueezeRouteRepository(db)
        tracker = RouteTracker(ROUTE.route_id, baseline=D(0), baseline_at=NOW)
        high, low = books()
        evaluation, transition = tracker.advance(ROUTE, high, low, NOW)
        await repo.save_scan(ROUTE, tracker, evaluation, high, low, transition)
        restored = await repo.load_tracker(ROUTE.route_id)
        assert restored.phase == "dislocation"
        assert restored.event_id == tracker.event_id
        events = await repo.list_events(include_inputs=True)
        assert len(events) == 1
        assert events[0]["inputs"]["expensive"]["bids"][0]["price"] == "1.2"
        assert events[0]["evaluation"]["capacities"][0]["base_quantity"] == "100"
        assert (await repo.list_routes())[0]["state"]["phase"] == "dislocation"
        for seconds, seq in ((5, 2), (7, 3), (9, 4)):
            at = NOW + timedelta(seconds=seconds)
            next_high, next_low = books(
                at, high="1.15", high_seq=seq, low_seq=seq
            )
            next_evaluation, next_transition = restored.advance(
                ROUTE, next_high, next_low, at
            )
            await repo.save_scan(
                ROUTE, restored, next_evaluation, next_high, next_low,
                next_transition,
            )
        assert (await repo.load_tracker(ROUTE.route_id)).phase == "confirmed"
        assert len(await repo.list_events(include_inputs=True)) == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_provider_failure_clears_confirmation_state_on_disk() -> None:
    class FailingAfterFirst:
        calls = 0

        async def fetch_route(self, at):
            self.calls += 1
            if self.calls > 1:
                raise TimeoutError("public depth unavailable")
            return ROUTE, *books(at)

        async def aclose(self):
            pass

    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SqueezeRouteRepository(db)
        await repo.save_tracker(RouteTracker(
            ROUTE.route_id, baseline=D(0), baseline_at=NOW
        ), NOW)
        monitor = SqueezeRouteMonitor(
            repo, RouteMonitorConfig(), FailingAfterFirst()
        )
        await monitor.scan_once(NOW)
        assert (await repo.load_tracker(ROUTE.route_id)).phase == "dislocation"
        await monitor.scan_once(NOW + timedelta(seconds=5))
        restored = await repo.load_tracker(ROUTE.route_id)
        assert restored.phase == "blocked"
        assert restored.confirmation_count == 0
        assert (await repo.status(enabled=True))["last_error"].startswith("TimeoutError")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_route_write_rolls_back_partial_snapshot(monkeypatch, tmp_path) -> None:
    db = await connect_database(str(tmp_path / "partial-route.db"))
    try:
        await initialize_schema(db)
        repo = SqueezeRouteRepository(db)
        tracker = RouteTracker(ROUTE.route_id, baseline=D(0), baseline_at=NOW)
        high, low = books()
        evaluation, transition = tracker.advance(ROUTE, high, low, NOW)
        execute = db.execute

        async def fail_latest(sql, parameters=None):
            if "INSERT INTO squeeze_route_latest" in sql:
                raise sqlite3.OperationalError("database is locked")
            return await execute(sql, parameters)

        monkeypatch.setattr(db, "execute", fail_latest)
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            await repo.save_scan(ROUTE, tracker, evaluation, high, low, transition)
        assert (await repo.load_tracker(ROUTE.route_id)).phase == "watching"
        assert await repo.list_routes() == []
        assert await repo.list_events() == []
        assert not db.in_transaction
        monkeypatch.setattr(db, "execute", execute)
        await repo.save_scan(ROUTE, tracker, evaluation, high, low, transition)
        assert len(await repo.list_routes()) == 1
        assert len(await repo.list_events()) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_locked_route_db_reports_error_and_worker_recovers(tmp_path) -> None:
    path = str(tmp_path / "locked-route.db")
    db = await connect_database(path)
    holder = await connect_database(path)
    stop_event = asyncio.Event()
    task = None

    class Provider:
        sequence = 0

        async def fetch_route(self, at):
            self.sequence += 1
            return ROUTE, *books(at, high_seq=self.sequence, low_seq=self.sequence)

        async def aclose(self):
            pass

    try:
        await initialize_schema(db)
        repo = SqueezeRouteRepository(db)
        await repo.save_tracker(
            RouteTracker(ROUTE.route_id, baseline=D(0), baseline_at=NOW), NOW
        )
        await db.execute("PRAGMA busy_timeout=20")
        await holder.execute("BEGIN IMMEDIATE")
        monitor = SqueezeRouteMonitor(repo, RouteMonitorConfig(poll_seconds=3), Provider())
        task = asyncio.create_task(monitor.run(stop_event))
        for _ in range(80):
            if monitor.last_error is not None:
                break
            await asyncio.sleep(0.05)
        assert monitor.last_error == "OperationalError:database is locked"
        assert not task.done()
        status = await monitor.status()
        assert status["enabled"] is True
        assert status["last_error"] == monitor.last_error
        assert status["last_attempt_at"] is not None
        await asyncio.sleep(3.5)
        assert monitor.provider.sequence >= 2
        assert (await monitor.status())["queue_depth"] >= 2
        assert await repo.list_routes() == []
        await holder.rollback()
        for _ in range(100):
            status = await monitor.status()
            if status["queue_depth"] == 0 and (await repo.list_routes()):
                break
            await asyncio.sleep(0.05)
        assert monitor.last_error is None
        persisted = await repo.status(enabled=True)
        assert persisted["last_error"] is None
        assert persisted["storage_failure_count"] >= 1
        assert persisted["last_storage_error_at"] is not None
        assert persisted["dropped_scan_count"] == 0
        assert (await repo.load_tracker(ROUTE.route_id)).phase == "dislocation"
        assert len(await repo.list_events()) == 1
    finally:
        stop_event.set()
        if task is not None:
            await task
        await holder.rollback()
        await holder.close()
        await db.close()


@pytest.mark.asyncio
async def test_route_worker_state_migrates_existing_database(tmp_path) -> None:
    db = await connect_database(str(tmp_path / "old-route.db"))
    try:
        await db.execute(
            """CREATE TABLE squeeze_route_worker_state (
               id INTEGER PRIMARY KEY CHECK(id = 1), last_attempt_at TEXT,
               last_success_at TEXT, last_error TEXT, last_route_id TEXT)"""
        )
        await db.execute(
            "INSERT INTO squeeze_route_worker_state(id,last_error) VALUES (1,'old error')"
        )
        await db.commit()
        await initialize_schema(db)
        status = await SqueezeRouteRepository(db).status(enabled=False)
        assert status["last_error"] == "old error"
        assert status["storage_failure_count"] == 0
        assert status["dropped_scan_count"] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_legacy_route_data_moves_to_isolated_database(tmp_path) -> None:
    legacy_path = str(tmp_path / "radar.db")
    route_path = str(tmp_path / "radar-squeeze-route.db")
    legacy = await connect_database(legacy_path)
    route_db = await connect_database(route_path)
    try:
        await initialize_schema(legacy)
        old_repo = SqueezeRouteRepository(legacy)
        tracker = RouteTracker(ROUTE.route_id, baseline=D(0), baseline_at=NOW)
        high, low = books()
        evaluation, transition = tracker.advance(ROUTE, high, low, NOW)
        await old_repo.save_scan(ROUTE, tracker, evaluation, high, low, transition)
        old_latest = await legacy.execute(
            "SELECT route_id,evaluated_at,evaluation_json FROM squeeze_route_latest"
        )
        old_latest_row = await old_latest.fetchone()
        await legacy.execute("DROP TABLE squeeze_route_latest")
        await legacy.execute(
            """CREATE TABLE squeeze_route_latest (
               route_id TEXT PRIMARY KEY, evaluated_at TEXT NOT NULL,
               evaluation_json TEXT NOT NULL)"""
        )
        await legacy.execute(
            "INSERT INTO squeeze_route_latest VALUES (?,?,?)", tuple(old_latest_row)
        )
        await legacy.execute("DROP TABLE squeeze_route_worker_state")
        await legacy.execute(
            """CREATE TABLE squeeze_route_worker_state (
               id INTEGER PRIMARY KEY CHECK(id = 1), last_attempt_at TEXT,
               last_success_at TEXT, last_error TEXT, last_route_id TEXT)"""
        )
        await legacy.execute(
            """INSERT INTO squeeze_route_worker_state
               VALUES (1, ?, ?, NULL, ?)""",
            (NOW.isoformat(), NOW.isoformat(), ROUTE.route_id),
        )
        await legacy.commit()

        await initialize_route_schema(route_db)
        await migrate_legacy_route_data(route_db, legacy_path)
        new_repo = SqueezeRouteRepository(route_db)
        assert (await new_repo.load_tracker(ROUTE.route_id)).phase == "dislocation"
        assert len(await new_repo.list_events()) == 1
        assert (await new_repo.list_routes())[0]["evaluation"]["route_id"] == ROUTE.route_id
        assert await new_repo.latest_for_paper(ROUTE.route_id) is None
        assert (await new_repo.status(enabled=False))["last_success_at"] == NOW.isoformat()

        await legacy.execute("BEGIN IMMEDIATE")
        await new_repo.save_tracker(RouteTracker(ROUTE.route_id, phase="blocked"), NOW)
        await legacy.rollback()
        await migrate_legacy_route_data(route_db, legacy_path)
        assert (await new_repo.load_tracker(ROUTE.route_id)).phase == "blocked"
    finally:
        await route_db.close()
        await legacy.close()


@pytest.mark.asyncio
async def test_full_route_queue_marks_gap_and_persists_drop_count() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SqueezeRouteRepository(db)
        monitor = SqueezeRouteMonitor(repo, RouteMonitorConfig(max_pending_scans=2))
        monitor.tracker = RouteTracker(ROUTE.route_id, phase="dislocation")
        item = PendingSourceError(None, NOW, "source unavailable")
        assert monitor._enqueue(item, NOW)
        assert monitor._enqueue(item, NOW)
        assert not monitor._enqueue(item, NOW)
        assert monitor.tracker.phase == "blocked"
        assert (await monitor.status())["dropped_scan_count"] == 1
        tracker = RouteTracker(ROUTE.route_id, baseline=D(0), baseline_at=NOW)
        high, low = books()
        evaluation, transition = tracker.advance(ROUTE, high, low, NOW)
        await repo.save_scan(
            ROUTE, tracker, evaluation, high, low, transition,
            dropped_scans=monitor._pending_drops, last_drop_at=monitor.last_drop_at,
        )
        status = await repo.status(enabled=True)
        assert status["dropped_scan_count"] == 1
        assert status["last_drop_at"] == NOW.isoformat()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_provider_rejects_wrong_bybit_underlying() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "exchangeInfo" in str(request.url):
            return httpx.Response(200, json={"symbols": [{
                "symbol": "LSKUSDT", "status": "TRADING", "contractType": "PERPETUAL",
                "baseAsset": "LSK", "quoteAsset": "USDT", "marginAsset": "USDT",
                "filters": [{"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"},
                            {"filterType": "MIN_NOTIONAL", "notional": "5"}],
            }]})
        return httpx.Response(200, json={"result": {"list": [{
            "symbol": "LSKUSDT", "status": "Trading",
            "contractType": "LinearPerpetual", "fullName": "Other",
            "baseCoin": "LSK", "quoteCoin": "USDT", "settleCoin": "USDT",
        }]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = PublicRouteProvider(client)
        with pytest.raises(ValueError, match="Bybit Lisk identity"):
            await provider.verified_route(NOW)


@pytest.mark.asyncio
async def test_provider_preserves_live_response_source_time_sequence_and_lot_rules() -> None:
    now = datetime.now(UTC)
    ms = int(now.timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "exchangeInfo" in url:
            payload = {"symbols": [{
                "symbol": "LSKUSDT", "status": "TRADING", "contractType": "PERPETUAL",
                "baseAsset": "LSK", "quoteAsset": "USDT", "marginAsset": "USDT",
                "filters": [{"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"},
                            {"filterType": "MIN_NOTIONAL", "notional": "5"}],
            }]}
        elif "fundingInfo" in url:
            payload = [{"symbol": "LSKUSDT", "fundingIntervalHours": 4}]
        elif "instruments-info" in url:
            payload = {"result": {"list": [{
                "symbol": "LSKUSDT", "status": "Trading",
                "contractType": "LinearPerpetual", "fullName": "Lisk",
                "baseCoin": "LSK", "quoteCoin": "USDT", "settleCoin": "USDT",
                "lotSizeFilter": {
                    "qtyStep": "0.1", "minOrderQty": "0.1", "minNotionalValue": "5"
                },
            }]}}
        elif "premiumIndex" in url:
            payload = {
                "symbol": "LSKUSDT", "lastFundingRate": "-0.002", "nextFundingTime": ms + 4 * 3600000,
                "time": ms,
            }
        elif "ticker/24hr" in url:
            payload = {"symbol": "LSKUSDT", "quoteVolume": "1200000"}
        elif "/fapi/v1/depth" in url:
            payload = {
                "lastUpdateId": 1123, "E": ms, "T": ms,
                "bids": [["1", "1000"]], "asks": [["1.001", "1000"]],
            }
        elif "/fapi/v1/trades" in url:
            payload = [{"price": "1", "qty": "3", "time": ms}]
        elif "/orderbook" in url:
            payload = {"result": {
                "s": "LSKUSDT", "ts": ms, "seq": 4523,
                "b": [["1.02", "1000"]], "a": [["1.021", "1000"]],
            }}
        elif "/recent-trade" in url:
            payload = {"result": {"list": [{
                "symbol": "LSKUSDT", "price": "1.02", "size": "2", "time": ms
            }]}}
        elif "/tickers" in url:
            payload = {"time": ms, "result": {"list": [{
                "symbol": "LSKUSDT", "fundingRate": "-0.001",
                "fundingIntervalHour": "1", "nextFundingTime": ms + 3600000,
                "turnover24h": "900000",
            }]}}
        else:
            raise AssertionError(url)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = PublicRouteProvider(client)
        route, expensive, cheap = await provider.fetch_route(now)
    assert route.expensive.quantity_step == D("0.1")
    assert route.cheap.quantity_step == D(1)
    assert expensive.sequence == 4523 and cheap.sequence == 1123
    assert expensive.source_at == cheap.source_at
    assert expensive.last_trade_notional == D("2.04")
    assert cheap.last_trade_notional == D(3)
    assert cheap.funding_interval_hours == 4
    assert expensive.funding_interval_hours == 1
    assert expensive.turnover_24h == D(900000)
    assert cheap.turnover_24h == D(1200000)


def test_api_only_route_enabled_does_not_start_worker(monkeypatch) -> None:
    start_task = Mock()
    monkeypatch.setattr("app.main._start_background_task", start_task)
    app = create_app(
        settings=Settings(database_url="sqlite:///:memory:", squeeze_route_enabled=True),
        start_background_workers=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/squeeze-arbitrage/status").json()["routes"]["enabled"] is False
        assert client.get("/api/squeeze-arbitrage/routes").json() == []
        assert client.get("/api/squeeze-arbitrage/route-events").json() == []
    start_task.assert_not_called()


def test_file_backed_route_sampler_has_its_own_sqlite_connection(tmp_path) -> None:
    database_url = "sqlite:///" + (tmp_path / "radar.db").as_posix()
    app = create_app(
        settings=Settings(database_url=database_url),
        start_background_workers=False,
    )
    with TestClient(app) as client:
        assert app.state.squeeze_route_repo.db is not app.state.db
        assert client.get("/api/squeeze-arbitrage/routes").json() == []
    assert (tmp_path / "radar-squeeze-route.db").exists()
