import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.db.repositories import SettingsRepository
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.settings import FloatingWatchMutation, FloatingWatchSettings


@pytest.mark.asyncio
async def test_floating_watch_repository_round_trip() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SettingsRepository(db)

        assert await repo.get_floating_watch_settings() == FloatingWatchSettings()

        await asyncio.gather(
            repo.mutate_floating_watch_settings(
                FloatingWatchMutation(action="add", item_type="symbol", value="btc")
            ),
            repo.mutate_floating_watch_settings(
                FloatingWatchMutation(action="add", item_type="symbol", value="eth/usdt")
            ),
            repo.mutate_floating_watch_settings(
                FloatingWatchMutation(action="add", item_type="pair", value=" pair-a ")
            ),
        )
        loaded = await repo.get_floating_watch_settings()

        assert loaded.symbols == ["BTCUSDT", "ETHUSDT"]
        assert loaded.pair_ids == ["pair-a"]
    finally:
        await db.close()


def test_floating_watch_api_syncs_devices_and_protects_writes() -> None:
    app = create_app(
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
        )
    )

    with TestClient(app) as client:
        assert client.get("/api/settings/floating-watch").json() == {
            "symbols": [],
            "pair_ids": [],
        }

        unauthorized = client.post(
            "/api/settings/floating-watch/items",
            json={"action": "add", "item_type": "symbol", "value": "btc"},
        )
        assert unauthorized.status_code == 401

        saved = client.post(
            "/api/settings/floating-watch/items",
            headers={"X-Dashboard-Password": "secret"},
            json={"action": "add", "item_type": "symbol", "value": "btc"},
        )
        assert saved.status_code == 200
        assert saved.json() == {
            "symbols": ["BTCUSDT"],
            "pair_ids": [],
        }

        pair_saved = client.post(
            "/api/settings/floating-watch/items",
            headers={"X-Dashboard-Password": "secret"},
            json={"action": "add", "item_type": "pair", "value": "pair-a"},
        )
        assert pair_saved.json()["pair_ids"] == ["pair-a"]

        second_device = client.get("/api/settings/floating-watch")
        assert second_device.json() == pair_saved.json()
