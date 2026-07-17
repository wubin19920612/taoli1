from pathlib import Path

from app.core.config import get_settings


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
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(backend_dir)
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("FEISHU_SECRET", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.feishu_webhook_url == "https://example.test/hook"
    assert settings.feishu_secret == "local-secret"
    assert settings.dashboard_password == "dashboard-pass"


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
