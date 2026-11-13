import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.db.auth import AuthStore
from app.db.database import connect_database
from app.db.repositories import SettingsRepository
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.alert import AlertRule
from app.models.settings import BigSpreadMute, RiskSettings
from app.services.alert_engine import AlertEngine
from app.services.alert_reject import AlertRejectLogger
from app.services.big_spread_alert import BigSpreadAlertEngine, big_spread_is_muted
from auth_helpers import TEST_TOTP_SECRET, seed_sessions, session_headers
from test_big_spread_alert import capture_rejects, item, loop_app
from app import main


def mute(**updates):
    return BigSpreadMute(symbol="imx-usdt", buy_exchange="BINANCE", sell_exchange="gate", **updates)


def test_mute_suppresses_fast_alert_and_resets_hits_until_expiry():
    now = datetime.now(UTC)
    settings = RiskSettings(big_spread_alert_mutes=[mute(until=now + timedelta(seconds=10))])
    engine = BigSpreadAlertEngine()
    assert not engine.evaluate([item(now=now)], [], now=now)
    assert not engine.evaluate([item(now=now)], [], now=now, risk_settings=settings)
    expired = now + timedelta(seconds=10)
    assert not engine.evaluate([item(now=expired)], [], now=expired, risk_settings=settings)
    assert engine.evaluate([item(now=expired)], [], now=expired, risk_settings=settings)


@pytest.mark.parametrize("buy,sell,both,expected", [
    ("binance", "gate", False, True),
    ("gate", "binance", False, False),
    ("gate", "binance", True, True),
    ("*", "gate", False, True),
    ("binance", "*", False, True),
    ("*", "*", False, True),
    ("*", "binance", False, False),
    ("*", "binance", True, True),
    ("okx", "gate", True, False),
])
def test_exact_direction_wildcards_and_reverse(buy, sell, both, expected):
    settings = RiskSettings(big_spread_alert_mutes=[BigSpreadMute(
        symbol="IMX/USDT", buy_exchange=buy, sell_exchange=sell, both_directions=both,
    )])
    assert big_spread_is_muted(item(symbol="imx_usdt"), settings, datetime.now(UTC)) is expected
    assert not big_spread_is_muted(item(symbol="OTHERUSDT"), settings, datetime.now(UTC))


def test_defaults_normalization_and_old_risk_data():
    value = mute()
    assert value.symbol == "IMXUSDT" and value.buy_exchange == "binance"
    assert value.both_directions and value.until is None and value.note == ""
    assert value.created_at.tzinfo is UTC
    assert RiskSettings.model_validate({"min_volume_24h_usdt": 10}).big_spread_alert_mutes == []
    assert mute(until=datetime(2000, 1, 1)).until.tzinfo is UTC
    assert BigSpreadMute(symbol="xyz:ANT_USDT", buy_exchange="hyperliquid", sell_exchange="gate").symbol == "XYZ:ANTUSDT"


def test_ordinary_rule_and_reject_diagnostics_still_run(monkeypatch):
    now = datetime.now(UTC)
    settings = RiskSettings(big_spread_alert_hits=1, big_spread_alert_mutes=[mute()])
    candidate = item(now=now)
    rule = AlertRule(name="ordinary", consecutive_hits=1, excluded_risk_labels=[])
    engine = AlertEngine()
    matches = engine.evaluate([candidate], [rule], now=now, risk_settings=settings)
    assert len(matches) == 1
    assert not BigSpreadAlertEngine().evaluate([candidate], [rule], now=now, risk_settings=settings)
    rows = capture_rejects(monkeypatch)
    AlertRejectLogger().log_round([candidate], [rule], settings, engine, matches, now)
    assert rows[0]["reasons"] == [{"rule_id": "big_spread", "reasons": ["big_spread_muted"]}]
    assert "buy_raw_symbol" in rows[0] and "buy_dex" in rows[0]


@pytest.mark.asyncio
async def test_loop_mute_only_blocks_fast_delivery_and_keeps_reject_log(monkeypatch):
    rows = capture_rejects(monkeypatch)
    app, stop = loop_app(item(), [AlertRule(name="ordinary", consecutive_hits=1, excluded_risk_labels=[])],
                         RiskSettings(big_spread_alert_hits=1, big_spread_alert_mutes=[mute()]))
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    assert len(app.state.feishu_notifier.sent_texts) == 1
    assert app.state.alert_event_repo.events[0].rule_id != "big_spread"
    assert any("big_spread_muted" in detail["reasons"] for detail in rows[0]["reasons"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mute_read", [1, 2])
async def test_inflight_match_rechecks_mutes_before_card_processing_and_sending(mute_read):
    normal = RiskSettings(big_spread_alert_hits=1)
    muted = RiskSettings(big_spread_alert_hits=1, big_spread_alert_mutes=[mute()])
    app, stop = loop_app(item())
    app.state.settings_repo.get_risk_settings = AsyncMock(side_effect=[normal] * mute_read + [muted] * 5)
    await asyncio.wait_for(main._run_alert_loop(app, .001, stop), 2)
    assert not app.state.feishu_notifier.sent_texts
    assert app.state.alert_event_repo.events[0].status == "muted"
    assert "big_spread_muted" in app.state.alert_event_repo.events[0].message


@pytest.fixture
async def repo():
    db = await connect_database(":memory:")
    await initialize_schema(db)
    try:
        yield SettingsRepository(db)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_read_and_save_clean_expired_mutes_and_persist(repo):
    old = mute(until=datetime.now(UTC) - timedelta(seconds=1))
    payload = {"big_spread_alert_mutes": [old.model_dump(mode="json"), mute().model_dump(mode="json")]}
    await repo.db.execute("INSERT INTO app_settings (key, payload) VALUES ('risk', ?)", (json.dumps(payload),))
    await repo.db.commit()
    assert len((await repo.get_risk_settings()).big_spread_alert_mutes) == 1
    cursor = await repo.db.execute("SELECT payload FROM app_settings WHERE key = 'risk'")
    assert len(json.loads((await cursor.fetchone())["payload"])["big_spread_alert_mutes"]) == 1
    settings = await repo.get_risk_settings()
    settings.big_spread_alert_mutes.append(old)
    assert len((await repo.set_risk_settings(settings)).big_spread_alert_mutes) == 1
    assert len((await SettingsRepository(repo.db).get_risk_settings()).big_spread_alert_mutes) == 1


@pytest.mark.asyncio
async def test_concurrent_mutations_risk_save_and_delete_do_not_lose_updates(repo):
    other = SettingsRepository(repo.db)
    values = [BigSpreadMute(symbol=f"COIN{i}", buy_exchange="*", sell_exchange="gate") for i in range(30)]
    await asyncio.gather(*(other.mutate_big_spread_mutes(value, mute=value) for value in values),
                         repo.set_risk_settings(RiskSettings(big_spread_alert_pct=5)))
    settings = await repo.get_risk_settings()
    assert len(settings.big_spread_alert_mutes) == 30 and settings.big_spread_alert_pct == 5
    await asyncio.gather(*(repo.mutate_big_spread_mutes(value) for value in values[:15]),
                         other.set_risk_settings(RiskSettings(big_spread_alert_pct=6)))
    settings = await repo.get_risk_settings()
    assert {value.symbol for value in settings.big_spread_alert_mutes} == {value.symbol for value in values[15:]}
    assert settings.big_spread_alert_pct == 6


@pytest.mark.asyncio
async def test_capacity_upsert_and_expiry_reclaim(repo):
    values = [BigSpreadMute(symbol=f"COIN{i}", buy_exchange="*", sell_exchange="*") for i in range(500)]
    await repo.set_risk_settings(RiskSettings(big_spread_alert_mutes=values))
    replacement = values[0].model_copy(update={"note": "updated"})
    assert len(await repo.mutate_big_spread_mutes(replacement, mute=replacement)) == 500
    with pytest.raises(ValidationError):
        await repo.mutate_big_spread_mutes(mute(), mute=mute())
    assert len((await repo.get_risk_settings()).big_spread_alert_mutes) == 500
    values[0].until = datetime.now(UTC) - timedelta(seconds=1)
    await repo.set_risk_settings(RiskSettings(big_spread_alert_mutes=values))
    assert len(await repo.mutate_big_spread_mutes(mute(), mute=mute())) == 500
    with pytest.raises(ValidationError):
        RiskSettings(big_spread_alert_mutes=[mute()] * 501)


@pytest.fixture
async def client(repo):
    app = create_app(settings=Settings(
        dashboard_totp_secret=TEST_TOTP_SECRET, dashboard_password="route-permissions-test",
        database_url="sqlite:///:memory:",
    ), start_background_workers=False)
    app.state.settings_repo = repo
    store = app.state.auth_store = await AuthStore.open(":memory:")
    await seed_sessions(store)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_mute_api_authentication(client, method):
    path = "/api/alerts/big-spread-mutes"
    payload = {"symbol": "imx-usdt", "buy_exchange": "binance", "sell_exchange": "gate"}
    for headers in ({}, session_headers("wrong"), {"X-Dashboard-Password": "route-permissions-test"}):
        response = await client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 401
    response = await client.request(method, path, headers=session_headers("route-permissions-test"), json=payload)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_mute_api_lifecycle_duration_and_risk_save(client):
    path, headers = "/api/alerts/big-spread-mutes", session_headers("route-permissions-test")
    route = {"symbol": " imx-usdt ", "buy_exchange": " BINANCE ", "sell_exchange": "gate"}
    response = await client.post(path, headers=headers, json={**route, "duration_hours": 24, "note": "规格不同"})
    assert response.status_code == 200
    value = response.json()[0]
    assert value["symbol"] == "IMXUSDT" and value["both_directions"] and value["note"] == "规格不同"
    assert 86390 < (datetime.fromisoformat(value["until"]) - datetime.now(UTC)).total_seconds() <= 86400
    assert (await client.get(path, headers=headers)).json() == [value]
    assert (await client.put("/api/settings/risk", headers=headers, json={"big_spread_alert_pct": 5})).status_code == 200
    assert (await client.get(path, headers=headers)).json() == [value]
    response = await client.post(path, headers=headers, json={**route, "until": None, "both_directions": False})
    assert len(response.json()) == 1 and response.json()[0]["until"] is None
    response = await client.request("DELETE", path, headers=headers, json=route)
    assert response.status_code == 200 and response.json() == []
    assert (await client.get(path, headers=headers)).json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [
    {"symbol": "-_/"}, {"symbol": "*"}, {"symbol": "IMX USDT"}, {"buy_exchange": ""},
    {"sell_exchange": "g*"}, {"duration_hours": 0}, {"duration_hours": -1},
    {"duration_hours": 1e300}, {"duration_hours": 24, "until": "2099-01-01T00:00:00Z"},
    {"until": "2000-01-01T00:00:00Z"}, {"until": "invalid"}, {"note": "x" * 1001},
    {"both_directions": "invalid"}, {"unexpected": True},
])
async def test_mute_api_validation(client, updates):
    response = await client.post("/api/alerts/big-spread-mutes", headers=session_headers("route-permissions-test"),
                                 json={"symbol": "IMXUSDT", "buy_exchange": "*", "sell_exchange": "gate", **updates})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_api_capacity_and_delete_exact_key(client, repo):
    headers = session_headers("route-permissions-test")
    await repo.set_risk_settings(RiskSettings(big_spread_alert_mutes=[mute()] * 500))
    path = "/api/alerts/big-spread-mutes"
    route = {"symbol": "OTHER", "buy_exchange": "*", "sell_exchange": "*"}
    assert (await client.post(path, headers=headers, json=route)).status_code == 422
    reverse = {"symbol": "IMXUSDT", "buy_exchange": "gate", "sell_exchange": "binance"}
    assert len((await client.request("DELETE", path, headers=headers, json=reverse)).json()) == 500
    assert (await client.request("DELETE", path, headers=headers, json={"symbol": "IMX-USDT", "buy_exchange": "binance", "sell_exchange": "gate"})).json() == []
