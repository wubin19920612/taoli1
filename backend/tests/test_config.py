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
