from datetime import timedelta

import pytest
from test_market_identity_validation import NOW, binding, market

from app.db.database import connect_database
from app.db.market_identity_repository import (
    MarketIdentityRepository,
    initialize_market_identity_schema,
)
from app.models.market_identity import BindingMutation


@pytest.mark.asyncio
async def test_binding_history_and_optimistic_revision_survive_reopen(tmp_path):
    path = str(tmp_path / "identities.db")
    db = await connect_database(path)
    await initialize_market_identity_schema(db)
    await initialize_market_identity_schema(db)
    repo = MarketIdentityRepository(db)
    original = binding(market())
    saved = await repo.save_binding(
        BindingMutation(binding=original, expected_revision=0, reason="核验官方上币公告")
    )
    assert saved.revision == 1
    with pytest.raises(ValueError, match="revision"):
        await repo.save_binding(
            BindingMutation(binding=original, expected_revision=0, reason="过期修改请求")
        )
    changed = original.model_copy(update={"valid_until": NOW + timedelta(days=60)})
    saved = await repo.save_binding(
        BindingMutation(binding=changed, expected_revision=1, reason="重新核验官方资料")
    )
    assert saved.revision == 2
    await db.close()
    db = await connect_database(path)
    try:
        rows = await MarketIdentityRepository(db).list_bindings()
        assert len(rows) == 1 and rows[0].revision == 2
        assert len(await MarketIdentityRepository(db).audit_events()) == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pending_discovery_never_overwrites_verified_and_incidents_are_deduplicated():
    db = await connect_database(":memory:")
    try:
        await initialize_market_identity_schema(db)
        repo = MarketIdentityRepository(db)
        item = binding(market())
        await repo.save_binding(
            BindingMutation(binding=item, expected_revision=0, reason="确认资产身份")
        )
        await repo.discover([market(), market("bybit")])
        await repo.discover([market(), market("bybit")])
        rows = await repo.list_bindings()
        assert len(rows) == 2
        assert next(r for r in rows if r.market.exchange == "bitget").identity_status == "verified"
        await repo.record_incident(item.market.key, "REFERENCE_QUOTE_ONLY", {"reference": True})
        await repo.record_incident(item.market.key, "REFERENCE_QUOTE_ONLY", {"reference": True})
        assert len(await repo.incidents()) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_catalog_reimport_and_numeric_serialization_do_not_create_false_conflicts():
    db = await connect_database(":memory:")
    try:
        await initialize_market_identity_schema(db)
        repo = MarketIdentityRepository(db)
        item = binding(market())
        assert (
            item.fingerprint == type(item).model_validate_json(item.model_dump_json()).fingerprint
        )
        assert await repo.import_catalog([item]) == 1
        assert await repo.import_catalog([item]) == 0
        assert (await repo.list_bindings())[0].identity_status == "verified"
    finally:
        await db.close()
