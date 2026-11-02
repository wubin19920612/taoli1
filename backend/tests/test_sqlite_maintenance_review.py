"""Regression cases from the S6 read-only review."""

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from app.core import config
from app.db.repositories import OpportunityHistoryRepository
from app.maintenance import sqlite as maintenance
from app.services.history import OpportunityHistoryRecorder

NOW = datetime(2026, 11, 2, 20, 15, tzinfo=UTC)
CUTOFF = NOW - timedelta(days=90)


def worker(path, **kwargs):
    return maintenance.SQLiteMaintenance(str(path), config.Settings(
        sqlite_maintenance_enabled=True, **kwargs,
    ))


def seed(path, values):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE alert_events (created_at TEXT)")
        db.execute("CREATE INDEX age_idx ON alert_events(created_at DESC)")
        db.executemany("INSERT INTO alert_events VALUES (?)", [(v,) for v in values])


def track_connections(monkeypatch):
    """Trace from sqlite3.Connection.__init__, including ALL initialization SQL."""
    original = aiosqlite.connect
    statements, opened, closed, interrupted, targets = [], [], [], [], []

    def connect(*args, **kwargs):
        base = kwargs.get("factory", sqlite3.Connection)

        class Traced(base):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.set_trace_callback(statements.append)

            def close(self):
                closed.append(True)
                return super().close()

            def interrupt(self):
                interrupted.append(True)
                return super().interrupt()

        targets.append((args, kwargs.copy()))
        kwargs["factory"] = Traced
        connection = original(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(aiosqlite, "connect", connect)
    return statements, opened, closed, interrupted, targets


@pytest.mark.parametrize("enabled,legacy,expected", [
    (False, True, True), (False, False, False), (True, True, False), (True, False, False),
])
async def test_legacy_vacuum_switches(enabled, legacy, expected):
    settings = config.Settings(sqlite_maintenance_enabled=enabled,
                               history_legacy_vacuum_enabled=legacy)
    repo = AsyncMock(spec=OpportunityHistoryRepository)
    repo.prune_before.return_value = 1
    recorder = OpportunityHistoryRecorder(
        repo, settings.history_settings,
        legacy_vacuum_enabled=(settings.history_legacy_vacuum_enabled
                               and not settings.sqlite_maintenance_enabled),
    )
    await recorder._prune(NOW)
    repo.prune_before.assert_awaited_once()
    assert bool(repo.vacuum_if_beneficial.await_count) is expected


def test_legacy_config_defaults_and_environment(monkeypatch):
    assert config.Settings().history_legacy_vacuum_enabled is True
    monkeypatch.setattr(config, "find_dotenv", lambda **kwargs: "")
    monkeypatch.setenv("HISTORY_LEGACY_VACUUM_ENABLED", "false")
    assert config.get_settings.__wrapped__().history_legacy_vacuum_enabled is False


async def test_missing_dry_run_path_does_not_connect_or_create(tmp_path, monkeypatch):
    path = tmp_path / "missing.db"
    _, opened, _, _, _ = track_connections(monkeypatch)
    await worker(path).run_once(now=NOW)
    assert not path.exists()
    assert not opened


async def test_dry_run_readonly_from_connection_creation(tmp_path, monkeypatch):
    path = tmp_path / "space # 中文.db"
    seed(path, [(CUTOFF - timedelta(days=1)).isoformat()])
    before = path.read_bytes()
    statements, _, _, _, targets = track_connections(monkeypatch)
    await worker(path).run_once(now=NOW)
    assert path.read_bytes() == before
    assert targets[0][1].get("uri") is True
    assert "mode=ro" in targets[0][0][0]
    assert statements[0] == "PRAGMA query_only=ON"
    assert not any("journal_mode=" in sql or "wal_checkpoint(" in sql
                   or sql.startswith(("DELETE", "VACUUM")) for sql in statements)


@pytest.mark.parametrize("cancel", [True, False])
@pytest.mark.parametrize("pragma", ["PRAGMA journal_mode=WAL", "PRAGMA query_only=ON"])
async def test_initialization_always_interrupts_and_closes(tmp_path, monkeypatch, cancel, pragma):
    path = tmp_path / "init.db"
    seed(path, [])
    _, opened, closed, interrupted, _ = track_connections(monkeypatch)
    entered = asyncio.Event()
    original = aiosqlite.Connection.execute

    async def fail():
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise RuntimeError("initialization failed")

    def execute(self, sql, *args, **kwargs):
        return fail() if sql == pragma else original(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "execute", execute)
    task = asyncio.create_task(worker(
        path, sqlite_maintenance_dry_run="query_only" in pragma,
    ).run_once(now=NOW))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
        assert interrupted == [True]
        assert closed == [True]
    finally:
        for db in opened:
            await db.close()


@pytest.mark.parametrize("dry_run", [True, False])
async def test_indexed_ranges_keep_offset_and_invalid_times_safe(tmp_path, monkeypatch, dry_run):
    path = tmp_path / "indexed.db"
    # Both offsets cross the cutoff in the opposite direction to lexical ordering.
    values = [
        "2026-08-04T21:15:00+02:00",  # old in UTC
        "2026-08-04T19:15:00-02:00",  # fresh in UTC
        CUTOFF.isoformat(),
        (CUTOFF - timedelta(microseconds=1)).isoformat(),
        "0000-invalid", "2026-08-04T00:00:00",  # invalid / naive: retain
        "2026-08-04T20:14:59Z", "2026-08-05T19:14:59+23:00",
        (NOW + timedelta(days=1)).isoformat(),
    ]
    seed(path, values)
    statements, _, _, _, _ = track_connections(monkeypatch)
    await worker(path, sqlite_maintenance_dry_run=dry_run,
                 sqlite_maintenance_batch_size=2).run_once(now=NOW)
    selects = [s for s in statements if s.startswith("SELECT rowid")]
    assert selects
    with sqlite3.connect(path) as db:
        for sql in selects:
            plan = " ".join(row[3] for row in db.execute("EXPLAIN QUERY PLAN " + sql))
            assert "SEARCH" in plan and "age_idx" in plan
            assert "TEMP B-TREE" not in plan
        remaining = [r[0] for r in db.execute("SELECT created_at FROM alert_events ORDER BY rowid")]
    assert remaining == (values if dry_run else [values[i] for i in [1, 2, 4, 5, 8]])


async def test_dry_run_yields_and_stops_counting(tmp_path, monkeypatch, caplog):
    path = tmp_path / "count.db"
    seed(path, [(CUTOFF - timedelta(days=1)).isoformat()] * 7)
    stop = asyncio.Event()
    instance = worker(path, sqlite_maintenance_batch_size=2)

    async def between():
        stop.set()

    monkeypatch.setattr(instance, "_yield_between_batches", between)
    with caplog.at_level(logging.INFO):
        await instance.run_once(now=NOW, stop_event=stop)
    assert "would_delete=2" in caplog.text
    assert "status=interrupted" in caplog.text


async def test_count_time_budget_aborts_even_without_deadline(tmp_path, monkeypatch, caplog):
    path = tmp_path / "budget.db"
    seed(path, [(CUTOFF - timedelta(days=1)).isoformat()] * 100)
    monkeypatch.setattr(maintenance, "TABLE_BUDGET_SECONDS", 0, raising=False)
    with caplog.at_level(logging.INFO):
        await worker(path).run_once(now=NOW)
    assert "status=interrupted" in caplog.text
    assert "would_delete=100" not in caplog.text


async def test_wal_autocheckpoint_and_pinned_reader_pause(tmp_path, monkeypatch, caplog):
    path = tmp_path / "wal.db"
    seed(path, [(CUTOFF - timedelta(days=1)).isoformat()] * 7)
    reader = sqlite3.connect(path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM alert_events").fetchall()
    statements, _, _, _, _ = track_connections(monkeypatch)
    monkeypatch.setattr(maintenance, "WAL_LIMIT_BYTES", 1, raising=False)
    instance = worker(path, sqlite_maintenance_dry_run=False, sqlite_maintenance_batch_size=2)
    vacuum = AsyncMock()
    monkeypatch.setattr(instance, "_vacuum_if_beneficial", vacuum)
    try:
        with caplog.at_level(logging.INFO):
            await instance.run_once(now=NOW)
        assert "PRAGMA wal_autocheckpoint=1000" in statements
        assert "PRAGMA wal_checkpoint(PASSIVE)" in statements
        assert "wal_pressure" in caplog.text
        assert "PRAGMA wal_checkpoint(TRUNCATE)" not in statements
        vacuum.assert_not_awaited()
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0] == 5
    finally:
        reader.close()
