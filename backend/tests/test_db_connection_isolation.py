"""Keep repository rollbacks away from the application's shared writes."""

import ast
import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.main import create_app
from app.services.alert_delivery_store import AlertDeliveryStore


def test_rollback_calls_stay_in_isolated_connection_modules():
    app_root = Path(__file__).resolve().parents[1] / "app"
    allowed = {
        "db/auth.py",  # Dedicated authentication connection, never the shared business writer.
        "db/database.py",  # Recovery releases only a failed read snapshot.
        "db/market_identity_repository.py",
        "services/pair_spread_presets.py",
        "services/astro_card_attempts.py",
    }
    unexpected = []
    for path in sorted(app_root.rglob("*.py")):
        module = path.relative_to(app_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "rollback" and module not in allowed):
                unexpected.append(f"{module}:{node.lineno}")
    assert not unexpected, (
        "New rollback callers must use an independent connection and join the whitelist: "
        + ", ".join(unexpected)
    )


@pytest.mark.parametrize("in_memory", [False, True], ids=["file", "memory"])
async def test_attempts_connection_lifespan(tmp_path, in_memory):
    db_path = ":memory:" if in_memory else str(tmp_path / "radar.db")
    app = create_app(
        settings=Settings(dashboard_auth_disabled=True, database_url=f"sqlite:///{db_path}"),
        start_background_workers=False,
    )
    async with app.router.lifespan_context(app):
        main_db = app.state.alert_event_repo.db
        auth_db = app.state.auth_store.db
        assert auth_db is not main_db
        attempts = app.state.astro_alert_service.attempts
        assert (attempts.db is main_db) is in_memory
        # The dedicated connection sees the schema initialized on the main one.
        assert await attempts.list_attempts() == []
        async with attempts.db.execute("PRAGMA busy_timeout") as cursor:
            assert (await cursor.fetchone())[0] == (5000 if in_memory else 15000)
    with pytest.raises(ValueError, match="no active connection"):
        await attempts.db.execute("SELECT 1")
    with pytest.raises(ValueError, match="no active connection"):
        await main_db.execute("SELECT 1")
    with pytest.raises(ValueError, match="no active connection"):
        await auth_db.execute("SELECT 1")


@pytest.mark.parametrize("failure", [sqlite3.OperationalError, asyncio.CancelledError])
async def test_attempts_rollback_preserves_uncommitted_shared_write(tmp_path, monkeypatch, failure):
    db_path = str(tmp_path / "radar.db")
    app = create_app(
        settings=Settings(dashboard_auth_disabled=True, database_url=f"sqlite:///{db_path}"),
        start_background_workers=False,
    )
    async with app.router.lifespan_context(app):
        store = app.state.alert_delivery_store
        main_db = app.state.alert_event_repo.db
        assert store.db is main_db
        attempts = app.state.astro_alert_service.attempts
        # Stop the real repository after execute, before its actual commit.
        with monkeypatch.context() as paused:
            paused.setattr(main_db, "commit", AsyncMock())
            await store.mark_pending("shared-write", datetime.now(UTC))
        assert main_db.in_transaction

        rollback = AsyncMock(wraps=attempts.db.rollback)
        with monkeypatch.context() as failed:
            failed.setattr(attempts.db, "execute", AsyncMock(side_effect=failure("ledger failure")))
            failed.setattr(attempts.db, "rollback", rollback)
            with pytest.raises(failure, match="ledger failure"):
                await attempts.mark_pending(
                    {"name": "BTC", "type": "FF", "buyEx": "binance", "sellEx": "gate"},
                    "manual", False,
                )
        rollback.assert_awaited_once()
        await main_db.commit()
        # A fresh connection verifies that the original write was persisted.
        observer = await connect_database(db_path)
        try:
            rows = await AlertDeliveryStore(observer).load_all()
            assert [row.key for row in rows] == ["shared-write"]
            assert rows[0].pending_since is not None
        finally:
            await observer.close()


pytestmark = pytest.mark.usefixtures("legacy_auth_sessions")
