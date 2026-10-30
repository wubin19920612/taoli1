from pathlib import Path

import pytest

from app.core.config import Settings, get_settings


def test_get_settings_loads_dotenv_from_parent_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path
    backend_dir = project_root / "backend"
    backend_dir.mkdir()
    (project_root / ".env").write_text(
        "\n".join(
            [
                "FEISHU_WEBHOOK_URL=https://example.test/hook",
                "FEISHU_SECRET=local-secret",
                "DASHBOARD_PASSWORD=dashboard-pass",
                "ACCOUNT_CREDENTIALS_MASTER_KEY=encrypted-storage-key",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(backend_dir)
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("FEISHU_SECRET", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("ACCOUNT_CREDENTIALS_MASTER_KEY", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.feishu_webhook_url == "https://example.test/hook"
    assert settings.feishu_secret == "local-secret"
    assert settings.feishu_live_send_enabled is False
    assert settings.dashboard_password == "dashboard-pass"
    assert settings.account_credentials_master_key == "encrypted-storage-key"


def test_get_settings_requires_explicit_opt_in_for_live_feishu(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("FEISHU_LIVE_SEND_ENABLED=true", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FEISHU_LIVE_SEND_ENABLED", raising=False)
    get_settings.cache_clear()

    try:
        assert get_settings().feishu_live_send_enabled is True
    finally:
        get_settings.cache_clear()


def test_get_settings_prefers_local_database_copy_when_docker_path_is_loaded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path
    backend_dir = project_root / "backend"
    backend_data_dir = backend_dir / "data"
    root_data_dir = project_root / "data"
    backend_data_dir.mkdir(parents=True)
    root_data_dir.mkdir(parents=True)
    (project_root / ".env").write_text(
        "DATABASE_URL=sqlite:////data/radar.db",
        encoding="utf-8",
    )
    (backend_data_dir / "radar.db").write_bytes(b"backend-db")
    (root_data_dir / "radar.db").write_bytes(b"root")

    monkeypatch.chdir(backend_dir)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()

    try:
        settings = get_settings()

        assert Path(settings.sqlite_path) == backend_data_dir / "radar.db"
    finally:
        get_settings.cache_clear()


def test_get_settings_loads_astro_sdk_configuration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path
    backend_dir = project_root / "backend"
    backend_dir.mkdir()
    (project_root / ".env").write_text(
        "\n".join(
            [
                "ASTRO_SDK_BASE_URL=https://127.0.0.1:8443",
                "ASTRO_ADMIN_PREFIX=admin",
                "ASTRO_API_KEY=secret",
                "ASTRO_VERIFY_TLS=false",
                "ASTRO_CA_BUNDLE=/certs/astro-ca.pem",
                "ASTRO_ALERT_AUTO_CREATE=true",
                "ASTRO_MANUAL_CARD_CREATE=true",
                "ASTRO_DEFAULT_MAX_TRADE_USDT=25",
                "ASTRO_DEFAULT_OPEN_ENABLED=true",
                "ASTRO_DEFAULT_CLOSE_POSITION_BUFFER_PCT=0.2",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(backend_dir)
    for name in [
        "ASTRO_SDK_BASE_URL",
        "ASTRO_ADMIN_PREFIX",
        "ASTRO_API_KEY",
        "ASTRO_VERIFY_TLS",
        "ASTRO_CA_BUNDLE",
        "ASTRO_ALERT_AUTO_CREATE",
        "ASTRO_MANUAL_CARD_CREATE",
        "ASTRO_DEFAULT_MAX_TRADE_USDT",
        "ASTRO_DEFAULT_OPEN_ENABLED",
        "ASTRO_DEFAULT_CLOSE_POSITION_BUFFER_PCT",
    ]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()

    try:
        settings = get_settings()

        assert settings.astro_sdk_base_url == "https://127.0.0.1:8443"
        assert settings.astro_admin_prefix == "admin"
        assert settings.astro_api_key == "secret"
        assert settings.astro_verify_tls is False
        assert settings.astro_ca_bundle == "/certs/astro-ca.pem"
        assert settings.astro_alert_auto_create is True
        assert settings.astro_manual_card_create is True
        assert settings.astro_default_max_trade_usdt == 25
        assert settings.astro_default_open_enabled is True
        assert settings.astro_card_settings.open_enabled is True
        assert settings.astro_default_close_position_buffer_pct == 0.2
    finally:
        get_settings.cache_clear()


def test_get_settings_disables_astro_alert_auto_create_by_default(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ASTRO_ALERT_AUTO_CREATE", raising=False)
    monkeypatch.delenv("ASTRO_MANUAL_CARD_CREATE", raising=False)
    get_settings.cache_clear()

    try:
        settings = get_settings()

        assert settings.astro_alert_auto_create is False
        assert settings.astro_manual_card_create is False
    finally:
        get_settings.cache_clear()


def test_get_settings_loads_funding_research_flags(tmp_path: Path, monkeypatch) -> None:
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "FUNDING_RESEARCH_ENABLED=true",
                "FUNDING_RESEARCH_MANAGE_PAPER_TRADES=false",
                "FUNDING_POLL_INTERVAL_SECONDS=45",
                "FUNDING_RESEARCH_SNAPSHOT_RETENTION_HOURS=24",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(backend_dir)
    for name in [
        "FUNDING_RESEARCH_ENABLED",
        "FUNDING_RESEARCH_MANAGE_PAPER_TRADES",
        "FUNDING_POLL_INTERVAL_SECONDS",
        "FUNDING_RESEARCH_SNAPSHOT_RETENTION_HOURS",
    ]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()

    try:
        settings = get_settings()

        assert settings.funding_research_enabled is True
        assert settings.funding_research_manage_paper_trades is False
        assert settings.funding_poll_interval_seconds == 45
        assert settings.funding_research_snapshot_retention_hours == 24
    finally:
        get_settings.cache_clear()


def test_census_time_budget_defaults(monkeypatch):
    monkeypatch.setattr("app.core.config.find_dotenv", lambda **kwargs: "")
    monkeypatch.delenv("MEMORY_CENSUS_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("MEMORY_CENSUS_TARGET_SECONDS", raising=False)
    for settings in (Settings(), get_settings.__wrapped__()):
        assert settings.memory_census_budget_seconds == 3.0
        assert settings.memory_census_target_seconds == 0.5


@pytest.mark.parametrize("budget,target", [(3.0, 0.5), (10.0, 10.0), (1e-9, 1e-9), (0.25, 0.1)])
def test_census_time_budget_valid_boundaries_and_environment(monkeypatch, budget, target):
    monkeypatch.setattr("app.core.config.find_dotenv", lambda **kwargs: "")
    monkeypatch.setenv("MEMORY_CENSUS_BUDGET_SECONDS", str(budget))
    monkeypatch.setenv("MEMORY_CENSUS_TARGET_SECONDS", str(target))
    for settings in (Settings(memory_census_budget_seconds=budget, memory_census_target_seconds=target),
                     get_settings.__wrapped__()):
        assert settings.memory_census_budget_seconds == budget
        assert settings.memory_census_target_seconds == target


@pytest.mark.parametrize("field,value", [
    (field, value)
    for field in ("memory_census_budget_seconds", "memory_census_target_seconds")
    for value in (0, -1, float("inf"), float("-inf"), float("nan"))
] + [("memory_census_budget_seconds", 10.000001), ("memory_census_target_seconds", 3.000001)])
def test_census_time_budget_rejects_invalid_values_and_environment(monkeypatch, field, value):
    monkeypatch.setattr("app.core.config.find_dotenv", lambda **kwargs: "")
    monkeypatch.setenv("MEMORY_CENSUS_BUDGET_SECONDS", "3.0")
    monkeypatch.setenv("MEMORY_CENSUS_TARGET_SECONDS", "0.5")
    with pytest.raises(ValueError, match=field):
        Settings(**{field: value})
    monkeypatch.setenv(field.upper(), str(value))
    with pytest.raises(ValueError, match=field):
        get_settings.__wrapped__()


@pytest.mark.parametrize("budget,target", [(0.25, 0.5), (9.0, 9.000001)])
def test_census_target_cannot_exceed_configured_budget(monkeypatch, budget, target):
    monkeypatch.setattr("app.core.config.find_dotenv", lambda **kwargs: "")
    monkeypatch.setenv("MEMORY_CENSUS_BUDGET_SECONDS", str(budget))
    monkeypatch.setenv("MEMORY_CENSUS_TARGET_SECONDS", str(target))
    with pytest.raises(ValueError, match="memory_census_target_seconds"):
        Settings(memory_census_budget_seconds=budget, memory_census_target_seconds=target)
    with pytest.raises(ValueError, match="memory_census_target_seconds"):
        get_settings.__wrapped__()


@pytest.mark.parametrize("name", ["MEMORY_CENSUS_BUDGET_SECONDS", "MEMORY_CENSUS_TARGET_SECONDS"])
@pytest.mark.parametrize("value", ["", "invalid"])
def test_census_time_budget_rejects_malformed_environment(monkeypatch, name, value):
    monkeypatch.setattr("app.core.config.find_dotenv", lambda **kwargs: "")
    monkeypatch.setenv("MEMORY_CENSUS_BUDGET_SECONDS", "3.0")
    monkeypatch.setenv("MEMORY_CENSUS_TARGET_SECONDS", "0.5")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        get_settings.__wrapped__()
