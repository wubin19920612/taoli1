import pytest

from app.db.database import connect_database
from app.db.repositories import AlertRuleRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.alert import AlertRule, AlertSeverity


@pytest.mark.asyncio
async def test_alert_rule_crud_roundtrip() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = AlertRuleRepository(db)

    rule = AlertRule(
        name="large FF spread",
        enabled=True,
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=3,
        cooldown_seconds=300,
        severity=AlertSeverity.WARNING,
    )

    created = await repo.create(rule)
    loaded = await repo.get(created.id)

    assert loaded is not None
    assert loaded.name == "large FF spread"
    assert loaded.types == ["FF"]


@pytest.mark.asyncio
async def test_schema_migrates_legacy_mark_index_alert_exclusions() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = AlertRuleRepository(db)

    legacy_rule = AlertRule(
        name="legacy rule",
        excluded_risk_labels=["LOW_VOLUME", "MARK_INDEX_DEVIATION"],
    )
    await repo.create(legacy_rule)

    await initialize_schema(db)
    loaded = await repo.get(legacy_rule.id)

    assert loaded is not None
    assert loaded.excluded_risk_labels == ["LOW_VOLUME"]


@pytest.mark.asyncio
async def test_settings_repository_defaults() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = SettingsRepository(db)

    settings = await repo.get_risk_settings()

    assert settings.min_volume_24h_usdt == 1_000_000


@pytest.mark.asyncio
async def test_alert_message_template_repository_defaults_and_roundtrip() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SettingsRepository(db)

        template = await repo.get_alert_message_template()

        assert template.include_trigger_summary is True
        assert template.include_observations is True

        saved = await repo.set_alert_message_template(
            template.model_copy(
                update={
                    "include_rule_details": False,
                    "include_observations": False,
                    "observation_limit": 2,
                }
            )
        )
        loaded = await repo.get_alert_message_template()

        assert saved.include_rule_details is False
        assert loaded.include_observations is False
        assert loaded.observation_limit == 2
    finally:
        await db.close()
