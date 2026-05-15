import os
from dataclasses import dataclass
from functools import lru_cache


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

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def sqlite_path(self) -> str:
        return self.database_url.removeprefix("sqlite:///")


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Arbitrage Radar"),
        environment=os.getenv("ENVIRONMENT", "development"),
        cors_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/radar.db"),
        poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "8")),
        funding_poll_interval_seconds=float(os.getenv("FUNDING_POLL_INTERVAL_SECONDS", "120")),
        feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", ""),
        feishu_secret=os.getenv("FEISHU_SECRET", ""),
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
    )
