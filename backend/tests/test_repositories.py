import pytest

from app.db.database import connect_database
from app.db.repositories import AlertRuleRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.alert import AlertRule, AlertSeverity
from app.models.settings import AstroCardSettings


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


@pytest.mark.asyncio
async def test_astro_card_settings_round_trip() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SettingsRepository(db)

        defaults = await repo.get_astro_card_settings()

        assert defaults.max_trade_usdt == 10
        assert defaults.leverage == 1
        assert defaults.close_position_buffer_pct == 0.1
        assert defaults.unfavorable_funding_weight == 1
        assert defaults.close_position_floor_pct == 0

        saved = await repo.set_astro_card_settings(
            AstroCardSettings(
                max_trade_usdt=50,
                leverage=3,
                min_notional=10,
                max_notional=50,
                close_position_buffer_pct=0.2,
                unfavorable_funding_weight=1.5,
                close_position_floor_pct=0.01,
            )
        )
        loaded = await repo.get_astro_card_settings()

        assert saved.max_trade_usdt == 50
        assert loaded.max_trade_usdt == 50
        assert loaded.leverage == 3
        assert loaded.max_notional == 50
        assert loaded.close_position_buffer_pct == 0.2
        assert loaded.unfavorable_funding_weight == 1.5
        assert loaded.close_position_floor_pct == 0.01
    finally:
        await db.close()
