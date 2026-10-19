import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
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
        leg1_raw_symbol=f"{preset_id}USDT",
        leg1_price_multiplier=1,
        leg1_contract_size_multiplier=0.01,
        leg2_exchange="okx",
        leg2_market_type="future",
        leg2_symbol=f"{preset_id}USDT",
        leg2_raw_symbol=f"{preset_id}-USDT-SWAP",
        leg2_price_multiplier=10,
        leg2_contract_size_multiplier=1,
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
        assert merged.json()[0]["leg1_raw_symbol"] == "BTCUSDT"
        assert merged.json()[0]["leg1_contract_size_multiplier"] == 0.01
        assert merged.json()[0]["leg2_raw_symbol"] == "BTC-USDT-SWAP"
        assert merged.json()[0]["leg2_price_multiplier"] == 10

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


def test_file_backed_presets_can_save_while_collector_has_a_stale_snapshot(tmp_path) -> None:
    path = tmp_path / "radar.db"
    with sqlite3.connect(path) as other:
        other.execute("CREATE TABLE snapshot_probe (value INTEGER)")
        other.execute("INSERT INTO snapshot_probe VALUES (1)")
    app = create_app(
        settings=Settings(dashboard_password="secret", database_url=f"sqlite:///{path}"),
        start_background_workers=False,
    )
    preset = _preset("hyperliquid-io-oai-rh-openai", datetime.now(UTC)).model_copy(update={
        "leg1_exchange": "hyperliquid", "leg1_symbol": "OAIUSDT",
        "leg1_raw_symbol": "io:OAI", "leg1_dex": "io",
        "leg2_exchange": "rh-lighter", "leg2_symbol": "OPENAIUSDT",
        "leg2_raw_symbol": "OPENAI", "leg2_price_multiplier": 1,
    })
    with TestClient(app) as client:
        async def hold_snapshot():
            await app.state.db.execute("BEGIN")
            async with app.state.db.execute("SELECT * FROM snapshot_probe") as cursor:
                await cursor.fetchall()

        client.portal.call(hold_snapshot)
        try:
            with sqlite3.connect(path) as other:
                other.execute("INSERT INTO snapshot_probe VALUES (2)")
            response = client.put(
                f"/api/pair-spread/presets/{preset.id}",
                headers={"X-Dashboard-Password": "secret"},
                json=preset.model_dump(mode="json", by_alias=True),
            )
            assert response.status_code == 200
            assert response.json()["leg1_raw_symbol"] == "io:OAI"
            assert response.json()["leg1_dex"] == "io"
            assert response.json()["leg2_exchange"] == "rh-lighter"
            assert app.state.db.in_transaction  # Saving must not commit the collector's work.
            assert client.get("/api/pair-spread/presets").json()[0]["id"] == preset.id
        finally:
            client.portal.call(app.state.db.rollback)


@pytest.mark.parametrize("operation", ["upsert", "merge", "delete"])
async def test_failed_preset_writes_roll_back_and_release_the_writer(tmp_path, operation) -> None:
    db = await connect_database(str(tmp_path / "radar.db"))
    other = None
    try:
        await initialize_schema(db)
        repo = PairSpreadPresetRepository(db)
        original = _preset("BTC", datetime.now(UTC))
        await repo.upsert(original)
        # Abort after a successful DML statement: no partial save/delete may survive.
        await db.execute(
            "CREATE TEMP TRIGGER abort_preset_delete BEFORE DELETE ON pair_spread_presets "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
        await db.commit()
        with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
            if operation == "delete":
                await repo.delete("BTC")
            else:
                await db.execute(
                    "CREATE TEMP TRIGGER abort_preset_insert AFTER INSERT ON pair_spread_presets "
                    "BEGIN DELETE FROM pair_spread_presets WHERE id = 'BTC'; END"
                )
                await db.commit()
                new = _preset("ETH", datetime.now(UTC))
                await (repo.upsert(new) if operation == "upsert" else repo.merge([new]))
        assert not db.in_transaction
        assert [item.id for item in await repo.list()] == ["BTC"]
        other = await connect_database(str(tmp_path / "radar.db"))
        await other.execute("PRAGMA busy_timeout=20")
        await other.execute("BEGIN IMMEDIATE")
        await other.rollback()
    finally:
        if other is not None:
            await other.close()
        await db.close()


def test_preset_api_reports_busy_then_recovers_without_a_restart(tmp_path) -> None:
    path = tmp_path / "radar.db"
    app = create_app(
        settings=Settings(dashboard_password="secret", database_url=f"sqlite:///{path}"),
        start_background_workers=False,
    )
    headers = {"X-Dashboard-Password": "secret"}
    payload = _preset("BTC", datetime.now(UTC)).model_dump(mode="json", by_alias=True)
    with TestClient(app) as client, sqlite3.connect(path) as blocker:
        client.portal.call(app.state.pair_spread_preset_repo.db.execute, "PRAGMA busy_timeout=20")
        blocker.execute("BEGIN IMMEDIATE")
        try:
            responses = [
                client.put("/api/pair-spread/presets/BTC", headers=headers, json=payload),
                client.post("/api/pair-spread/presets/merge", headers=headers, json={"presets": [payload]}),
                client.delete("/api/pair-spread/presets/BTC", headers=headers),
            ]
            assert [response.status_code for response in responses] == [503, 503, 503]
            assert all(response.headers["Retry-After"] == "1" for response in responses)
            assert all("稍后重试" in response.json()["detail"] for response in responses)
            assert not app.state.pair_spread_preset_repo.db.in_transaction
        finally:
            blocker.rollback()
        assert client.put("/api/pair-spread/presets/BTC", headers=headers, json=payload).status_code == 200
        assert client.get("/api/pair-spread/presets").json()[0]["id"] == "BTC"
        assert client.delete("/api/pair-spread/presets/BTC", headers=headers).status_code == 200


async def test_cancelled_save_rolls_back_before_other_requests_read(tmp_path, monkeypatch) -> None:
    db = await connect_database(str(tmp_path / "radar.db"))
    try:
        await initialize_schema(db)
        repo = PairSpreadPresetRepository(db)
        await repo.upsert(_preset("BTC", datetime.now(UTC)))
        inserted = asyncio.Event()

        async def pause_after_insert():
            inserted.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(repo, "_prune_without_commit", pause_after_insert)
        writer = asyncio.create_task(repo.upsert(_preset("ETH", datetime.now(UTC))))
        reader = None
        try:
            await asyncio.wait_for(inserted.wait(), timeout=5)
            reader = asyncio.create_task(repo.list())
            await asyncio.sleep(0)
            assert not reader.done()
        finally:
            writer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await writer
        assert reader is not None
        assert [item.id for item in await asyncio.wait_for(reader, timeout=5)] == ["BTC"]
        assert not db.in_transaction
    finally:
        await db.close()
