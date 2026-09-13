from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.pair_spread import MAX_PAIR_SPREAD_PRESETS, PairSpreadPreset
from app.services.pair_spread_presets import PairSpreadPresetRepository


def _preset(
    preset_id: str,
    saved_at: datetime,
    *,
    hours: int = 4,
) -> PairSpreadPreset:
    return PairSpreadPreset(
        id=preset_id,
        leg1_exchange="binance",
        leg1_market_type="future",
        leg1_symbol=f"{preset_id}USDT",
        leg2_exchange="okx",
        leg2_market_type="future",
        leg2_symbol=f"{preset_id}USDT",
        leg2_multiplier=1,
        hours=hours,
        interval_seconds=60,
        saved_at=saved_at,
    )


async def test_repository_merges_by_saved_at_and_prunes_old_presets() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = PairSpreadPresetRepository(db)
    now = datetime(2026, 9, 12, tzinfo=UTC)

    await repo.upsert(_preset("BTC", now, hours=12))
    await repo.merge([_preset("BTC", now - timedelta(hours=1), hours=2)])
    unchanged = await repo.get("BTC")
    assert unchanged is not None
    assert unchanged.hours == 12

    await repo.merge(
        [
            *[
                _preset(f"P{index:02d}", now + timedelta(minutes=index))
                for index in range(MAX_PAIR_SPREAD_PRESETS + 3)
            ],
        ]
    )

    presets = await repo.list()
    assert len(presets) == MAX_PAIR_SPREAD_PRESETS
    assert [preset.id for preset in presets[:3]] == ["P26", "P25", "P24"]
    assert "P00" not in {preset.id for preset in presets}

    await repo.upsert(_preset("P26", now + timedelta(days=1), hours=24))
    updated = await repo.get("P26")
    assert updated is not None
    assert updated.hours == 24

    await repo.delete("P26")
    assert await repo.get("P26") is None
    await db.close()


def test_pair_spread_preset_api_syncs_devices_and_protects_writes() -> None:
    app = create_app(
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
        )
    )
    headers = {"X-Dashboard-Password": "secret"}
    now = datetime(2026, 9, 12, tzinfo=UTC)
    payload = _preset("BTC", now, hours=12).model_dump(mode="json", by_alias=True)

    with TestClient(app) as client:
        assert client.get("/api/pair-spread/presets").json() == []

        unauthorized = client.post(
            "/api/pair-spread/presets/merge",
            json={"presets": [payload]},
        )
        assert unauthorized.status_code == 401

        merged = client.post(
            "/api/pair-spread/presets/merge",
            headers=headers,
            json={"presets": [payload]},
        )
        assert merged.status_code == 200
        assert merged.json()[0]["id"] == "BTC"
        assert merged.json()[0]["intervalSeconds"] == 60

        # A second device reads the same server-side list without browser storage.
        second_device = client.get("/api/pair-spread/presets")
        assert second_device.status_code == 200
        assert second_device.json()[0]["hours"] == 12

        older_payload = _preset("BTC", now - timedelta(days=1), hours=2).model_dump(
            mode="json",
            by_alias=True,
        )
        stale_update = client.put(
            "/api/pair-spread/presets/BTC",
            headers=headers,
            json=older_payload,
        )
        assert stale_update.status_code == 200
        assert stale_update.json()["hours"] == 12

        unauthorized_delete = client.delete("/api/pair-spread/presets/BTC")
        assert unauthorized_delete.status_code == 401
        deleted = client.delete("/api/pair-spread/presets/BTC", headers=headers)
        assert deleted.status_code == 200
        assert client.get("/api/pair-spread/presets").json() == []
