from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.main import create_app
from app.services.squeeze_arbitrage.paper_engine import (
    advance_paper_trade,
    apply_funding_settlement,
    create_paper_trade,
    decode_route_inputs,
    ioc_from_book,
)
from app.services.squeeze_arbitrage.paper_funding import PublicFundingHistory
from app.services.squeeze_arbitrage.paper_models import (
    FundingSettlement,
    PaperAccount,
    PaperRun,
    PaperSettings,
    PaperTrade,
)
from app.services.squeeze_arbitrage.paper_report import build_paper_report
from app.services.squeeze_arbitrage.paper_repository import (
    SqueezePaperRepository,
    initialize_paper_schema,
)
from app.services.squeeze_arbitrage.paper_runner import SqueezePaperMonitor
from app.services.squeeze_arbitrage.route_engine import (
    BookLevel,
    LegSnapshot,
    lsk_research_route,
)
from app.services.squeeze_arbitrage.route_repository import (
    SqueezeRouteRepository,
    _inputs,
    initialize_route_schema,
)
from app.services.squeeze_arbitrage.route_tracker import RouteTracker

D = Decimal
NOW = datetime(2026, 9, 26, tzinfo=UTC)
ROUTE = lsk_research_route()
SETTINGS = PaperSettings()


def books(
    at: datetime = NOW, *, high: str = "1.2", low: str = "1",
    high_quantity: str = "2000", low_quantity: str = "2000", sequence: int = 1,
) -> tuple[LegSnapshot, LegSnapshot]:
    def snapshot(leg, bid, ask, quantity):
        return LegSnapshot(
            market_key=leg.key,
            bids=(BookLevel(D(bid), D(quantity)),),
            asks=(BookLevel(D(ask), D(quantity)),),
            source_at=at, received_at=at, sequence=sequence,
            last_trade_at=at - timedelta(seconds=2), last_trade_notional=D(100),
            funding_rate=D("-0.001"), funding_interval_hours=1,
            next_funding_at=at + timedelta(hours=1), funding_source_at=at,
            metadata_verified_at=at - timedelta(minutes=1), turnover_24h=D(1_000_000),
            funding_kind="public_estimate",
        )

    return (
        snapshot(ROUTE.expensive, high, str(D(high) + D("0.001")), high_quantity),
        snapshot(ROUTE.cheap, str(D(low) - D("0.001")), low, low_quantity),
    )


def event() -> dict:
    high, low = books()
    tracker = RouteTracker(ROUTE.route_id, baseline=D(0), baseline_at=NOW)
    evaluation, _ = tracker.advance(ROUTE, high, low, NOW)
    return {
        "id": "event-1:confirmed", "route_id": ROUTE.route_id,
        "occurred_at": NOW.isoformat(), "evaluation": evaluation.to_dict(),
        "inputs": json.loads(_inputs(ROUTE, high, low)),
    }


def accounts() -> dict[str, PaperAccount]:
    return {
        exchange: PaperAccount(
            exchange=exchange, initial_balance=D(10000),
            cash_balance=D(10000), updated_at=NOW,
        )
        for exchange in ("binance", "bybit")
    }


def test_delayed_entry_uses_new_book_and_restores_from_json() -> None:
    candidate = event()
    route, high, low = decode_route_inputs(candidate["inputs"])
    assert route.expensive.key == ROUTE.expensive.key
    assert high.bids[0].price == D("1.2")
    trade = create_paper_trade(candidate, SETTINGS)
    assert trade.status == "pending_entry"
    assert len({order.id for order in trade.orders}) == 2
    balances = accounts()
    advance_paper_trade(trade, balances, route, high, low, NOW, SETTINGS)
    assert trade.fills == []
    at = NOW + timedelta(seconds=1)
    next_high, next_low = books(at, high="1.201", low="1.0005", sequence=2)
    advance_paper_trade(trade, balances, route, next_high, next_low, at, SETTINGS)
    assert trade.status == "open"
    assert trade.expensive_entry_price == D("1.201")
    assert trade.cheap_entry_price == D("1.0005")
    assert len(trade.fills) == 2
    assert balances["bybit"].reserved_margin == D("120.1")
    restored = PaperTrade.model_validate_json(trade.model_dump_json())
    advance_paper_trade(restored, balances, route, next_high, next_low, at, SETTINGS)
    assert len(restored.fills) == 2


def test_partial_entry_recovery_flattens_both_legs_and_counts_fees() -> None:
    trade = create_paper_trade(event(), SETTINGS)
    balances = accounts()
    at = NOW + timedelta(seconds=1)
    high, low = books(at, high_quantity="50", sequence=2)
    advance_paper_trade(trade, balances, ROUTE, high, low, at, SETTINGS)
    assert trade.status == "recovering"
    assert trade.expensive_open_quantity == D(50)
    assert trade.cheap_open_quantity == D(100)
    next_at = at + timedelta(seconds=1)
    high, low = books(next_at, sequence=3)
    advance_paper_trade(trade, balances, ROUTE, high, low, next_at, SETTINGS)
    assert len([order for order in trade.orders if order.role == "recovery"]) == 2
    finish_at = next_at + timedelta(seconds=1)
    high, low = books(finish_at, sequence=4)
    advance_paper_trade(trade, balances, ROUTE, high, low, finish_at, SETTINGS)
    assert trade.status == "closed"
    assert trade.expensive_open_quantity == trade.cheap_open_quantity == 0
    assert balances["bybit"].reserved_margin == balances["binance"].reserved_margin == 0
    assert len(trade.fills) == 4
    assert trade.exit_reason == "entry_partial_or_leg_failure"
    assert trade.entry_fees > 0 and trade.exit_fees > 0
    assert trade.minimum_expensive_free_balance == D(10000) - D(50 * 1.2) - trade.fills[0].fee
    assert trade.minimum_cheap_free_balance is not None


def test_exit_waits_for_next_snapshot_and_applies_four_taker_fees() -> None:
    trade = create_paper_trade(event(), SETTINGS)
    balances = accounts()
    entry_at = NOW + timedelta(seconds=1)
    high, low = books(entry_at, sequence=2)
    advance_paper_trade(trade, balances, ROUTE, high, low, entry_at, SETTINGS)
    signal_at = NOW + timedelta(seconds=2)
    high, low = books(signal_at, high="1.05", sequence=3)
    advance_paper_trade(trade, balances, ROUTE, high, low, signal_at, SETTINGS)
    assert trade.status == "pending_exit"
    assert trade.closed_at is None
    exit_at = NOW + timedelta(seconds=3)
    high, low = books(exit_at, high="1.05", sequence=4)
    advance_paper_trade(trade, balances, ROUTE, high, low, exit_at, SETTINGS)
    assert trade.status == "closed"
    assert trade.closed_at == exit_at
    assert len(trade.fills) == 4
    assert trade.net_realized == trade.price_pnl - trade.entry_fees - trade.exit_fees
    assert trade.net_realized > 0


def test_no_improvement_reduces_half_once() -> None:
    trade = create_paper_trade(event(), SETTINGS)
    balances = accounts()
    entry_at = NOW + timedelta(seconds=1)
    high, low = books(entry_at, sequence=2)
    advance_paper_trade(trade, balances, ROUTE, high, low, entry_at, SETTINGS)
    review_at = entry_at + timedelta(seconds=SETTINGS.no_improvement_seconds)
    high, low = books(review_at, sequence=3)
    advance_paper_trade(trade, balances, ROUTE, high, low, review_at, SETTINGS)
    assert trade.status == "pending_exit"
    assert trade.exit_reason == "no_improvement_half"
    exit_orders = [order for order in trade.orders if order.role == "exit"]
    assert {order.target_quantity for order in exit_orders} == {D(50)}
    fill_at = review_at + timedelta(seconds=1)
    high, low = books(fill_at, sequence=4)
    advance_paper_trade(trade, balances, ROUTE, high, low, fill_at, SETTINGS)
    assert trade.status == "open"
    assert trade.expensive_open_quantity == trade.cheap_open_quantity == D(50)


def test_missing_exit_book_leaves_unresolved_exposure() -> None:
    trade = create_paper_trade(event(), SETTINGS)
    balances = accounts()
    entry_at = NOW + timedelta(seconds=1)
    high, low = books(entry_at, sequence=2)
    advance_paper_trade(trade, balances, ROUTE, high, low, entry_at, SETTINGS)
    late = entry_at + timedelta(seconds=SETTINGS.max_holding_seconds)
    high, low = books(late, sequence=3)
    high = replace(high, source_at=late - timedelta(seconds=5))
    advance_paper_trade(trade, balances, ROUTE, high, low, late, SETTINGS)
    assert trade.status == "unresolved"
    assert trade.closed_at is None
    assert trade.expensive_open_quantity == trade.cheap_open_quantity == D(100)
    assert balances["bybit"].reserved_margin > 0


def test_funding_uses_signed_actual_or_proxy_marks_once() -> None:
    trade = create_paper_trade(event(), SETTINGS)
    balances = accounts()
    entry_at = NOW + timedelta(seconds=1)
    high, low = books(entry_at, sequence=2)
    advance_paper_trade(trade, balances, ROUTE, high, low, entry_at, SETTINGS)
    settled_at = NOW + timedelta(hours=1)
    bybit = FundingSettlement(
        exchange="bybit", market_key=ROUTE.expensive.key, settled_at=settled_at,
        rate=D("-0.001"), mark_price=D("1.2"), mark_kind="one_minute_proxy",
        source="bybit_public_history_and_mark_kline",
    )
    binance = FundingSettlement(
        exchange="binance", market_key=ROUTE.cheap.key, settled_at=settled_at,
        rate=D("-0.002"), mark_price=D("1"), mark_kind="actual",
        source="binance_public_history",
    )
    assert apply_funding_settlement(trade, balances, bybit)
    assert apply_funding_settlement(trade, balances, binance)
    assert not apply_funding_settlement(trade, balances, bybit)
    assert trade.funding_total == D("0.08")
    assert "funding_mark_proxy" in trade.risk_labels
    assert len([flow for flow in trade.cashflows if flow.kind == "funding"]) == 2


def test_ioc_uses_contract_base_multiplier_and_visible_limit() -> None:
    scaled = replace(ROUTE.cheap, contract_base_qty=D(1000), quantity_step=D("0.001"))
    snapshot = replace(books()[1], bids=(BookLevel(D(1000), D(2)),),
                       asks=(BookLevel(D(1001), D(2)),))
    result = ioc_from_book(snapshot, scaled, "buy", D(100), D("1.001"))
    assert result.quantity == D(100)
    assert result.quote_notional == D("100.1")
    blocked = ioc_from_book(snapshot, scaled, "buy", D(100), D("1"))
    assert blocked.quantity == 0


@pytest.mark.asyncio
async def test_paper_ledger_survives_restart_and_rejects_duplicate_snapshot(tmp_path) -> None:
    path = str(tmp_path / "radar-squeeze-route.db")
    db = await connect_database(path)
    try:
        await initialize_route_schema(db)
        await initialize_paper_schema(db)
        repo = SqueezePaperRepository(db, asyncio.Lock())
        run = await repo.get_or_create_run(SETTINGS, NOW, ("bybit", "binance"))
        balances = await repo.load_accounts()
        assert {key: value.free_balance for key, value in balances.items()} == {
            "bybit": D(10000), "binance": D(10000),
        }
        trade = create_paper_trade(event(), SETTINGS)
        recorded = PaperRun(
            started_at=run.started_at, settings=run.settings,
            last_processed_at=NOW + timedelta(seconds=1),
            last_event_at=NOW, last_event_id=trade.event_id,
        )
        assert await repo.save_snapshot(recorded, balances, [trade])
        assert not await repo.save_snapshot(recorded, balances, [trade])
    finally:
        await db.close()
    db = await connect_database(path)
    try:
        await initialize_route_schema(db)
        await initialize_paper_schema(db)
        repo = SqueezePaperRepository(db, asyncio.Lock())
        restored = await repo.get_or_create_run(SETTINGS, NOW + timedelta(seconds=2),
                                                ("bybit", "binance"))
        assert restored.started_at == NOW
        assert restored.last_processed_at == NOW + timedelta(seconds=1)
        assert restored.last_event_id == trade.event_id
        assert len(await repo.list_trades()) == 1
        assert (await repo.list_trades())[0].orders[0].status == "pending"
        assert (await repo.load_accounts())["bybit"].cash_balance == D(10000)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_closed_trade_with_missing_funding_stays_in_recovery_queue() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_route_schema(db)
        await initialize_paper_schema(db)
        repo = SqueezePaperRepository(db, asyncio.Lock())
        run = await repo.get_or_create_run(SETTINGS, NOW, ("bybit", "binance"))
        trade = create_paper_trade(event(), SETTINGS)
        trade.status = "closed"
        trade.opened_at = NOW + timedelta(seconds=1)
        trade.closed_at = NOW + timedelta(hours=2)
        trade.funding_gap_at = NOW + timedelta(hours=1)
        recorded = PaperRun(
            started_at=run.started_at, settings=run.settings,
            last_processed_at=trade.closed_at, last_event_at=NOW,
            last_event_id=trade.event_id,
        )
        assert await repo.save_snapshot(recorded, await repo.load_accounts(), [trade])
        assert [item.id for item in await repo.list_worker_trades()] == [trade.id]
        report = build_paper_report(recorded, await repo.list_report_trades(),
                                    await repo.load_accounts(), trade.closed_at)
        assert report["closed_trades"] == 0
        assert report["closed_with_funding_gap"] == 1
        trade.funding_gap_at = None
        recorded.last_processed_at += timedelta(seconds=1)
        assert await repo.save_snapshot(recorded, await repo.load_accounts(), [trade])
        assert await repo.list_worker_trades() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_public_funding_marks_actual_and_proxy_sources() -> None:
    settled_at = NOW + timedelta(hours=1)
    millis = int(settled_at.timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/fundingRate"):
            return httpx.Response(200, json=[{
                "symbol": "LSKUSDT", "fundingTime": millis,
                "fundingRate": "-0.002", "markPrice": "1.01",
            }])
        if request.url.path.endswith("/funding/history"):
            return httpx.Response(200, json={"retCode": 0, "result": {"list": [{
                "symbol": "LSKUSDT", "fundingRateTimestamp": str(millis),
                "fundingRate": "-0.001",
            }]}})
        if request.url.path.endswith("/mark-price-kline"):
            return httpx.Response(200, json={"retCode": 0, "result": {
                "symbol": "LSKUSDT", "list": [[str(millis), "1", "1.1", "0.9", "1.02"]],
            }})
        raise AssertionError(request.url)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = PublicFundingHistory(client)
        actual = await provider.settlements(ROUTE.cheap, NOW, settled_at)
        proxy = await provider.settlements(ROUTE.expensive, NOW, settled_at + timedelta(minutes=1))
    assert actual[0].mark_kind == "actual"
    assert actual[0].mark_price == D("1.01")
    assert proxy[0].mark_kind == "one_minute_proxy"
    assert proxy[0].mark_price == D("1.02")


@pytest.mark.asyncio
async def test_funding_gap_is_not_zero_filled_or_skipped() -> None:
    trade = create_paper_trade(event(), SETTINGS)
    balances = accounts()
    opened = NOW + timedelta(seconds=1)
    high, low = books(opened, sequence=2)
    advance_paper_trade(trade, balances, ROUTE, high, low, opened, SETTINGS)
    due = trade.next_expensive_funding_at
    assert due is not None

    class FundingHistory:
        complete = False
        first_only = False

        async def settlements(self, leg, after, through):
            times = ([due, due + timedelta(hours=1)] if self.complete
                     else [due] if self.first_only else [due + timedelta(hours=1)])
            return [FundingSettlement(
                exchange=leg.exchange, market_key=leg.key, settled_at=at,
                rate=D("0.001"), mark_price=D(1), mark_kind="actual", source="fixture",
            ) for at in times if after < at <= through]

        async def aclose(self):
            pass

    funding = FundingHistory()
    db = await connect_database(":memory:")
    try:
        await initialize_route_schema(db)
        await initialize_paper_schema(db)
        route_repo = SqueezeRouteRepository(db)
        monitor = SqueezePaperMonitor(
            SqueezePaperRepository(db, route_repo._lock), route_repo, SETTINGS, funding
        )
        observed = due + timedelta(hours=1, minutes=2)
        await monitor._settle_due(trade, balances, ROUTE, observed)
        assert trade.funding_total == 0
        assert trade.funding_gap_at == due
        assert trade.next_expensive_funding_at == due
        funding.first_only = True
        await monitor._settle_due(trade, balances, ROUTE, observed + timedelta(minutes=2))
        assert trade.funding_gap_at == due + timedelta(hours=1)
        assert trade.next_expensive_funding_at == due + timedelta(hours=1)
        assert len([flow for flow in trade.cashflows if flow.kind == "funding"]) == 1
        funding.complete = True
        await monitor._settle_due(trade, balances, ROUTE, observed + timedelta(minutes=4))
        assert trade.funding_gap_at is None
        assert trade.next_expensive_funding_at == due + timedelta(hours=2)
        assert len([flow for flow in trade.cashflows if flow.kind == "funding"]) == 4
    finally:
        await db.close()


def test_report_requires_time_and_independent_events_and_keeps_unresolved() -> None:
    run = PaperRun(started_at=NOW, settings=SETTINGS, last_event_at=NOW)
    trade = create_paper_trade(event(), SETTINGS)
    trade.status = "unresolved"
    trade.opened_at = NOW + timedelta(seconds=1)
    trade.expensive_open_quantity = D(50)
    trade.minimum_expensive_free_balance = D(9900)
    trade.worst_close_difference = trade.signal_open_difference + D("0.03")
    trade.funding_gap_at = NOW + timedelta(hours=1)
    first = build_paper_report(run, [trade], accounts(), NOW + timedelta(days=15))
    assert first["sample_status"] == "sample_insufficient"
    assert first["unresolved_events"] == 1
    assert first["funding_gap_trades"] == 1
    assert first["maximum_adverse_spread_expansion"] == "0.03"
    assert first["minimum_free_balance_by_leg"][ROUTE.expensive.key] == "9900"
    assert first["profitability_conclusion"] is None
    independent = [trade.model_copy(update={"id": str(index), "event_id": str(index)})
                   for index in range(30)]
    early = build_paper_report(run, independent, accounts(), NOW + timedelta(days=13))
    mature = build_paper_report(run, independent, accounts(), NOW + timedelta(days=14))
    assert early["sample_status"] == "sample_insufficient"
    assert mature["sample_status"] == "ready_for_review"
    assert mature["profitability_conclusion"] is None


def test_api_only_paper_enabled_exposes_empty_read_only_endpoints(monkeypatch) -> None:
    start_task = Mock()
    monkeypatch.setattr("app.main._start_background_task", start_task)
    app = create_app(
        settings=Settings(database_url="sqlite:///:memory:", squeeze_paper_enabled=True),
        start_background_workers=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/squeeze-arbitrage/paper/status").json()["enabled"] is False
        assert client.get("/api/squeeze-arbitrage/status").json()["paper"]["enabled"] is False
        assert client.get("/api/squeeze-arbitrage/paper/positions").json() == []
        assert client.get("/api/squeeze-arbitrage/paper/trades").json() == []
        report = client.get("/api/squeeze-arbitrage/paper/report").json()
        assert report["sample_status"] == "sample_insufficient"
        assert report["independent_events"] == 0
        assert client.get("/api/squeeze-arbitrage/paper/trades?limit=101").status_code == 422
    start_task.assert_not_called()


@pytest.mark.asyncio
async def test_paper_monitor_uses_durable_event_and_next_route_snapshot() -> None:
    class NoFunding:
        async def settlements(self, leg, after, through):
            raise AssertionError("funding is not due")

        async def aclose(self):
            pass

    db = await connect_database(":memory:")
    try:
        await initialize_route_schema(db)
        await initialize_paper_schema(db)
        route_repo = SqueezeRouteRepository(db)
        paper_repo = SqueezePaperRepository(db, route_repo._lock)
        tracker = RouteTracker(ROUTE.route_id, baseline=D(0), baseline_at=NOW)
        high, low = books()
        evaluation, _ = tracker.advance(ROUTE, high, low, NOW)
        await route_repo.save_scan(ROUTE, tracker, evaluation, high, low, "confirmed")
        monitor = SqueezePaperMonitor(paper_repo, route_repo, SETTINGS, NoFunding())
        await monitor.scan_once(NOW)
        assert (await paper_repo.list_trades())[0].status == "pending_entry"
        at = NOW + timedelta(seconds=1)
        high, low = books(at, sequence=2)
        evaluation, transition = tracker.advance(ROUTE, high, low, at)
        await route_repo.save_scan(ROUTE, tracker, evaluation, high, low, transition)
        await monitor.scan_once(at)
        trades = await paper_repo.list_trades()
        assert len(trades) == 1
        assert trades[0].status == "open"
        assert len(trades[0].fills) == 2
        restarted = SqueezePaperMonitor(paper_repo, route_repo, SETTINGS, NoFunding())
        await restarted.scan_once(at)
        assert len((await paper_repo.list_trades())[0].fills) == 2
        assert (await paper_repo.load_accounts())["bybit"].reserved_margin > 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_stale_route_still_settles_funding_without_moving_book_cursor() -> None:
    class FundingHistory:
        async def settlements(self, leg, after, through):
            due = NOW + timedelta(hours=1, seconds=1)
            return [FundingSettlement(
                exchange=leg.exchange, market_key=leg.key, settled_at=due,
                rate=D("0.001"), mark_price=D(1), mark_kind="actual", source="fixture",
            )] if after < due <= through else []

        async def aclose(self):
            pass

    db = await connect_database(":memory:")
    try:
        await initialize_route_schema(db)
        await initialize_paper_schema(db)
        route_repo = SqueezeRouteRepository(db)
        paper_repo = SqueezePaperRepository(db, route_repo._lock)
        tracker = RouteTracker(ROUTE.route_id, baseline=D(0), baseline_at=NOW)
        high, low = books()
        evaluation, _ = tracker.advance(ROUTE, high, low, NOW)
        await route_repo.save_scan(ROUTE, tracker, evaluation, high, low, "confirmed")
        monitor = SqueezePaperMonitor(paper_repo, route_repo, SETTINGS, FundingHistory())
        await monitor.scan_once(NOW)
        opened = NOW + timedelta(seconds=1)
        high, low = books(opened, sequence=2)
        evaluation, transition = tracker.advance(ROUTE, high, low, opened)
        await route_repo.save_scan(ROUTE, tracker, evaluation, high, low, transition)
        await monitor.scan_once(opened)
        assert (await paper_repo.list_trades())[0].status == "open"

        await monitor.scan_once(NOW + timedelta(hours=1, minutes=2))
        run = await paper_repo.load_run()
        trade = (await paper_repo.list_trades())[0]
        assert run is not None and run.last_processed_at == opened
        assert len([flow for flow in trade.cashflows if flow.kind == "funding"]) == 2
        await monitor.scan_once(NOW + timedelta(hours=1, minutes=3))
        assert len((await paper_repo.list_trades())[0].cashflows) == len(trade.cashflows)

        await monitor.scan_once(opened + timedelta(seconds=SETTINGS.max_holding_seconds + 1))
        unresolved = (await paper_repo.list_trades())[0]
        assert unresolved.status == "unresolved"
        assert unresolved.closed_at is None
        assert unresolved.expensive_open_quantity > 0
    finally:
        await db.close()
