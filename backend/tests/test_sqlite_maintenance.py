import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import config
from app.db.database import connect_database
from app.maintenance import sqlite as maintenance

NOW = datetime(2026, 11, 2, 20, 15, tzinfo=UTC)
TABLES = [
    ("second_level_market_samples", "observed_at", 2),
    ("second_level_index_component_samples", "observed_at", 2),
    ("alert_events", "created_at", 90),
    ("oil_news_items", "published_at", 90),
    ("squeeze_hourly_candles", "event_time", 30),
]


def settings(**kwargs):
    return config.Settings(sqlite_maintenance_enabled=True, **kwargs)


async def count(db, table):
    async with db.execute(f'SELECT COUNT(*) FROM "{table}"') as cursor:
        return (await cursor.fetchone())[0]


@pytest.mark.parametrize("dry_run", [False, True])
async def test_retention_boundary_and_dry_run(tmp_path, monkeypatch, caplog, dry_run):
    path = str(tmp_path / "maintenance.db")
    db = await connect_database(path)
    statements = []
    original_connect = maintenance.aiosqlite.connect

    async def tracked_connect(path, **kwargs):
        dedicated = await original_connect(path, **kwargs)
        assert dedicated is not db
        await dedicated.set_trace_callback(statements.append)
        return dedicated

    monkeypatch.setattr(maintenance.aiosqlite, "connect", tracked_connect)
    try:
        for table, column, days in TABLES:
            await db.execute(f'CREATE TABLE "{table}" ("{column}" TEXT)')
            cutoff = NOW - timedelta(days=days)
            await db.executemany(f'INSERT INTO "{table}" VALUES (?)', [
                ((cutoff - timedelta(seconds=1)).isoformat(),),
                (cutoff.isoformat(),),
                (NOW.isoformat(),),
                ("invalid-time",),
            ])
        await db.commit()
        worker = maintenance.SQLiteMaintenance(path, settings(sqlite_maintenance_dry_run=dry_run))
        with caplog.at_level(logging.INFO):
            await worker.run_once(now=NOW)
        for table, _, _ in TABLES:
            assert await count(db, table) == (4 if dry_run else 3)
            assert f"table={table}" in caplog.text
        assert "elapsed_seconds=" in caplog.text
        assert ("would_delete=1" if dry_run else "deleted=1") in caplog.text
        assert any(sql.startswith("DELETE") for sql in statements) is (not dry_run)
        assert any("wal_checkpoint(TRUNCATE)" in sql for sql in statements) is (not dry_run)
        if dry_run:
            assert "VACUUM" not in statements
    finally:
        await db.close()


async def test_custom_retention_and_missing_schema_are_safe(tmp_path, caplog):
    path = str(tmp_path / "missing.db")
    db = await connect_database(path)
    try:
        await db.execute("CREATE TABLE alert_events (wrong_column TEXT)")
        await db.execute("CREATE TABLE oil_news_items (published_at TEXT)")
        await db.execute("INSERT INTO oil_news_items VALUES (?)", ((NOW - timedelta(days=4)).isoformat(),))
        await db.commit()
        worker = maintenance.SQLiteMaintenance(path, settings(
            sqlite_maintenance_dry_run=False, sqlite_maintenance_oil_news_items_retention_days=3,
        ))
        with caplog.at_level(logging.INFO):
            await worker.run_once(now=NOW)
        assert await count(db, "oil_news_items") == 0
        assert "missing_table" in caplog.text
        assert "missing_column" in caplog.text
    finally:
        await db.close()


async def test_batches_commit_and_release_write_lock(tmp_path, monkeypatch):
    path = str(tmp_path / "batches.db")
    writer = await connect_database(path)
    observed = []
    try:
        await writer.execute("PRAGMA busy_timeout=0")
        await writer.execute("CREATE TABLE alert_events (created_at TEXT)")
        await writer.execute("CREATE TABLE probe (id INTEGER)")
        await writer.executemany("INSERT INTO alert_events VALUES (?)", [
            ((NOW - timedelta(days=91)).isoformat(),) for _ in range(7)
        ])
        await writer.commit()
        worker = maintenance.SQLiteMaintenance(path, settings(
            sqlite_maintenance_dry_run=False, sqlite_maintenance_batch_size=2,
        ))

        async def between_batches():
            # A different connection must acquire the write lock at EVERY yield.
            await writer.execute("INSERT INTO probe VALUES (1)")
            await writer.commit()
            observed.append(await count(writer, "alert_events"))
            await asyncio.sleep(0)

        monkeypatch.setattr(worker, "_yield_between_batches", between_batches)
        await worker.run_once(now=NOW)
        assert observed == [5, 3, 1, 0]
    finally:
        await writer.close()


@pytest.mark.parametrize("free_bytes,expected", [(0, False), (1024**3, True)])
async def test_vacuum_disk_guard_and_actual_compaction(tmp_path, monkeypatch, caplog, free_bytes, expected):
    path = str(tmp_path / "vacuum.db")
    db = await connect_database(path)
    statements = []
    try:
        await db.execute("CREATE TABLE payload (id INTEGER PRIMARY KEY, body BLOB)")
        await db.executemany("INSERT INTO payload VALUES (?, zeroblob(1048576))", [(i,) for i in range(24)])
        await db.commit()
        await db.execute("DELETE FROM payload WHERE id < 20")
        await db.commit()
        await db.set_trace_callback(statements.append)
        checked_paths = []

        def disk_usage(directory):
            checked_paths.append(directory)
            return SimpleNamespace(free=free_bytes)

        monkeypatch.setattr(maintenance.shutil, "disk_usage", disk_usage)
        worker = maintenance.SQLiteMaintenance(path, settings(sqlite_maintenance_dry_run=False))
        with caplog.at_level(logging.INFO):
            assert await worker._vacuum_if_beneficial(db) is expected
        assert ("VACUUM" in statements) is expected
        assert checked_paths == [tmp_path]
        assert await count(db, "payload") == 4
        if not expected:
            assert "insufficient_disk" in caplog.text
        async with db.execute("PRAGMA integrity_check") as cursor:
            assert (await cursor.fetchone())[0] == "ok"
    finally:
        await db.close()


@pytest.mark.parametrize("enabled,workers,expected", [(False, True, False), (True, False, False), (True, True, True)])
@pytest.mark.parametrize("legacy", [True, False])
async def test_main_startup_switch(tmp_path, monkeypatch, enabled, workers, expected, legacy):
    from app import main
    from app.runtime.supervisor import TaskSupervisor

    started = {}
    recorders = []
    original_recorder = main.OpportunityHistoryRecorder

    def recorder(*args, **kwargs):
        result = original_recorder(*args, **kwargs)
        recorders.append(result)
        return result

    def start(self, name, factory, *, restart, critical):
        started[name] = factory

    monkeypatch.setattr(TaskSupervisor, "start", start)
    monkeypatch.setattr(main, "OpportunityHistoryRecorder", recorder)
    monkeypatch.setattr(main, "default_exchange_adapters", list)
    app = main.create_app(settings=config.Settings(
        database_url=f"sqlite:///{tmp_path / 'startup.db'}",
        disabled_features=config.OPTIONAL_FEATURES,
        sqlite_maintenance_enabled=enabled,
        history_legacy_vacuum_enabled=legacy,
    ), start_background_workers=workers, start_collector=workers)
    async with app.router.lifespan_context(app):
        assert ("sqlite-maintenance" in started) is expected
        if workers:
            assert len(recorders) == 1
            assert recorders[0].legacy_vacuum_enabled is (legacy and not enabled)


async def test_schedule_only_runs_once_in_low_peak_window(tmp_path, monkeypatch):
    worker = maintenance.SQLiteMaintenance(str(tmp_path / "schedule.db"), settings())
    stop = asyncio.Event()
    times = iter([NOW.replace(hour=19), NOW, NOW + timedelta(minutes=1), NOW + timedelta(days=1)])
    worker._now = lambda: next(times)
    runs = []

    async def run_once(**kwargs):
        runs.append(kwargs["now"])
        if len(runs) == 2:
            stop.set()

    monkeypatch.setattr(worker, "run_once", run_once)
    monkeypatch.setattr(worker, "_wait", AsyncMock())
    await worker.run(stop)
    assert runs == [NOW, NOW + timedelta(days=1)]


async def test_disabled_worker_never_connects(tmp_path, monkeypatch):
    connect = AsyncMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(maintenance.aiosqlite, "connect", connect)
    worker = maintenance.SQLiteMaintenance(str(tmp_path / "disabled.db"), config.Settings())
    await worker.run(asyncio.Event())
    await worker.run_once(now=NOW)
    connect.assert_not_awaited()


def test_configuration_defaults_env_and_validation(monkeypatch):
    monkeypatch.setattr(config, "find_dotenv", lambda **kwargs: "")
    defaults = config.Settings()
    assert defaults.sqlite_maintenance_enabled is False
    assert defaults.sqlite_maintenance_dry_run is True
    for table, _, days in TABLES:
        name = f"sqlite_maintenance_{table}_retention_days"
        assert getattr(defaults, name) == days
        monkeypatch.setenv(name.upper(), "7")
    monkeypatch.setenv("SQLITE_MAINTENANCE_ENABLED", "true")
    monkeypatch.setenv("SQLITE_MAINTENANCE_DRY_RUN", "false")
    monkeypatch.setenv("SQLITE_MAINTENANCE_HOUR_UTC", "19")
    monkeypatch.setenv("SQLITE_MAINTENANCE_BATCH_SIZE", "25")
    loaded = config.get_settings.__wrapped__()
    assert loaded.sqlite_maintenance_enabled and not loaded.sqlite_maintenance_dry_run
    assert loaded.sqlite_maintenance_hour_utc == 19
    assert loaded.sqlite_maintenance_batch_size == 25
    for table, _, _ in TABLES:
        assert getattr(loaded, f"sqlite_maintenance_{table}_retention_days") == 7
    for field, value in [("batch_size", 5001), ("batch_size", 0), ("hour_utc", 24),
                         ("alert_events_retention_days", -1)]:
        with pytest.raises(ValueError):
            replace(defaults, **{f"sqlite_maintenance_{field}": value})
    monkeypatch.setenv("SQLITE_MAINTENANCE_DRY_RUN", "typo")
    with pytest.raises(ValueError, match="SQLITE_MAINTENANCE_DRY_RUN"):
        config.get_settings.__wrapped__()


@pytest.mark.parametrize("cancel", [False, True])
async def test_stop_or_cancel_between_batches_closes_dedicated_connection(tmp_path, monkeypatch, cancel):
    from app.runtime.supervisor import TaskSupervisor

    path = str(tmp_path / "stop.db")
    db = await connect_database(path)
    dedicated_connections = []
    original_connect = maintenance.aiosqlite.connect
    stop = asyncio.Event()
    yielded = asyncio.Event()
    supervisor = TaskSupervisor()

    async def tracked_connect(path, **kwargs):
        connection = await original_connect(path, **kwargs)
        dedicated_connections.append(connection)
        return connection

    monkeypatch.setattr(maintenance.aiosqlite, "connect", tracked_connect)
    try:
        await db.execute("CREATE TABLE alert_events (created_at TEXT)")
        await db.executemany("INSERT INTO alert_events VALUES (?)", [
            ((NOW - timedelta(days=91)).isoformat(),) for _ in range(5)
        ])
        await db.commit()
        worker = maintenance.SQLiteMaintenance(path, settings(
            sqlite_maintenance_dry_run=False, sqlite_maintenance_batch_size=2,
        ))

        async def between_batches():
            yielded.set()
            if cancel:
                await asyncio.Event().wait()
            else:
                stop.set()

        monkeypatch.setattr(worker, "_yield_between_batches", between_batches)
        task = supervisor.start("sqlite-maintenance", lambda: worker.run_once(now=NOW, stop_event=stop),
                                restart=True, critical=False)
        await asyncio.wait_for(yielded.wait(), timeout=5)
        if cancel:
            await supervisor.stop()
        else:
            await task
        assert await count(db, "alert_events") == 3
        assert len(dedicated_connections) == 1
        with pytest.raises(ValueError, match="no active connection"):
            await dedicated_connections[0].execute("SELECT 1")
        await db.execute("INSERT INTO alert_events VALUES (?)", (NOW.isoformat(),))
        await db.commit()
    finally:
        await supervisor.stop()
        await db.close()


async def test_window_expiry_stops_further_batches_and_vacuum(tmp_path, monkeypatch):
    path = str(tmp_path / "deadline.db")
    db = await connect_database(path)
    try:
        await db.execute("CREATE TABLE alert_events (created_at TEXT)")
        await db.executemany("INSERT INTO alert_events VALUES (?)", [
            ((NOW - timedelta(days=91)).isoformat(),) for _ in range(5)
        ])
        await db.commit()
        worker = maintenance.SQLiteMaintenance(path, settings(
            sqlite_maintenance_dry_run=False, sqlite_maintenance_batch_size=2,
        ))
        worker._now = lambda: NOW

        async def expire_window():
            worker._now = lambda: NOW + timedelta(hours=1)

        vacuum = AsyncMock()
        monkeypatch.setattr(worker, "_yield_between_batches", expire_window)
        monkeypatch.setattr(worker, "_vacuum_if_beneficial", vacuum)
        await worker.run_once(now=NOW, deadline=NOW + timedelta(minutes=1))
        assert await count(db, "alert_events") == 3
        vacuum.assert_not_awaited()
    finally:
        await db.close()
