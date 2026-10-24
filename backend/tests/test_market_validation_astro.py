from datetime import UTC, datetime

import pytest
from test_market_identity_validation import binding, market

from app.core.config import Settings
from app.models.settings import AstroCardSettings, LivePilotSettings
from app.services.astro_alerts import AstroAlertService
from app.services.market_identity import MarketIdentityRegistry
from app.services.market_validation_review import MarketValidationService
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_directional_opportunity


class Client:
    def __init__(self):
        self.added = []

    async def list_pairs(self):
        return []

    async def add_pair(self, pair):
        self.added.append(pair)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["manual", "alert", "preadd", "live"])
async def test_all_astro_entries_block_confirmed_wrong_asset_even_in_shadow(entry):
    now = datetime.now(UTC)
    a = market().model_copy(update={"timestamp": now})
    b = market("hyperliquid", "io:PRL", 1.94, "io").model_copy(update={"timestamp": now})
    store = SnapshotStore()
    store.set_all_markets([a, b])
    validation = MarketValidationService(None, store)
    validation.registry = MarketIdentityRegistry([binding(a), binding(b, "pearl")], mode="shadow")
    client = Client()
    service = AstroAlertService(
        client,
        Settings(
            astro_manual_card_create=True, astro_alert_auto_create=True, astro_dry_run_only=False
        ),
        live_pilot_settings=LivePilotSettings(enabled=True),
    )
    service.market_validation = validation
    opportunity = build_directional_opportunity(a, b, mode="FF")
    if entry == "manual":
        result = await service.handle_manual_create(opportunity)
    elif entry == "alert":
        result = await service.handle_alert(opportunity)
    elif entry == "preadd":
        result = await service.handle_preadd(opportunity, AstroCardSettings())
    else:
        result = await service.handle_live_pilot(opportunity)
    assert result.status == "skipped" and result.action == "market_validation"
    assert not client.added
