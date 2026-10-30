from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.astro import AstroAlertActionResult
from app.models.settings import AstroCardSettings, RiskSettings
from app.services.arb_mvp_client import ArbMvpClient, ArbMvpResult
from app.services.snapshot_store import SnapshotStore
from test_arb_mvp import arb_opportunity


@pytest.fixture
def context(monkeypatch):
    opp = arb_opportunity()
    store = SnapshotStore()
    monkeypatch.setattr(store, "get_opportunities", lambda: [opp])
    app = create_app(snapshot_store=store, settings=Settings(
        database_url="sqlite:///:memory:", dashboard_password="", arb_paper_url="http://paper.test",
        arb_paper_api_key="paper-secret", arb_live_url="http://live.test", arb_live_api_key="live-secret"))
    app.state.market_validation = None
    app.state.orderbook_validator = None
    repo = AsyncMock()
    repo.get_risk_settings.return_value = RiskSettings()
    repo.find_astro_card_settings.return_value = AstroCardSettings(max_trade_usdt=20)
    repo.get_arb_mvp_default_max_position_usd.return_value = 100
    app.state.settings_repo = repo
    service = AsyncMock()
    service.card_settings = AstroCardSettings(max_trade_usdt=20)
    service.handle_manual_create.return_value = AstroAlertActionResult(enabled=True, status="created", action="add", message="astro")
    app.state.astro_alert_service = service
    add = AsyncMock(return_value=ArbMvpResult(True, "已上架 STOPPED", "BTC"))
    monkeypatch.setattr(ArbMvpClient, "add_pair", add)
    async def instrument(*args):
        return opp, []
    monkeypatch.setattr("app.api.routes_astro._find_instrument_opportunity", instrument)
    return app, TestClient(app), repo, service, add


@pytest.mark.parametrize("target", ["arb_paper", "arb_live"])
def test_arb_never_calls_or_mutates_astro(context, target, caplog):
    app, client, repo, service, add = context
    with caplog.at_level("INFO"):
        response = client.post("/api/astro/opportunities/opp-1/card", json={
            "target": target, "open_enabled": True, "max_trade_usdt": 120, "save_as_default": True})
    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["action"] == target.replace("arb_", "arb_mvp_") + "_add"
    pair = add.call_args.args[0]
    assert pair["disableOpen"] is True and pair["maxPositionUsd"] == "120"
    assert service.card_settings.max_trade_usdt == 20
    service.handle_manual_create.assert_not_called()
    repo.set_astro_card_settings.assert_not_called()
    repo.set_arb_mvp_default_max_position_usd.assert_awaited_once_with(120)
    assert target in caplog.text and "created" in caplog.text
    assert "paper-secret" not in caplog.text and "live-secret" not in caplog.text


def test_default_request_still_calls_astro(context):
    _, client, _, service, add = context
    response = client.post("/api/astro/opportunities/opp-1/card", json={})
    assert response.json()["message"] == "astro"
    service.handle_manual_create.assert_awaited_once()
    add.assert_not_called()


def test_unconfigured_disabled_and_status_private(context):
    app, client, _, service, add = context
    app.state.settings = replace(app.state.settings, arb_live_api_key="")
    status = client.get("/api/arb-mvp/status")
    assert status.json() == {"paper": {"configured": True}, "live": {"configured": False}, "default_max_position_usd": 100}
    response = client.post("/api/astro/opportunities/opp-1/card", json={"target": "arb_live"})
    assert response.json()["enabled"] is False and response.json()["status"] == "disabled"
    add.assert_not_called()
    service.handle_manual_create.assert_not_called()
    app.state.settings = replace(app.state.settings, dashboard_password="test-password")
    assert client.get("/api/arb-mvp/status").status_code == 401


def test_both_previews_and_instrument_creation_honor_target(context):
    _, client, _, service, add = context
    route = {"symbol": "BTC", "buy_exchange": "hyperliquid", "sell_exchange": "lighter",
             "buy_market_type": "future", "sell_market_type": "future"}
    for response in [client.get("/api/astro/preview/opp-1?target=arb_paper"),
                     client.post("/api/astro/instrument/preview?target=arb_live", json=route)]:
        assert response.status_code == 200 and response.json()["can_submit"]
        assert response.json()["pair"]["maxPositionUsd"] == "100"
        assert response.json()["route_variants"] == []
    response = client.post("/api/astro/instrument/card", json={"route": route, "expected_open_spread_pct": 0.8, "card": {"target": "arb_paper"}})
    assert response.json()["status"] == "created"
    service.handle_manual_create.assert_not_called()
    add.assert_awaited_once()


def test_mapping_failure_never_writes(context):
    _, client, repo, service, add = context
    response = client.post("/api/astro/opportunities/opp-1/card", json={"target": "arb_paper", "min_notional": 1, "save_as_default": True})
    assert response.json()["status"] == "skipped"
    assert response.json()["action"] == "arb_mvp_mapping"
    add.assert_not_called()
    repo.set_arb_mvp_default_max_position_usd.assert_not_called()
    service.handle_manual_create.assert_not_called()


def test_review_failure_blocks_and_depth_failure_only_warns(context, monkeypatch):
    app, client, _, service, add = context
    review = AsyncMock(return_value=(arb_opportunity(), "复核失败"))
    monkeypatch.setattr("app.api.routes_astro._review_manual_opportunity", review)
    response = client.post("/api/astro/opportunities/opp-1/card", json={"target": "arb_paper"})
    assert response.json()["status"] == "skipped"
    assert response.json()["action"] == "market_validation"
    add.assert_not_called()
    review.return_value = (arb_opportunity(), None)
    app.state.orderbook_validator = AsyncMock()
    app.state.orderbook_validator.validate.side_effect = RuntimeError("private-error")
    response = client.post("/api/astro/opportunities/opp-1/card", json={"target": "arb_paper"})
    assert response.json()["status"] == "created"
    assert any("深度检查失败" in warning for warning in response.json()["warnings"])
    assert "private-error" not in response.text
    service.handle_manual_create.assert_not_called()


@pytest.mark.asyncio
async def test_arb_default_persists_separately():
    import aiosqlite
    from app.db.repositories import SettingsRepository

    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        repo = SettingsRepository(db)
        assert await repo.get_arb_mvp_default_max_position_usd() == 100
        await repo.set_astro_card_settings(AstroCardSettings(max_trade_usdt=25))
        await repo.set_arb_mvp_default_max_position_usd(150)
        assert await SettingsRepository(db).get_arb_mvp_default_max_position_usd() == 150
        assert (await repo.get_astro_card_settings()).max_trade_usdt == 25
