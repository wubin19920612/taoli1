import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from app.models.settings import HistorySettings


@dataclass(frozen=True)
class Settings:
    app_name: str = "Arbitrage Radar"
    environment: str = "development"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    database_url: str = "sqlite:///./data/radar.db"
    poll_interval_seconds: float = 8.0
    funding_poll_interval_seconds: float = 120.0
    feishu_webhook_url: str = ""
    feishu_secret: str = ""
    dashboard_password: str = ""
    history_enabled: bool = True
    history_sample_seconds: int = 120
    history_retention_days: int = 3
    history_keep_top_n: int = 100
    history_min_open_spread_pct: float = 0.5
    history_min_volume_24h_k: float = 100
    history_vacuum_interval_seconds: int = 86_400
    service_control_enabled: bool = False
    service_control_restart_delay_seconds: float = 1.0
    service_control_docker_socket_path: str = "/var/run/docker.sock"
    compose_project_name: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def sqlite_path(self) -> str:
        return self.database_url.removeprefix("sqlite:///")

    @property
    def history_settings(self) -> HistorySettings:
        return HistorySettings(
            enabled=self.history_enabled,
            sample_seconds=self.history_sample_seconds,
            retention_days=self.history_retention_days,
            keep_top_n=self.history_keep_top_n,
            min_open_spread_pct=self.history_min_open_spread_pct,
            min_volume_24h_k=self.history_min_volume_24h_k,
            vacuum_interval_seconds=self.history_vacuum_interval_seconds,
        )


def _is_running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def _resolve_sqlite_database_url(database_url: str, dotenv_path: str | None) -> str:
    if not database_url.startswith("sqlite:///"):
        return database_url

    sqlite_path = database_url.removeprefix("sqlite:///")
    if not sqlite_path.startswith("/data/"):
        return database_url

    if _is_running_in_container():
        return database_url

    base_dir = (
        Path(dotenv_path).resolve().parent
        if dotenv_path
        else Path(__file__).resolve().parents[3]
    )
    candidates = [
        base_dir / "backend" / sqlite_path.lstrip("/"),
        base_dir / sqlite_path.lstrip("/"),
    ]
    existing_candidates = [candidate for candidate in candidates if candidate.exists()]
    if not existing_candidates:
        return database_url

    best_candidate = max(
        existing_candidates,
        key=lambda candidate: (candidate.stat().st_size, candidate.stat().st_mtime),
    )
    return f"sqlite:///{best_candidate.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)

    def bool_env(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None or not value.strip():
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    environment = os.getenv("ENVIRONMENT", "development")
    database_url = _resolve_sqlite_database_url(
        os.getenv("DATABASE_URL", "sqlite:///./data/radar.db"),
        dotenv_path or None,
    )

    return Settings(
        app_name=os.getenv("APP_NAME", "Arbitrage Radar"),
        environment=environment,
        cors_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"),
        database_url=database_url,
        poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "8")),
        funding_poll_interval_seconds=float(os.getenv("FUNDING_POLL_INTERVAL_SECONDS", "120")),
        feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", ""),
        feishu_secret=os.getenv("FEISHU_SECRET", ""),
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
        history_enabled=bool_env("HISTORY_ENABLED", True),
        history_sample_seconds=int(os.getenv("HISTORY_SAMPLE_SECONDS", "120")),
        history_retention_days=int(os.getenv("HISTORY_RETENTION_DAYS", "3")),
        history_keep_top_n=int(os.getenv("HISTORY_KEEP_TOP_N", "100")),
        history_min_open_spread_pct=float(os.getenv("HISTORY_MIN_OPEN_SPREAD_PCT", "0.5")),
        history_min_volume_24h_k=float(os.getenv("HISTORY_MIN_VOLUME_24H_K", "100")),
        history_vacuum_interval_seconds=int(os.getenv("HISTORY_VACUUM_INTERVAL_SECONDS", "86400")),
        service_control_enabled=bool_env(
            "SERVICE_CONTROL_ENABLED",
            environment.strip().lower() in {"development", "local", "test"},
        ),
        service_control_restart_delay_seconds=float(
            os.getenv("SERVICE_CONTROL_RESTART_DELAY_SECONDS", "1")
        ),
        service_control_docker_socket_path=os.getenv(
            "SERVICE_CONTROL_DOCKER_SOCKET_PATH",
            "/var/run/docker.sock",
        ),
        compose_project_name=os.getenv("COMPOSE_PROJECT_NAME", "").strip(),
    )
