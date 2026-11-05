"""Fourth-round PM requirements; keep failures visible until implementation."""

import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.db.repositories import ShadowTradeRepository
from app.db.schema import initialize_shadow_schema
from app.services.shadow_ledger import ShadowLedger
from shadow_helpers import NOW, pair


def test_startup_registers_existing_spread_without_opening():
    book = ShadowLedger(Settings(shadow_enabled=True))
    markets, opportunity = pair()

    book.process(markets, [opportunity], NOW)

    assert book.open_count == 0
    assert not book.positions
    assert not book.pending
    assert book.baseline_skipped == 1


@pytest.mark.asyncio
async def test_restore_retires_v1_without_deleting_history(tmp_path):
    db = await connect_database(str(tmp_path / "round4-retirement.db"))
    try:
        await initialize_shadow_schema(db)
        repo = ShadowTradeRepository(db)
        legacy = {
            "id": "legacy-row", "opportunity_id": "legacy-op",
            "variant": "conv-0.1", "symbol": "BTCUSDT",
            "opened_at": NOW.isoformat(), "closed_at": None,
            "fake_flag": False, "strategy_version": "shadow-v1",
            "exit_reason": None, "net_bps": None,
        }
        await repo.save_batch([legacy])

        restored, _ = await repo.restore(NOW)

        assert restored == []
        history = await repo.list_trades()
        assert len(history) == 1
        assert history[0]["id"] == legacy["id"]
        assert history[0]["exit_reason"] == "retired"
        assert history[0]["closed_at"] == NOW.isoformat()
        assert history[0]["net_bps"] is None
    finally:
        await db.close()
