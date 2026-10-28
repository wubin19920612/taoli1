import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from test_astro_preview_fresh import create, fresh_app, preview
from test_market_identity_validation import market
from test_market_validation_review import setup_pair

from app.models.settings import RiskSettings
from app.services.orderbook_validator import OrderBookDepthValidator


@pytest.mark.asyncio
async def test_priority_review_escapes_busy_background_but_uses_book_limit():
    service, a, b, adapters = setup_pair(market(), market("bybit", price=0.101))
    entered, release = asyncio.Event(), asyncio.Event()

    async def occupy_background():
        async with service.semaphore:
            entered.set()
            await release.wait()

    service.semaphore = asyncio.Semaphore(1)
    blocker = asyncio.create_task(occupy_background())
    await entered.wait()
    queued = asyncio.create_task(service.review(a, b, RiskSettings()))
    await asyncio.sleep(0)

    class CountedSemaphore(asyncio.Semaphore):
        entries = 0

        async def __aenter__(self):
            self.entries += 1
            return await super().__aenter__()

    service.book_semaphore = CountedSemaphore(1)
    try:
        result, _, _ = await asyncio.wait_for(
            service.review(a, b, RiskSettings(), priority=True), 1,
        )
        assert result.capabilities.manual_card
        assert not queued.done()
        assert service.book_semaphore.entries == 2
        assert [adapter.calls for adapter in adapters] == [1, 1]
        assert service.manual_semaphore._value == 2
    finally:
        release.set()
        await blocker
        await queued
        await service.aclose()


@pytest.mark.parametrize("entry", ["instrument", "opportunity"])
@pytest.mark.parametrize("variants", [1, 2])
def test_manual_create_one_review_and_only_inter_variant_sleep(entry, variants, monkeypatch, caplog):
    app, opportunity, route, adapters, external = fresh_app(real_submit=True)
    app.state.orderbook_validator = OrderBookDepthValidator(adapters)
    app.state.astro_hl_gc_catalog.supports_opportunity.return_value = variants == 2
    events = []

    async def add(pair):
        events.append("add")

    async def sleep(seconds):
        events.append(("sleep", seconds))

    external.add_pair.side_effect = add
    monkeypatch.setattr("app.services.astro_alerts.asyncio.sleep", sleep)
    with caplog.at_level(logging.INFO), TestClient(app) as client:
        response = create(client, entry, opportunity, route)
    assert response.json()["status"] == "created"
    assert events == (["add"] if variants == 1 else ["add", ("sleep", 3.0), "add"])
    calls = app.state.market_validation.qualify_opportunity.await_args_list
    assert len(calls) == 1
    assert calls[0].kwargs == {"fresh": True, "fetch_books_when_unverified": True, "priority": True}
    assert all(adapter.fetch_order_book.await_count == 1 for adapter in adapters)
    rows = [json.loads(record.message) for record in caplog.records
            if record.message.startswith('{"astro_manual_create_timing"')]
    assert len(rows) == 1
    row = rows[0]
    assert {"pair", "variants", "review_ms", "depth_ms", "list_ms", "add_ms",
            "sleep_ms", "total_ms", "reviews"} <= row.keys()
    assert row["variants"] == variants and row["reviews"] == 1
    assert len(row["add_ms"]) == variants


def test_preview_priority_and_timing(caplog):
    app, opportunity, route, _, _ = fresh_app()
    with caplog.at_level(logging.INFO), TestClient(app) as client:
        assert preview(client, "instrument", opportunity, route).json()["can_submit"]
    assert app.state.market_validation.qualify_opportunity.await_args.kwargs["priority"] is True
    rows = [json.loads(record.message) for record in caplog.records
            if record.message.startswith('{"astro_manual_preview_timing"')]
    assert len(rows) == 1 and rows[0]["reviews"] == 1
    assert rows[0]["total_ms"] >= rows[0]["review_ms"] >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize("age,expected", [(5.0, 1), (5.001, 2)])
async def test_request_review_age_boundary(age, expected, monkeypatch):
    from app.services.market_validation_review import ManualReview

    app, opportunity, _, _, _ = fresh_app(real_submit=True)
    clock = [100.0]
    monkeypatch.setattr("app.services.market_validation_review.monotonic", lambda: clock[0])
    context = ManualReview()
    await context.qualify(app.state.market_validation, opportunity, RiskSettings())
    clock[0] += age
    await context.qualify(app.state.market_validation, opportunity, RiskSettings())
    assert app.state.market_validation.qualify_opportunity.await_count == expected
    assert all(call.kwargs["priority"] for call in
               app.state.market_validation.qualify_opportunity.await_args_list)


@pytest.mark.asyncio
async def test_depth_and_list_overlap_and_depth_exception_remains_warning():
    from app.api.routes_astro import _create_astro_card

    app, opportunity, _, _, external = fresh_app(real_submit=True)
    depth_started, list_started = asyncio.Event(), asyncio.Event()

    async def depth(*args, **kwargs):
        depth_started.set()
        await asyncio.wait_for(list_started.wait(), 1)
        raise ValueError("depth unavailable")

    async def list_pairs():
        list_started.set()
        await asyncio.wait_for(depth_started.wait(), 1)
        return []

    app.state.orderbook_validator = SimpleNamespace(validate=AsyncMock(side_effect=depth))
    external.list_pairs.side_effect = list_pairs
    result = await _create_astro_card(SimpleNamespace(app=app), opportunity, None)
    assert result.status == "created"
    assert any("depth unavailable" in warning for warning in result.warnings)
    external.add_pair.assert_awaited_once()


@pytest.mark.asyncio
async def test_cached_review_still_blocks_mapping_change_before_add():
    from app.api.routes_astro import _create_astro_card

    app, opportunity, _, _, external = fresh_app(real_submit=True)
    service = app.state.market_validation

    async def list_pairs():
        bindings = [row.model_copy(update={"revision": row.revision + 1})
                    for row in service.test_bindings]
        service.registry.replace(bindings)
        return []

    external.list_pairs.side_effect = list_pairs
    result = await _create_astro_card(SimpleNamespace(app=app), opportunity, None)
    assert "市场映射已改变" in result.message
    external.add_pair.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [0, 6])
@pytest.mark.parametrize("change", ["expired", "revision", "enforce"])
async def test_slow_list_refreshes_before_add(change, age, monkeypatch):
    from app.api.routes_astro import _create_astro_card

    app, opportunity, _, _, external = fresh_app(real_submit=True)
    service = app.state.market_validation
    clock = [100.0]
    monkeypatch.setattr("app.services.market_validation_review.monotonic", lambda: clock[0])

    async def list_pairs():
        clock[0] += age
        if change != "expired":
            updates = {"revision": 2} if change == "revision" else {"identity_status": "conflict"}
            service.registry.replace([row.model_copy(update=updates) for row in service.test_bindings])
        return []

    external.list_pairs.side_effect = list_pairs
    result = await _create_astro_card(SimpleNamespace(app=app), opportunity, None)
    if change == "expired":
        assert result.status == "created"
        assert service.qualify_opportunity.await_count == (2 if age > 5 else 1)
        external.add_pair.assert_awaited_once()
    else:
        assert result.status == "skipped"
        assert ("市场映射已改变" if change == "revision" else "资产或合约规格与核验记录冲突") in result.message
        external.add_pair.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["alert", "preadd"])
async def test_automatic_and_preadd_keep_fresh_reviews_and_trailing_sleep(entry, monkeypatch):
    from app.models.settings import AstroCardSettings
    from app.services.spread_engine import build_directional_opportunity

    app, opportunity, _, _, external = fresh_app(real_submit=True)
    validation = app.state.market_validation
    result, a, b = await validation.qualify_opportunity(opportunity, RiskSettings(), fresh=True)
    app.state.snapshot_store.set_all_markets([a, b])
    opportunity = build_directional_opportunity(a, b, mode="FF").model_copy(update={"validation": result})
    validation.qualify_opportunity.reset_mock()
    service = app.state.astro_alert_service
    service.alert_auto_create_enabled = True
    app.state.astro_hl_gc_catalog.supports_opportunity.return_value = True
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.astro_alerts.asyncio.sleep", sleep)
    if entry == "alert":
        response = await service.handle_alert(opportunity)
    else:
        response = await service.handle_preadd(opportunity, AstroCardSettings())
    assert response.status == "created"
    assert external.add_pair.await_count == 2
    assert [call.args for call in sleep.await_args_list] == [(3.0,), (3.0,)]
    assert [call.kwargs for call in validation.qualify_opportunity.await_args_list] == [
        {}, {"fresh": True}, {"fresh": True},
    ]


@pytest.mark.asyncio
async def test_parallel_list_error_retains_action_and_never_adds():
    from app.api.routes_astro import _create_astro_card
    from app.services.astro_client import AstroClientError

    app, opportunity, _, _, external = fresh_app(real_submit=True)
    external.list_pairs.side_effect = AstroClientError("list unavailable")
    result = await _create_astro_card(SimpleNamespace(app=app), opportunity, None)
    assert result.status == "failed" and result.action == "list"
    assert "list unavailable" in result.message
    external.add_pair.assert_not_awaited()
