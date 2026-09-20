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


def test_index_component_auto_watch_switch_syncs_server_watchlist() -> None:
    app = create_app(settings=Settings(dashboard_password="secret", database_url="sqlite:///:memory:"))
    with TestClient(app) as client:
        async def astro_pairs():
            return [{"name": "VANRY", "type": "FF", "status": False, "aExPosition": "1"}]

        app.state.astro_client.list_pairs = astro_pairs
        assert client.get("/api/index-components/auto-watch").status_code == 401
        headers = {"X-Dashboard-Password": "secret"}
        assert client.get("/api/index-components/auto-watch", headers=headers).json() == {
            "enabled": False, "items": [], "error": None
        }
        unauthorized = client.put("/api/index-components/auto-watch", json={"enabled": True})
        assert unauthorized.status_code == 401
        client.post(
            "/api/settings/floating-watch/items", headers=headers,
            json={"action": "add", "item_type": "symbol", "value": "ttwo"},
        )

        enabled = client.put(
            "/api/index-components/auto-watch", headers=headers, json={"enabled": True}
        )
        assert enabled.status_code == 200
        assert {(item["source"], item["symbol"]) for item in enabled.json()["items"]} == {
            ("floating_symbols", "TTWOUSDT"), ("positions", "VANRYUSDT")
        }
        assert client.get("/api/index-components/auto-watch", headers=headers).json()["enabled"] is True
        assert client.post("/api/index-components/auto-watch/sync").status_code == 401

        disabled = client.put(
            "/api/index-components/auto-watch", headers=headers, json={"enabled": False}
        )
        assert disabled.json()["items"] == []
