import asyncio
import logging
import re
import sqlite3
import time
from contextlib import closing

import pytest
from auth_helpers import TEST_TOTP_SECRET
from test_mobile_api import mobile_app  # noqa: F401 - shared production-auth app fixture

from app.core.totp import totp_at


PINS = "/api/mobile/pins"


async def login(client, scope, counter):
    response = await client.post("/api/auth/login", json={
        "password": "fixture", "code": totp_at(TEST_TOTP_SECRET, counter), "scope": scope,
    })
    assert response.status_code == 200, response.text


async def pin(client, card_id, pinned=True):
    return await client.post(PINS, json={"card_id": card_id, "pinned": pinned})


@pytest.fixture
async def signed_in(mobile_app):
    app, client = mobile_app
    await login(client, "full", int(time.time() // 30))
    return app, client


async def test_global_pin_lifecycle_and_session_permissions(mobile_app):
    _, client = mobile_app
    card_id = "Ab-_" + "9" * 60
    client.headers["X-Api-Token"] = "a" * 64
    assert (await client.get(PINS)).status_code == 401
    assert (await pin(client, card_id)).status_code == 401
    del client.headers["X-Api-Token"]

    counter = int(time.time() // 30)
    await login(client, "full", counter)
    assert (await client.get(PINS)).json() == {"pins": {}}
    start = time.time()
    first = await pin(client, card_id)
    assert first.status_code == 200
    timestamp = first.json()["pins"][card_id]
    assert isinstance(timestamp, float) and start <= timestamp <= time.time()
    second = (await pin(client, "B")).json()["pins"]
    assert second == {card_id: timestamp, "B": second["B"]}
    assert (await client.post("/api/auth/logout")).status_code == 200

    await login(client, "mobile_readonly", counter + 1)
    assert (await client.get(PINS)).json() == {"pins": second}
    refreshed = await pin(client, card_id)
    assert refreshed.status_code == 200
    latest = refreshed.json()["pins"]
    assert latest[card_id] > max(second.values())
    assert latest["B"] == second["B"]
    assert (await client.post("/api/auth/sessions/revoke-all")).status_code == 403
    for _ in range(2):
        assert (await pin(client, card_id, False)).json() == {"pins": {"B": second["B"]}}


async def test_invalid_pins_return_chinese_errors_without_echo(signed_in):
    _, client = signed_in
    for invalid in ("", "x" * 65, "private/id", "中文输入", "private\n", 123456, None):
        response = await pin(client, invalid)
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert isinstance(detail, str) and re.search(r"[\u4e00-\u9fff]", detail)
        if invalid:
            assert str(invalid) not in detail
    assert (await pin(client, "valid", "false")).status_code == 422
    assert (await client.get(PINS)).json() == {"pins": {}}


async def test_concurrent_inserts_at_limit_allow_refresh_and_removal(signed_in, tmp_path):
    _, client = signed_in
    with closing(sqlite3.connect(tmp_path / "radar.db")) as db, db:
        db.executemany("INSERT INTO mobile_card_pins VALUES (?, 1.0)",
                       [(str(number),) for number in range(199)])
    responses = await asyncio.gather(pin(client, "left"), pin(client, "right"))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert next(r for r in responses if r.status_code == 409).json() == {"detail": "置顶数量已达上限"}
    full = (await pin(client, "0")).json()["pins"]
    assert len(full) == 200 and full["0"] > 1.0
    assert len((await pin(client, "0", False)).json()["pins"]) == 199
    assert len((await pin(client, "new")).json()["pins"]) == 200


async def test_summary_matches_by_id_preserves_order_and_absent_pins(signed_in):
    app, client = signed_in
    pairs = [{"name": "BTC", "id": "A"}, {"name": "BTC", "id": "a"}, {"name": "BTC"}]
    app.state.astro_client.list_pairs_cached.return_value = pairs
    assert (await pin(client, "a")).status_code == 200
    pins = (await pin(client, "absent")).json()["pins"]
    cards = (await client.get("/api/mobile/summary")).json()["cards"]
    assert [(c["id"], c["pinned_at"]) for c in cards] == [("A", None), ("a", pins["a"]), (None, None)]
    assert (await client.get(PINS)).json() == {"pins": pins}


async def test_summary_warns_and_returns_cards_when_pins_unavailable(signed_in, tmp_path, caplog):
    app, client = signed_in
    app.state.astro_client.list_pairs_cached.return_value = [{"name": "BTC", "id": "A"}]
    with closing(sqlite3.connect(tmp_path / "radar.db")) as db, db:
        db.execute("DROP TABLE mobile_card_pins")
    with caplog.at_level(logging.WARNING):
        response = await client.get("/api/mobile/summary")
    assert response.status_code == 200
    assert [(c["id"], c["pinned_at"]) for c in response.json()["cards"]] == [("A", None)]
    assert any(r.levelno == logging.WARNING and re.search("pin|置顶", r.getMessage(), re.I)
               for r in caplog.records)
