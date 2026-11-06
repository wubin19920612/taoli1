import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import find_dotenv, load_dotenv

from app.models.settings import AstroAutomationSettings, AstroCardSettings, HistorySettings

OPTIONAL_FEATURES = frozenset({"gate_twap"})

# Accepted for old deployments, but no longer exposed or effective.
RETIRED_FEATURES = frozenset({
    "squeeze", "negative_basis", "oil_news", "opportunity_radar", "symbol_spread",
    "fat_finger", "second_level_sampling", "funding_research",
})


def _bounded_alert_number(value, default, upper, *, integer=False):
    try:
        number = int(value) if integer else float(value)
        if isinstance(value, bool) or not math.isfinite(number) or not 1 <= number <= upper:
            return default
        if integer and number != float(value):
            return default
        return number
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "Arbitrage Radar"
    environment: str = "development"
    disabled_features: frozenset[str] = frozenset()
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    database_url: str = "sqlite:///./data/radar.db"
    poll_interval_seconds: float = 5.0
    hl_bbo_enabled: bool = True
    hl_bbo_max_age_seconds: float = 10.0
    hl_bbo_max_coins: int = 450
    alert_match_concurrency: int = 3
    alert_diagnostic_timeout_seconds: float = 20.0
    alert_orderbook_timeout_seconds: float = 15.0
    memory_probe_enabled: bool = False
    memory_probe_interval_seconds: float = 10.0
    memory_census_enabled: bool = False
    memory_census_interval_seconds: float = 300.0
    memory_census_max_objects: int = 600_000
    memory_census_budget_seconds: float = 3.0
    memory_census_target_seconds: float = 0.5
    memory_tracemalloc_seconds: float = 0.0
    market_identity_catalog_startup_delay_seconds: float = 300.0
    collector_post_publish_mode: Literal["background", "inline"] = "background"
    collector_max_due_adapters_per_cycle: int = 6
    collector_risk_settings_timeout_seconds: float = 2.0
    feishu_webhook_url: str = ""
    feishu_secret: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_alert_chat_id: str = ""
    feishu_phone_user_ids: str = ""
    feishu_phone_user_id_type: str = "open_id"
    feishu_phone_enabled: bool = False
    feishu_live_send_enabled: bool = False
    dashboard_password: str = ""
    account_credentials_master_key: str = ""
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
    astro_sdk_base_url: str = ""
    arb_paper_url: str = ""
    arb_paper_api_key: str = ""
    arb_live_url: str = ""
    arb_live_api_key: str = ""
    astro_admin_prefix: str = ""
    astro_api_key: str = ""
    astro_verify_tls: bool = True
    astro_ca_bundle: str = ""
    astro_dry_run_only: bool = True
    astro_alert_auto_create: bool = False
    astro_manual_card_create: bool = False
    astro_default_max_trade_usdt: float = 10.0
    astro_default_leverage: int = 1
    astro_default_min_notional: float = 10.0
    astro_default_max_notional: float = 10.0
    astro_default_open_enabled: bool = False
    astro_default_close_position_buffer_pct: float = 0.1
    astro_request_timeout_seconds: float = 10.0
    shadow_enabled: bool = False
    shadow_min_open_pct: float = 0.2
    shadow_child_max_rss_mb: float = 250
    shadow_min_liquidity_usd: float = 0
    shadow_max_open: int = 1500
    shadow_slippage_bps: str = '{"gt50k":2,"5k-50k":5,"500-5k":15,"lt500":30,"unknown":30}'
    native_route_exchanges: str = "hyperliquid,lighter,bitget"
    crossex_catalog_refresh_seconds: int = 1800

    dashboard_totp_secret: str = ""
    dashboard_api_token: str = ""
    dashboard_session_days: int = 30
    dashboard_auth_disabled: bool = False
    trusted_proxy_ips: str = "172.18.0.0/24"

    def __repr__(self) -> str:
        # Configuration can contain credentials; never reveal them via exception/debug repr.
        return "Settings(<redacted>)"

    def __post_init__(self) -> None:
        from app.services.execution_routes import validate_shadow_settings

        validate_shadow_settings(self)
        if type(self.dashboard_session_days) is not int or not 1 <= self.dashboard_session_days <= 30:
            raise ValueError("DASHBOARD_SESSION_DAYS must be between 1 and 30")
        if not math.isfinite(self.hl_bbo_max_age_seconds) or self.hl_bbo_max_age_seconds <= 0:
            raise ValueError("hl_bbo_max_age_seconds must be finite and positive")
        if type(self.hl_bbo_max_coins) is not int or self.hl_bbo_max_coins <= 0:
            raise ValueError("hl_bbo_max_coins must be a positive integer")
        for name, default, upper, integer in (
            ("alert_match_concurrency", 3, 8, True),
            ("alert_diagnostic_timeout_seconds", 20.0, 120, False),
            ("alert_orderbook_timeout_seconds", 15.0, 120, False),
        ):
            object.__setattr__(self, name, _bounded_alert_number(
                getattr(self, name), default, upper, integer=integer,
            ))
        disabled = frozenset(key.strip().lower() for key in self.disabled_features if key.strip())
        unknown = disabled - OPTIONAL_FEATURES - RETIRED_FEATURES
        if unknown:
            raise ValueError(f"Unknown DISABLED_FEATURES: {', '.join(sorted(unknown))}")
        object.__setattr__(self, "disabled_features", disabled & OPTIONAL_FEATURES)
        delay = self.market_identity_catalog_startup_delay_seconds
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("market_identity_catalog_startup_delay_seconds must be finite and nonnegative")
        if not math.isfinite(self.memory_probe_interval_seconds) or self.memory_probe_interval_seconds <= 0:
            raise ValueError("memory_probe_interval_seconds must be finite and positive")
        if not math.isfinite(self.memory_census_interval_seconds) or self.memory_census_interval_seconds <= 0:
            raise ValueError("memory_census_interval_seconds must be finite and positive")
        if type(self.memory_census_max_objects) is not int or self.memory_census_max_objects <= 0:
            raise ValueError("memory_census_max_objects must be a positive integer")
        budget = self.memory_census_budget_seconds
        if not math.isfinite(budget) or not 0 < budget <= 10:
            raise ValueError("memory_census_budget_seconds must be finite and 0 < x <= 10")
        target = self.memory_census_target_seconds
        if not math.isfinite(target) or not 0 < target <= budget:
            raise ValueError("memory_census_target_seconds must be finite and 0 < x <= budget")
        if not math.isfinite(self.memory_tracemalloc_seconds) or self.memory_tracemalloc_seconds < 0:
            raise ValueError("memory_tracemalloc_seconds must be finite and nonnegative")
        if self.collector_post_publish_mode not in {"background", "inline"}:
            raise ValueError("collector_post_publish_mode must be background or inline")
        limit = self.collector_max_due_adapters_per_cycle
        if type(limit) is not int or limit < 1:
            raise ValueError("collector_max_due_adapters_per_cycle must be a positive integer")
        timeout = self.collector_risk_settings_timeout_seconds
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("collector_risk_settings_timeout_seconds must be finite and positive")

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

    @property
    def astro_card_settings(self) -> AstroCardSettings:
        return AstroCardSettings(
            max_trade_usdt=self.astro_default_max_trade_usdt,
            leverage=self.astro_default_leverage,
            min_notional=self.astro_default_min_notional,
            max_notional=self.astro_default_max_notional,
            open_enabled=self.astro_default_open_enabled,
            close_position_buffer_pct=self.astro_default_close_position_buffer_pct,
        )

    @property
    def astro_automation_settings(self) -> AstroAutomationSettings:
        return AstroAutomationSettings(alert_auto_create=self.astro_alert_auto_create)

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
    feishu_live_send_enabled = bool_env("FEISHU_LIVE_SEND_ENABLED", False)

    return Settings(
        dashboard_totp_secret=os.getenv("DASHBOARD_TOTP_SECRET", "").strip(),
        dashboard_api_token=os.getenv("DASHBOARD_API_TOKEN", ""),
        dashboard_session_days=int(os.getenv("DASHBOARD_SESSION_DAYS", "30")),
        dashboard_auth_disabled=bool_env("DASHBOARD_AUTH_DISABLED", False),
        trusted_proxy_ips=os.getenv("TRUSTED_PROXY_IPS", "172.18.0.0/24"),
        disabled_features=frozenset(os.getenv("DISABLED_FEATURES", "").split(",")),
        app_name=os.getenv("APP_NAME", "Arbitrage Radar"),
        environment=environment,
        cors_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"),
        database_url=database_url,
        poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "5")),
        hl_bbo_enabled=bool_env("HL_BBO_ENABLED", True),
        hl_bbo_max_age_seconds=float(os.getenv("HL_BBO_MAX_AGE_SECONDS", "10")),
        hl_bbo_max_coins=int(os.getenv("HL_BBO_MAX_COINS", "450")),
        alert_match_concurrency=_bounded_alert_number(
            os.getenv("ALERT_MATCH_CONCURRENCY", "3"), 3, 8, integer=True,
        ),
        alert_diagnostic_timeout_seconds=_bounded_alert_number(
            os.getenv("ALERT_DIAGNOSTIC_TIMEOUT_SECONDS", "20"), 20.0, 120,
        ),
        alert_orderbook_timeout_seconds=_bounded_alert_number(
            os.getenv("ALERT_ORDERBOOK_TIMEOUT_SECONDS", "15"), 15.0, 120,
        ),
        memory_probe_enabled=bool_env("MEMORY_PROBE_ENABLED", False),
        memory_probe_interval_seconds=float(os.getenv("MEMORY_PROBE_INTERVAL_SECONDS", "10")),
        memory_census_enabled=bool_env("MEMORY_CENSUS_ENABLED", False),
        memory_census_interval_seconds=float(os.getenv("MEMORY_CENSUS_INTERVAL_SECONDS", "300")),
        memory_census_max_objects=int(os.getenv("MEMORY_CENSUS_MAX_OBJECTS", "600000")),
        memory_census_budget_seconds=float(os.getenv("MEMORY_CENSUS_BUDGET_SECONDS", "3.0")),
        memory_census_target_seconds=float(os.getenv("MEMORY_CENSUS_TARGET_SECONDS", "0.5")),
        memory_tracemalloc_seconds=float(os.getenv("MEMORY_TRACEMALLOC_SECONDS", "0")),
        market_identity_catalog_startup_delay_seconds=float(
            os.getenv("MARKET_IDENTITY_CATALOG_STARTUP_DELAY_SECONDS", "300")
        ),
        collector_post_publish_mode=os.getenv("COLLECTOR_POST_PUBLISH_MODE", "background"),
        collector_max_due_adapters_per_cycle=int(os.getenv("COLLECTOR_MAX_DUE_ADAPTERS_PER_CYCLE", "6")),
        collector_risk_settings_timeout_seconds=float(os.getenv("COLLECTOR_RISK_SETTINGS_TIMEOUT_SECONDS", "2")),
        feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", ""),
        feishu_secret=os.getenv("FEISHU_SECRET", ""),
        feishu_app_id=os.getenv("FEISHU_APP_ID", "").strip(),
        feishu_app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
        feishu_alert_chat_id=os.getenv("FEISHU_ALERT_CHAT_ID", "").strip(),
        feishu_phone_user_ids=os.getenv("FEISHU_PHONE_USER_IDS", "").strip(),
        feishu_phone_user_id_type=os.getenv("FEISHU_PHONE_USER_ID_TYPE", "open_id").strip() or "open_id",
        feishu_phone_enabled=bool_env("FEISHU_PHONE_ENABLED", False),
        feishu_live_send_enabled=feishu_live_send_enabled,
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
        account_credentials_master_key=os.getenv("ACCOUNT_CREDENTIALS_MASTER_KEY", "").strip(),
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
        astro_sdk_base_url=os.getenv("ASTRO_SDK_BASE_URL", "").strip(),
        arb_paper_url=os.getenv("ARB_PAPER_URL", "").strip(),
        arb_paper_api_key=os.getenv("ARB_PAPER_API_KEY", "").strip(),
        arb_live_url=os.getenv("ARB_LIVE_URL", "").strip(),
        arb_live_api_key=os.getenv("ARB_LIVE_API_KEY", "").strip(),
        astro_admin_prefix=os.getenv("ASTRO_ADMIN_PREFIX", "").strip(),
        astro_api_key=os.getenv("ASTRO_API_KEY", "").strip(),
        astro_verify_tls=bool_env("ASTRO_VERIFY_TLS", True),
        astro_ca_bundle=os.getenv("ASTRO_CA_BUNDLE", "").strip(),
        astro_dry_run_only=bool_env("ASTRO_DRY_RUN_ONLY", True),
        astro_alert_auto_create=bool_env("ASTRO_ALERT_AUTO_CREATE", False),
        astro_manual_card_create=bool_env("ASTRO_MANUAL_CARD_CREATE", False),
        astro_default_max_trade_usdt=float(os.getenv("ASTRO_DEFAULT_MAX_TRADE_USDT", "10")),
        astro_default_leverage=int(os.getenv("ASTRO_DEFAULT_LEVERAGE", "1")),
        astro_default_min_notional=float(os.getenv("ASTRO_DEFAULT_MIN_NOTIONAL", "10")),
        astro_default_max_notional=float(os.getenv("ASTRO_DEFAULT_MAX_NOTIONAL", "10")),
        astro_default_open_enabled=bool_env("ASTRO_DEFAULT_OPEN_ENABLED", False),
        astro_default_close_position_buffer_pct=float(
            os.getenv("ASTRO_DEFAULT_CLOSE_POSITION_BUFFER_PCT", "0.1")
        ),
        astro_request_timeout_seconds=float(os.getenv("ASTRO_REQUEST_TIMEOUT_SECONDS", "10")),
        shadow_enabled=bool_env("SHADOW_ENABLED", False),
        shadow_min_open_pct=float(os.getenv("SHADOW_MIN_OPEN_PCT", "0.2")),
        shadow_max_open=int(os.getenv("SHADOW_MAX_OPEN", "1500")),
        shadow_child_max_rss_mb=float(os.getenv("SHADOW_CHILD_MAX_RSS_MB", "250")),
        shadow_min_liquidity_usd=float(os.getenv("SHADOW_MIN_LIQUIDITY_USD", "0")),
        shadow_slippage_bps=os.getenv("SHADOW_SLIPPAGE_BPS", Settings.shadow_slippage_bps),
        native_route_exchanges=os.getenv("NATIVE_ROUTE_EXCHANGES", "hyperliquid,lighter,bitget"),
        crossex_catalog_refresh_seconds=int(os.getenv("CROSSEX_CATALOG_REFRESH_SECONDS", "1800")),
    )
