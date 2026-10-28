import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_market_identity_validation import binding, market

from app.api.routes_astro import router
from app.core.config import Settings
from app.models.astro import AstroAlertActionResult
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.models.settings import RiskSettings
from app.services.astro_alerts import AstroAlertService
from app.services.astro_planner import AstroPairPlanner
from app.services.market_identity import MarketIdentityRegistry
from app.services.market_validation_review import MarketValidationService
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_directional_opportunity


def fresh_app(*, verified=True, real_submit=False, mode="enforce"):
    now = datetime.now(UTC)
    buy = market(raw="JIOUSDT", price=100).model_copy(update={
        "symbol": "JIOUSDT", "base": "JIO", "timestamp": now,
    })
    sell = market("hyperliquid", "xyz:JIO", 101, "xyz").model_copy(update={
        "symbol": "JIOUSDT", "base": "JIO", "quote_kind": "reference",
        "timestamp": now - timedelta(seconds=20), "estimated_fields": ["bid", "ask"],
    })
    store = SnapshotStore()
    store.set_all_markets([buy, sell])
    opportunity = build_directional_opportunity(
        buy, sell, mode="FF", buy_fee_pct=0.03, sell_fee_pct=0.04,
        safety_slippage_pct=0.02,
    )
    store.set_opportunities([opportunity])
    books = []
    for row, bid, ask in ((buy, 99, 100), (sell, 103, 104)):
        books.append(OrderBookSnapshot(
            exchange=row.exchange, symbol=row.symbol, market_type=row.market_type,
            raw_symbol=row.raw_symbol, timestamp=now,
            bids=[OrderBookLevel(price=bid, size=100000)],
            asks=[OrderBookLevel(price=ask, size=100000)],
        ))
    adapters = [SimpleNamespace(name=row.exchange, fetch_order_book=AsyncMock(return_value=book))
                for row, book in zip((buy, sell), books)]
    validation = MarketValidationService(None, store, adapters)
    validation.settings = validation.settings.model_copy(update={"mode": mode})
    bindings = [binding(row, "jio").model_copy(update={
        "verified_at": now, "valid_until": now + timedelta(days=1),
    }) for row in (buy, sell)] if verified else []
    validation.registry = MarketIdentityRegistry(bindings, mode=mode)
    validation.qualify_opportunity = AsyncMock(wraps=validation.qualify_opportunity)
    catalog = SimpleNamespace(supports_opportunity=AsyncMock(return_value=False))
    settings = Settings(
        dashboard_password="", astro_manual_card_create=True, astro_dry_run_only=False,
    )
    external = SimpleNamespace(list_pairs=AsyncMock(return_value=[]), add_pair=AsyncMock())
    if real_submit:
        submit = AstroAlertService(external, settings, hl_gc_catalog=catalog)
        submit.market_validation = validation
    else:
        submit = SimpleNamespace(handle_manual_create=AsyncMock(return_value=AstroAlertActionResult(
            enabled=True, status="created", action="add", message="created",
        )))
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.settings = settings
    app.state.snapshot_store = store
    app.state.market_validation = validation
    app.state.astro_hl_gc_catalog = catalog
    app.state.astro_alert_service = submit
    route = {
        "symbol": "JIOUSDT", "buy_exchange": "bitget", "buy_market_type": "future",
        "buy_raw_symbol": "JIOUSDT", "sell_exchange": "hyperliquid",
        "sell_market_type": "future", "sell_raw_symbol": "xyz:JIO", "sell_dex": "xyz",
    }
    return app, opportunity, route, adapters, external


def preview(client, entry, opportunity, route):
    if entry == "instrument":
        return client.post("/api/astro/instrument/preview", json=route)
    return client.get(f"/api/astro/preview/{opportunity.id}")


def create(client, entry, opportunity, route):
    if entry == "instrument":
        return client.post("/api/astro/instrument/card", json={
            "route": route, "expected_open_spread_pct": opportunity.open_spread_pct,
        })
    return client.post(f"/api/astro/opportunities/{opportunity.id}/card", json={})


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
def test_astro_preview_fresh_books_replace_reference_prices_and_skew(entry):
    app, opportunity, route, adapters, _ = fresh_app()
    with TestClient(app) as client:
        response = preview(client, entry, opportunity, route)
    assert response.status_code == 200
    plan = response.json()
    assert plan["can_submit"] is True
    assert plan["blockers"] == []
    assert plan["source_open_spread_pct"] == pytest.approx(200 * 3 / 203)
    assert plan["pair"]["openPosition"] == "0.029557"
    assert plan["pair"]["bHlDex"] == "xyz"
    assert plan["quoted_at"] == opportunity.buy_timestamp.isoformat().replace("+00:00", "Z")
    assert app.state.market_validation.qualify_opportunity.await_args.kwargs["fresh"] is True
    for adapter in adapters:
        adapter.fetch_order_book.assert_awaited_once()


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
def test_astro_preview_fresh_unknown_identity_remains_blocked_once(entry):
    app, opportunity, route, adapters, _ = fresh_app(verified=False)
    with TestClient(app) as client:
        response = preview(client, entry, opportunity, route)
    assert response.status_code == 200
    plan = response.json()
    assert plan["can_submit"] is False
    assert plan["blockers"] == ["资产身份待核验或证据已过期", "价格与合约单位待核验"]
    for message in ("REFERENCE_QUOTE_ONLY", "LEG_TIME_SKEW",
                    "仅有参考价格", "双方行情时间差过大"):
        assert not any(message in item for item in plan["blockers"] + plan["warnings"])
    for adapter in adapters:
        adapter.fetch_order_book.assert_awaited_once()


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
def test_astro_preview_fresh_shadow_unknown_identity_allows_manual_preview(entry):
    app, opportunity, route, adapters, _ = fresh_app(verified=False, mode="shadow")
    service = app.state.market_validation
    initial = service.validate(*app.state.snapshot_store.get_all_markets())
    assert {"REFERENCE_QUOTE_ONLY", "LEG_TIME_SKEW"} <= set(initial.reason_codes)
    with TestClient(app) as client:
        response = preview(client, entry, opportunity, route)
    assert response.status_code == 200
    plan = response.json()
    assert plan["can_submit"] is True
    assert plan["blockers"] == []
    assert plan["source_open_spread_pct"] == pytest.approx(200 * 3 / 203)
    for message in ("REFERENCE_QUOTE_ONLY", "LEG_TIME_SKEW",
                    "仅有参考价格", "双方行情时间差过大"):
        assert not any(message in item for item in plan["blockers"] + plan["warnings"])
    assert service.qualify_opportunity.await_args.kwargs["fresh"] is True
    for adapter in adapters:
        adapter.fetch_order_book.assert_awaited_once()


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
@pytest.mark.parametrize("mode", ["shadow", "enforce"])
@pytest.mark.parametrize("relation,message", [
    ("different", "双方属于不同底层资产，不能作为同币配对"),
    ("conflict", "资产或合约规格与核验记录冲突"),
])
def test_astro_preview_fresh_identity_mismatch_is_blocked(entry, mode, relation, message):
    app, opportunity, route, _, external = fresh_app(mode=mode)
    service = app.state.market_validation
    bindings = list(service.registry.bindings.values())
    updates = {"asset_id": "different-jio"} if relation == "different" else {
        "identity_status": "conflict",
    }
    bindings[1] = bindings[1].model_copy(update=updates)
    service.registry.replace(bindings)
    initial = service.validate(*app.state.snapshot_store.get_all_markets())
    assert initial.identity_relation == relation
    with TestClient(app) as client:
        response = preview(client, entry, opportunity, route)
    assert response.status_code == 200
    plan = response.json()
    assert plan["can_submit"] is False
    assert plan["blockers"].count(message) == 1
    assert service.qualify_opportunity.await_args.kwargs["fresh"] is True
    app.state.astro_alert_service.handle_manual_create.assert_not_awaited()
    external.add_pair.assert_not_awaited()


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
@pytest.mark.parametrize("error", [ValueError("review queue is full"),
                                   httpx.ReadTimeout("l2Book timed out")])
def test_astro_preview_fresh_failure_is_http_200_blocker(entry, error):
    app, opportunity, route, _, _ = fresh_app()
    app.state.market_validation.qualify_opportunity.side_effect = error
    with TestClient(app) as client:
        response = preview(client, entry, opportunity, route)
    assert response.status_code == 200
    plan = response.json()
    assert plan["can_submit"] is False
    assert any("实时盘口复核失败" in message and "请稍后重试" in message
               for message in plan["blockers"])


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
def test_astro_create_fresh_rebuilds_before_submit(entry):
    app, opportunity, route, _, _ = fresh_app()
    with TestClient(app) as client:
        response = create(client, entry, opportunity, route)
    assert response.status_code == 200
    assert response.json()["status"] == "created"
    app.state.market_validation.qualify_opportunity.assert_awaited_once()
    assert app.state.market_validation.qualify_opportunity.await_args.kwargs["fresh"] is True
    reviewed = app.state.astro_alert_service.handle_manual_create.await_args.args[0]
    assert (reviewed.buy_ask, reviewed.sell_bid) == (100, 103)
    assert reviewed.open_spread_pct == pytest.approx(200 * 3 / 203)
    assert reviewed.sell_raw_symbol == "xyz:JIO" and reviewed.sell_dex == "xyz"
    if entry == "opportunity":
        assert reviewed.id == opportunity.id
        assert (reviewed.buy_fee_pct, reviewed.sell_fee_pct, reviewed.safety_slippage_pct) == (0.03, 0.04, 0.02)


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
def test_astro_create_fresh_failure_never_calls_submit(entry):
    app, opportunity, route, _, _ = fresh_app()
    app.state.market_validation.qualify_opportunity.side_effect = ValueError("review queue is full")
    with TestClient(app) as client:
        response = create(client, entry, opportunity, route)
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "实时盘口复核失败" in response.json()["message"]
    app.state.astro_alert_service.handle_manual_create.assert_not_awaited()


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
def test_astro_create_fresh_reaches_real_submit_with_reviewed_threshold(entry):
    app, opportunity, route, _, external = fresh_app(real_submit=True)
    with TestClient(app) as client:
        response = create(client, entry, opportunity, route)
    assert response.status_code == 200
    assert response.json()["status"] == "created"
    external.add_pair.assert_awaited_once()
    assert external.add_pair.await_args.args[0]["openPosition"] == "0.029557"
    calls = app.state.market_validation.qualify_opportunity.await_args_list
    assert all(call.kwargs["fresh"] is True for call in calls)
    assert calls[0].kwargs["fetch_books_when_unverified"] is True
    assert calls[1].kwargs["fetch_books_when_unverified"] is True
    # The existing final pre-write review retains its default policy.
    assert calls[2].kwargs == {"fresh": True}


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
def test_astro_create_unknown_identity_never_submits(entry):
    app, opportunity, route, _, external = fresh_app(verified=False, real_submit=True)
    with TestClient(app) as client:
        response = create(client, entry, opportunity, route)
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert "资产身份待核验" in response.json()["message"]
    external.add_pair.assert_not_awaited()


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
def test_astro_preview_actual_book_failure_is_blocked(entry):
    app, opportunity, route, adapters, _ = fresh_app()
    adapters[1].fetch_order_book.side_effect = httpx.ReadTimeout("l2Book timed out")
    with TestClient(app) as client:
        response = preview(client, entry, opportunity, route)
    assert response.status_code == 200
    assert response.json()["can_submit"] is False
    assert "实时盘口复核失败" in response.json()["blockers"][0]


@pytest.mark.parametrize("entry", ["background", "fresh", "automatic"])
def test_astro_unverified_default_review_does_not_fetch_books(entry):
    app, opportunity, _, adapters, external = fresh_app(verified=False, real_submit=True)
    service = app.state.market_validation

    async def run():
        if entry == "background":
            buy, sell = app.state.snapshot_store.get_all_markets()
            await service._background_review(buy, sell, RiskSettings(), service.pair_key(buy, sell))
            assert not service.failures  # Do not hide errors swallowed by background review.
        elif entry == "fresh":
            result, _, _ = await service.qualify_opportunity(opportunity, RiskSettings(), fresh=True)
            assert "ASSET_IDENTITY_UNVERIFIED" in result.reason_codes
            assert not result.capabilities.manual_card
        else:
            app.state.astro_alert_service.alert_auto_create_enabled = True
            result = await app.state.astro_alert_service.handle_alert(opportunity)
            assert result.status == "skipped"
            assert service.qualify_opportunity.await_args.kwargs == {}

    asyncio.run(run())
    for adapter in adapters:
        adapter.fetch_order_book.assert_not_awaited()
    external.add_pair.assert_not_awaited()


def test_astro_manual_review_coalesces_without_changing_default_review_policy():
    app, opportunity, _, adapters, _ = fresh_app(verified=False)
    service = app.state.market_validation

    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        original_book = adapters[0].fetch_order_book.return_value

        async def delayed_book(*args):
            started.set()
            await release.wait()
            return original_book

        adapters[0].fetch_order_book.side_effect = delayed_book
        first = asyncio.create_task(service.qualify_opportunity(
            opportunity, RiskSettings(), fresh=True, fetch_books_when_unverified=True,
        ))
        await asyncio.wait_for(started.wait(), 2)
        try:
            default, _, _ = await asyncio.wait_for(
                service.qualify_opportunity(opportunity, RiskSettings(), fresh=True), 2,
            )
            assert "REFERENCE_QUOTE_ONLY" in default.reason_codes
            second = asyncio.create_task(service.qualify_opportunity(
                opportunity, RiskSettings(), fresh=True, fetch_books_when_unverified=True,
            ))
            await asyncio.sleep(0)
        finally:
            release.set()
        for result, _, _ in await asyncio.gather(first, second):
            assert "REFERENCE_QUOTE_ONLY" not in result.reason_codes
            assert "ASSET_IDENTITY_UNVERIFIED" in result.reason_codes
            assert not result.capabilities.manual_card

    asyncio.run(run())
    for adapter in adapters:
        adapter.fetch_order_book.assert_awaited_once()


def test_astro_planner_deduplicates_identity_messages_without_manual_bypass():
    app, opportunity, _, _, _ = fresh_app(verified=False)
    validation = app.state.market_validation.validate(*app.state.snapshot_store.get_all_markets())
    opportunity = opportunity.model_copy(update={"validation": validation, "identity_enforced": True})
    plan = AstroPairPlanner().plan(opportunity, allow_manual_override=True)
    assert not plan.can_submit
    assert plan.blockers.count("资产身份待核验或证据已过期") == 1
    assert plan.blockers.count("价格与合约单位待核验") == 1
    assert "仅有参考价格，缺少真实买卖报价" in plan.blockers
    assert "双方行情时间差过大" in plan.blockers
