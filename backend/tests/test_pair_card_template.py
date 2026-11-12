from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.models.astro import AstroCardCreateRequest
from app.services.astro_alerts import AstroAlertService
from app.services.astro_planner import AstroPairPlanner, _decimal_position, _ratio_position, position_to_pct
from test_astro_instrument_routes import _install_exact_books, instrument_app, route
from test_astro_planner import opportunity

pytestmark = pytest.mark.usefixtures("astro_attempts_for_services")


class RecordingClient:
    def __init__(self):
        self.pairs = []
        self.updated = []
        self.added = []
        self.reads = 0

    async def list_pairs(self):
        self.reads += 1
        return self.pairs

    async def list_pairs_cached(self):
        raise AssertionError("lookup/update must not use cached cards")

    async def update_pair(self, pair):
        self.updated.append(pair)
        self.pairs = [pair]
        return {"code": 0}

    async def add_pair(self, pair):
        self.added.append(pair)
        return {"code": 0}


@pytest.fixture
def connected():
    app = instrument_app(astro_dry_run_only=False, astro_manual_card_create=True)
    remote = RecordingClient()
    with TestClient(app) as client:
        _install_exact_books(app)
        app.state.astro_client = remote
        app.state.astro_alert_service = AstroAlertService(settings=app.state.settings, client=remote)
        preview = client.post("/api/astro/instrument/preview", json=route()).json()
        remote.pairs = [{**preview["pair"], "status": True, "disableOpen": False,
                         "customUntouched": {"values": [1, 2]}, "maxTradeUSDT": "37"}]
        yield client, remote, app


@pytest.mark.parametrize("ratio", [False, True])
@pytest.mark.parametrize("opening,closing", [(1.2, 0.3), (-0.1, -0.5), (0.123456, 0.123455)])
def test_planner_overrides_and_strict_rounding(ratio, opening, closing):
    opp = opportunity()
    if ratio:
        opp = opp.model_copy(update={"buy_raw_symbol": "BTCUSDT", "sell_raw_symbol": "ETHUSDT"})
    thresholds = AstroCardCreateRequest(open_spread_pct=opening, close_spread_pct=closing)
    plan = AstroPairPlanner().plan(opp, thresholds=thresholds)
    encode = _ratio_position if ratio else _decimal_position
    assert plan.pair["openPosition"] == encode(opening)
    assert Decimal(plan.pair["openPosition"]) > Decimal(plan.pair["closePosition"])
    assert abs(position_to_pct(plan.pair["closePosition"], plan.pair["type"]) - closing) < 0.0002
    assert ("当前价差已达到开仓线，建卡后可能立即开仓" in plan.warnings) == (opening <= opp.open_spread_pct)


@pytest.mark.parametrize("value", [-50, -2.3, 0, 0.12345, 1.2, 50])
@pytest.mark.parametrize("kind", ["FF", "FR"])
def test_position_percent_inverse(value, kind):
    encode = _ratio_position if kind == "FR" else _decimal_position
    assert position_to_pct(encode(value), kind) == pytest.approx(value, abs=0.00008)


@pytest.mark.parametrize("bad", [
    {"open_spread_pct": 1}, {"close_spread_pct": 0},
    {"open_spread_pct": 0, "close_spread_pct": 0},
    {"open_spread_pct": -1, "close_spread_pct": 0},
    {"open_spread_pct": 50.1, "close_spread_pct": 0},
    {"open_spread_pct": 1, "close_spread_pct": -50.1},
    {"open_spread_pct": "NaN", "close_spread_pct": 0},
])
def test_invalid_thresholds_422(connected, bad):
    client, remote, _ = connected
    assert client.post("/api/astro/instrument/preview", json={**route(), **bad}).status_code == 422
    assert client.post("/api/astro/instrument/card", json={"route": route(), "card": bad, "expected_open_spread_pct": 1}).status_code == 422
    assert client.post("/api/astro/instrument/card/update", json={"route": route(), **bad}).status_code == 422
    assert remote.updated == remote.added == []


def test_preview_and_creation_use_same_override(connected):
    client, remote, _ = connected
    remote.pairs = []
    overrides = {"open_spread_pct": 0.8, "close_spread_pct": 0.2}
    preview = client.post("/api/astro/instrument/preview", json={**route(), **overrides}).json()
    assert preview["pair"]["openPosition"] == "0.008000"
    assert preview["pair"]["closePosition"] == "0.002000"
    assert "当前价差已达到开仓线，建卡后可能立即开仓" in preview["warnings"]
    response = client.post("/api/astro/instrument/card", json={"route": route(),
        "card": {**overrides, "card_variant": "non_gc"}, "expected_open_spread_pct": preview["source_open_spread_pct"]})
    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert remote.added[0]["openPosition"] == preview["pair"]["openPosition"]
    assert remote.added[0]["closePosition"] == preview["pair"]["closePosition"]


@pytest.mark.parametrize("count", [0, 1, 2])
def test_lookup_live_card_count(connected, count):
    client, remote, _ = connected
    remote.pairs *= count
    response = client.post("/api/astro/instrument/card/lookup", json=route())
    assert response.status_code == 200
    data = response.json()
    assert data["found"] == bool(count)
    assert data["count"] == count
    assert remote.reads == 1
    if count == 1:
        assert data["open_spread_pct"] == pytest.approx(float(data["open_position"]) * 100)
        assert data["status"] is True
        assert data["disable_open"] is False
    else:
        assert data["open_position"] is None


def test_update_preserves_every_other_field_and_logs(connected, caplog):
    client, remote, app = connected
    before = dict(remote.pairs[0])
    original_lock = app.state.astro_alert_service.pair_lock(before["name"])
    assert original_lock is app.state.astro_alert_service.pair_lock(before["name"])
    original_update = remote.update_pair

    async def checked_update(pair):
        assert original_lock.locked()
        return await original_update(pair)

    remote.update_pair = checked_update
    with caplog.at_level("INFO", logger="astro_card_update"):
        response = client.post("/api/astro/instrument/card/update", json={"route": route(),
            "open_spread_pct": 0.7, "close_spread_pct": 0.1,
            "expected_open_position": before["openPosition"], "expected_close_position": before["closePosition"]})
    assert response.status_code == 200
    assert response.json()["status"] == "updated"
    assert "→" in response.json()["message"]
    assert remote.updated == [{**before, "openPosition": "0.007000", "closePosition": "0.001000"}]
    assert "astro_card_update pair=BTC before=" in caplog.text


@pytest.mark.parametrize("field", ["expected_open_position", "expected_close_position"])
def test_conflicting_update_is_rejected(connected, field):
    client, remote, _ = connected
    response = client.post("/api/astro/instrument/card/update", json={"route": route(),
        "open_spread_pct": 1, "close_spread_pct": 0, field: "0.123456"})
    assert response.status_code == 409
    assert response.json()["detail"] == "卡片已被修改，请刷新"
    assert not remote.updated


@pytest.mark.parametrize("count,status", [(0, 404), (2, 409)])
def test_update_requires_unique_card(connected, count, status):
    client, remote, _ = connected
    remote.pairs *= count
    assert client.post("/api/astro/instrument/card/update", json={"route": route(),
        "open_spread_pct": 1, "close_spread_pct": 0}).status_code == status
    assert not remote.updated


def test_new_endpoints_require_authentication():
    with TestClient(instrument_app(dashboard_password="password")) as client:
        for endpoint in ("lookup", "update"):
            assert client.post(f"/api/astro/instrument/card/{endpoint}", json={}).status_code == 401


@pytest.mark.parametrize("dry_run,enabled", [(True, True), (False, False)])
def test_update_respects_write_switches(dry_run, enabled):
    with TestClient(instrument_app(astro_dry_run_only=dry_run, astro_manual_card_create=enabled)) as client:
        response = client.post("/api/astro/instrument/card/update", json={"route": route(), "open_spread_pct": 1, "close_spread_pct": 0})
        assert response.status_code == 200
        assert response.json()["status"] == "disabled"


def test_ratio_preview_lookup_and_update_use_inverse_mapping(connected):
    client, remote, app = connected
    markets = app.state.snapshot_store.get_all_markets()
    markets[1] = markets[1].model_copy(update={"symbol": "ETHUSDT", "base": "ETH", "raw_symbol": "ETHUSDT"})
    app.state.snapshot_store.set_all_markets(markets)
    _install_exact_books(app)
    selected = {**route(), "buy_raw_symbol": "BTC-USDT-SWAP", "sell_raw_symbol": "ETHUSDT", "comparison_kind": "custom_cross_asset"}
    preview_response = client.post("/api/astro/instrument/preview", json={**selected, "open_spread_pct": 1.2, "close_spread_pct": 0.3})
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["pair"]["type"] == "FR"
    assert preview["pair"]["openPosition"] == _ratio_position(1.2)
    remote.pairs = [preview["pair"]]
    lookup = client.post("/api/astro/instrument/card/lookup", json=selected).json()
    assert lookup["count"] == 1
    assert lookup["open_spread_pct"] == pytest.approx(1.2, abs=0.0001)
    response = client.post("/api/astro/instrument/card/update", json={"route": selected, "open_spread_pct": 2, "close_spread_pct": 0.5})
    assert response.status_code == 200
    assert remote.updated[0]["openPosition"] == _ratio_position(2)
    assert remote.updated[0]["closePosition"] == _ratio_position(0.5)


def test_lookup_and_update_do_not_match_other_hyperliquid_dex(connected, monkeypatch):
    client, remote, app = connected
    monkeypatch.setattr(app.state.astro_hl_gc_catalog, "supports_opportunity", AsyncMock(return_value=False))
    markets = app.state.snapshot_store.get_all_markets()
    markets[0] = markets[0].model_copy(update={"exchange": "hyperliquid", "raw_symbol": "xyz:BTC", "dex": "xyz"})
    app.state.snapshot_store.set_all_markets(markets)
    _install_exact_books(app)
    selected = {**route(), "buy_exchange": "hyperliquid", "buy_raw_symbol": "xyz:BTC", "buy_dex": "xyz"}
    preview = client.post("/api/astro/instrument/preview", json=selected).json()
    assert preview["can_submit"]
    remote.pairs = [{**preview["pair"], "aHlDex": "abc"}]
    assert client.post("/api/astro/instrument/card/lookup", json=selected).json()["count"] == 0
    response = client.post("/api/astro/instrument/card/update", json={"route": selected, "open_spread_pct": 1, "close_spread_pct": 0})
    assert response.status_code == 404
    assert not remote.updated
