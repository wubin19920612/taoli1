import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from test_mobile_card_control import (
    CONTROL, FREE, OPS, POSTS, clock, enabled, login, mobile_app, other_client, phone, unlock_code,
)

ADMIN = "/api/settings/mobile-card-control"
KILL = OPS + "/kill"
STATUS = OPS + "/status"
TOKEN = {"X-Api-Token": "a" * 64}


async def test_default_and_kill_from_locked_readonly_session(enabled, clock):
    app, client = enabled
    send = app.state.mobile_push.send_urgent = AsyncMock()
    status = (await client.get(STATUS)).json()
    assert status["enabled"] and status["runtime_enabled"] and status["env_enabled"]
    assert status["changed_at"] is None and status["changed_by"] is None
    assert status["unlocked_until"] is None

    headers = {"User-Agent": "Chrome/100 Android"}
    results = await asyncio.gather(*(client.post(KILL, headers=headers) for _ in range(2)))
    assert all(r.status_code == 200 and r.json() == {"enabled": False} for r in results)
    status = (await client.get(STATUS)).json()
    assert status["enabled"] is False and status["runtime_enabled"] is False
    assert status["changed_by"] == "mobile:Chrome / Android" and status["changed_at"] == clock.now
    for path, payload in POSTS.items():
        assert (await client.post(path, json=payload)).status_code == 503, path
    app.state.astro_client.update_pair.assert_not_awaited()
    event, = await app.state.mobile_alert_repo.list(0, 10)
    assert (event["kind"], event["level"], event["title"]) == ("card_control", "urgent", "手机改卡已紧急停用")
    assert status["changed_by"] in event["body"]
    assert datetime.fromtimestamp(clock.now, UTC).strftime("%Y-%m-%d") in event["body"]
    send.assert_awaited_once()
    assert send.await_args.kwargs["body"] == event["body"]


async def test_kill_clears_all_previous_unlocks(enabled, other_client, clock):
    _, client = enabled
    assert (await unlock_code(client, clock.code())).status_code == 200
    clock.now += 60
    assert (await login(other_client, clock.code(), "full")).status_code == 200
    assert (await unlock_code(other_client, clock.code(1))).status_code == 200
    assert (await other_client.post(KILL)).status_code == 200
    assert (await other_client.put(ADMIN, json={"enabled": True})).json()["effective"] is True
    for session in (client, other_client):
        assert (await session.get(STATUS)).json()["unlocked_until"] is None
        assert (await session.post(OPS + "/settings", json={"unlock_minutes": 5})).status_code == 403


async def test_locked_card_rechecks_persisted_switch(enabled, monkeypatch):
    app, client = enabled
    pair_lock = app.state.astro_alert_service.pair_lock

    @asynccontextmanager
    async def kill_after_acquiring(name):
        async with pair_lock(name):
            assert (await client.post(KILL)).status_code == 200
            yield

    monkeypatch.setattr(app.state.astro_alert_service, "pair_lock", kill_after_acquiring)
    assert (await client.post(CONTROL, json=FREE)).status_code == 503
    app.state.astro_client.update_pair.assert_not_awaited()


async def test_switch_read_error_fails_closed(enabled, caplog):
    app, client = enabled
    db = app.state.mobile_card_control.repo.db
    await db.execute("DROP TABLE IF EXISTS mobile_ops_switch")
    await db.commit()
    with caplog.at_level(logging.WARNING):
        status = (await client.get(STATUS)).json()
        assert status["runtime_enabled"] is False and status["enabled"] is False
        assert (await client.post(CONTROL, json=FREE)).status_code == 503
    assert any(r.levelno == logging.WARNING and "开关" in r.getMessage() for r in caplog.records)
    app.state.astro_client.update_pair.assert_not_awaited()


async def test_web_permissions_audit_and_push(enabled, other_client, clock):
    app, client = enabled
    send = app.state.mobile_push.send_urgent = AsyncMock()
    assert (await client.get(ADMIN)).status_code == 403
    assert (await client.put(ADMIN, json={"enabled": True})).status_code == 403
    assert (await other_client.post(KILL, headers=TOKEN)).status_code == 401
    assert (await other_client.get(ADMIN)).status_code == 401
    assert (await other_client.get(ADMIN, headers=TOKEN)).json() == {
        "env_enabled": True, "dry_run": False, "runtime_enabled": True,
        "effective": True, "changed_at": None, "changed_by": None,
    }
    assert (await login(other_client, clock.code(), "full")).status_code == 200
    for value in (False, False, True):
        response = await other_client.put(ADMIN, json={"enabled": value})
        assert response.status_code == 200
        state = (await other_client.get(ADMIN)).json()
        assert response.json() == state
        assert state["effective"] is value and state["runtime_enabled"] is value
        assert (state["changed_by"], state["changed_at"]) == ("web", clock.now)
    events = await app.state.mobile_alert_repo.list(0, 10)
    titles = ["手机改卡已在网页停用", "手机改卡已在网页重新打开"]
    assert [e["title"] for e in reversed(events)] == titles
    assert [call.kwargs["title"] for call in send.await_args_list] == titles


@pytest.mark.parametrize("env,dry", [(False, False), (True, True)])
async def test_env_ceiling_and_kill_session_quota(enabled, other_client, clock, env, dry):
    app, client = enabled
    app.state.settings = replace(app.state.settings, mobile_card_control_enabled=env, astro_dry_run_only=dry)
    for _ in range(10):
        assert (await client.post(KILL)).json() == {"enabled": False}
    assert (await client.post(KILL)).status_code == 429
    state = (await other_client.put(ADMIN, json={"enabled": True}, headers=TOKEN)).json()
    assert state["runtime_enabled"] and state["effective"] is False
    assert state["env_enabled"] is env and state["dry_run"] is dry
    assert (await login(other_client, clock.code(), "full")).status_code == 200
    assert (await other_client.post(KILL)).status_code == 200
    clock.now += 60
    assert (await client.post(KILL)).status_code == 200
