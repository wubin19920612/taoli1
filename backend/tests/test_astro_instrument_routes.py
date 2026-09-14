from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.astro import AstroAlertActionResult
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import DepthValidationResult
from app.models.settings import AstroCardSettings, RiskSettings
from app.services.snapshot_store import SnapshotStore


def market(
    exchange: str,
    bid: float,
    ask: float,
    *,
    timestamp: datetime | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        exchange=exchange,
        market_type=MarketType.FUTURE,
        bid=bid,
        ask=ask,
        volume_24h_usdt=1_000_000,
        funding_rate_pct=0.01,
        funding_interval_hours=8,
        timestamp=timestamp or datetime.now(UTC),
        raw_symbol="BTC-USDT-SWAP" if exchange == "okx" else "BTCUSDT",
    )


def route() -> dict[str, str]:
    return {
        "symbol": "BTCUSDT",
        "buy_exchange": "okx",
        "buy_market_type": "future",
        "sell_exchange": "binance",
        "sell_market_type": "future",
    }


class FakeAstroSubmitService:
    def __init__(self) -> None:
        self.calls = []
        self.requests = []
        self.card_settings = AstroCardSettings()
        self.risk_settings_loader = None

    async def handle_manual_create(self, opportunity, card_request):
        self.calls.append(opportunity)
        self.requests.append(card_request)
        return AstroAlertActionResult(
            enabled=True,
            status="created",
            action="add",
            message="created from instrument lookup",
            pair_name="BTC",
            pair_type="FF",
        )


class FakeOrderBookValidator:
    def __init__(self, result: DepthValidationResult) -> None:
        self.result = result

    async def validate(
        self,
        opportunity,
        risk_settings,
        card_settings=None,
        override_notional_usdt=None,
    ) -> DepthValidationResult:
        return self.result


class RiskSettingsRepository:
    def __init__(self, settings: RiskSettings | None = None) -> None:
        self.settings = settings or RiskSettings(excluded_symbols=["BTCUSDT"])

    async def get_risk_settings(self) -> RiskSettings:
        return self.settings

    async def find_astro_card_settings(self):
        return None


class FailingRiskSettingsRepository(RiskSettingsRepository):
    async def get_risk_settings(self) -> RiskSettings:
        raise RuntimeError("database unavailable")

    async def find_astro_card_settings(self):
        raise RuntimeError("database unavailable")

    async def set_astro_card_settings(self, settings: AstroCardSettings):
        raise RuntimeError("database unavailable")


def instrument_app(*, dashboard_password: str = "", astro_dry_run_only: bool = True):
    store = SnapshotStore()
    store.set_all_markets(
        [
            market("okx", bid=99, ask=100),
            market("binance", bid=101, ask=102),
        ]
    )
    return create_app(
        snapshot_store=store,
        settings=Settings(
            database_url="sqlite:///:memory:",
            dashboard_password=dashboard_password,
            astro_dry_run_only=astro_dry_run_only,
        ),
    )


def test_instrument_astro_preview_uses_selected_live_market_direction() -> None:
    app = instrument_app()

    with TestClient(app) as client:
        response = client.post("/api/astro/instrument/preview", json=route())

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_submit"] is True
    assert payload["pair"]["type"] == "FF"
    assert payload["pair"]["buyEx"] == "okx"
    assert payload["pair"]["sellEx"] == "binance"
    assert payload["pair"]["openPosition"] == "0.009950"
    assert "系统当前处于 dry-run 模式" in payload["warnings"][0]
    assert not any("Dry-run only" in warning for warning in payload["warnings"])


def test_instrument_astro_preview_explains_when_confirm_will_write_to_astro() -> None:
    app = instrument_app(astro_dry_run_only=False)

    with TestClient(app) as client:
        response = client.post("/api/astro/instrument/preview", json=route())

    assert response.status_code == 200
    assert "确认创建后会实际写入 Astro" in response.json()["warnings"][0]


def test_instrument_astro_preview_surfaces_global_blacklist() -> None:
    app = instrument_app()

    with TestClient(app) as client:
        app.state.settings_repo = RiskSettingsRepository()
        response = client.post("/api/astro/instrument/preview", json=route())

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_submit"] is True
    assert payload["pair"] is not None
    assert payload["blockers"] == []
    assert any("BTCUSDT 已在全局黑名单" in warning for warning in payload["warnings"])
    assert any("仅作风险提示，未拦截创建" in warning for warning in payload["warnings"])


def test_instrument_astro_preview_allows_selected_negative_spread_direction() -> None:
    app = instrument_app()
    reverse_route = {
        **route(),
        "buy_exchange": "binance",
        "sell_exchange": "okx",
    }

    with TestClient(app) as client:
        response = client.post("/api/astro/instrument/preview", json=reverse_route)

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_submit"] is True
    assert payload["pair"]["buyEx"] == "binance"
    assert payload["pair"]["sellEx"] == "okx"
    assert payload["pair"]["openPosition"] == "-0.029851"
    assert payload["pair"]["closePosition"] == "-0.030851"
    assert any("Open spread must be positive" in warning for warning in payload["warnings"])


def test_instrument_astro_preview_maps_future_to_spot_route_to_fs() -> None:
    app = instrument_app()
    app.state.snapshot_store.set_all_markets(
        [
            market("binance", bid=99, ask=100),
            market("okx", bid=101, ask=102).model_copy(
                update={"market_type": MarketType.SPOT}
            ),
        ]
    )
    reverse_sf_route = {
        "symbol": "BTCUSDT",
        "buy_exchange": "binance",
        "buy_market_type": "future",
        "sell_exchange": "okx",
        "sell_market_type": "spot",
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/astro/instrument/preview",
            json=reverse_sf_route,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_submit"] is True
    assert payload["pair"]["type"] == "FS"
    assert payload["pair"]["buyEx"] == "binance"
    assert payload["pair"]["sellEx"] == "okx"
    assert any("Astro FS" in warning for warning in payload["warnings"])


def test_instrument_astro_preview_warns_when_risk_settings_cannot_be_loaded() -> None:
    app = instrument_app()

    with TestClient(app) as client:
        app.state.settings_repo = FailingRiskSettingsRepository()
        response = client.post("/api/astro/instrument/preview", json=route())

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_submit"] is True
    assert payload["pair"] is not None
    assert any("读取全局风险设置失败" in warning for warning in payload["warnings"])
    assert any("读取 Astro 建卡设置失败" in warning for warning in payload["warnings"])
    assert any("仅作风险提示，未拦截创建" in warning for warning in payload["warnings"])


def test_instrument_astro_create_rebuilds_route_and_passes_sizing_to_shared_service() -> None:
    app = instrument_app(dashboard_password="secret")
    service = FakeAstroSubmitService()
    app.state.astro_alert_service = service

    with TestClient(app) as client:
        response = client.post(
            "/api/astro/instrument/card",
            headers={"X-Dashboard-Password": "secret"},
            json={
                "route": route(),
                "card": {
                    "max_trade_usdt": 25,
                    "leverage": 2,
                    "min_notional": 10,
                    "max_notional": 25,
                    "open_enabled": False,
                },
                "expected_open_spread_pct": 1,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert len(service.calls) == 1
    opportunity = service.calls[0]
    assert opportunity.buy_exchange == "okx"
    assert opportunity.buy_raw_symbol == "BTC-USDT-SWAP"
    assert opportunity.sell_exchange == "binance"
    assert opportunity.sell_raw_symbol == "BTCUSDT"
    assert service.requests[0].max_trade_usdt == 25
    assert service.requests[0].leverage == 2


def test_instrument_astro_create_warns_but_continues_when_order_book_validation_fails() -> None:
    app = instrument_app(dashboard_password="secret")
    service = FakeAstroSubmitService()
    app.state.astro_alert_service = service
    app.state.orderbook_validator = FakeOrderBookValidator(
        DepthValidationResult(
            passed=False,
            target_notional_usdt=100,
            required_depth_usdt=200,
            price_band_pct=0.2,
            buy_filled_usdt=7.03,
            sell_filled_usdt=100,
            buy_vwap=100,
            sell_vwap=101,
            quoted_open_pct=1,
            executable_open_pct=0.8,
            effective_executable_edge_pct=0.5,
            slippage_loss_pct=0.2,
            blockers=["买入侧价格带深度不足：7.03/200.00 USDT"],
            warnings=[],
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/astro/instrument/card",
            headers={"X-Dashboard-Password": "secret"},
            json={
                "route": route(),
                "card": {"max_trade_usdt": 100, "max_notional": 100},
                "expected_open_spread_pct": 1,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "created"
    assert "created from instrument lookup" in payload["message"]
    assert "订单簿校验未通过" in payload["warnings"][0]
    assert "人工建卡，仅作风险提示，未拦截创建" in payload["warnings"][0]
    assert len(service.calls) == 1


def test_instrument_astro_create_requires_dashboard_password() -> None:
    app = instrument_app(dashboard_password="secret")

    with TestClient(app) as client:
        response = client.post(
            "/api/astro/instrument/card",
            json={
                "route": route(),
                "card": {},
                "expected_open_spread_pct": 1,
            },
        )

    assert response.status_code == 401


def test_instrument_astro_preview_warns_for_ignored_exchange_without_blocking() -> None:
    app = instrument_app()

    with TestClient(app) as client:
        app.state.settings_repo = RiskSettingsRepository(
            RiskSettings(ignored_exchanges=["okx"])
        )
        response = client.post("/api/astro/instrument/preview", json=route())

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_submit"] is True
    assert payload["pair"] is not None
    assert any("okx 已在全局忽略交易所列表" in warning for warning in payload["warnings"])


def test_instrument_astro_preview_warns_for_stale_market_snapshot_without_blocking() -> None:
    app = instrument_app()
    now = datetime.now(UTC)
    app.state.snapshot_store.set_all_markets(
        [
            market("okx", bid=99, ask=100, timestamp=now - timedelta(seconds=31)),
            market("binance", bid=101, ask=102, timestamp=now),
        ]
    )

    with TestClient(app) as client:
        response = client.post("/api/astro/instrument/preview", json=route())

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_submit"] is True
    assert payload["pair"] is not None
    assert any("买入侧 okx future 行情已过期" in warning for warning in payload["warnings"])


def test_instrument_astro_create_warns_for_spread_drift_and_continues() -> None:
    app = instrument_app(dashboard_password="secret")
    service = FakeAstroSubmitService()
    app.state.astro_alert_service = service

    with TestClient(app) as client:
        response = client.post(
            "/api/astro/instrument/card",
            headers={"X-Dashboard-Password": "secret"},
            json={
                "route": route(),
                "card": {},
                "expected_open_spread_pct": 0.2,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "created"
    assert len(service.calls) == 1
    assert any("预览后可成交价差变化过大" in warning for warning in payload["warnings"])
    assert any("仅作风险提示，未拦截创建" in warning for warning in payload["warnings"])


def test_instrument_astro_create_warns_when_risk_settings_cannot_be_loaded() -> None:
    app = instrument_app(dashboard_password="secret")
    service = FakeAstroSubmitService()
    app.state.astro_alert_service = service

    with TestClient(app) as client:
        app.state.settings_repo = FailingRiskSettingsRepository()
        response = client.post(
            "/api/astro/instrument/card",
            headers={"X-Dashboard-Password": "secret"},
            json={
                "route": route(),
                "card": {},
                "expected_open_spread_pct": 1,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "created"
    assert len(service.calls) == 1
    assert any("读取全局风险设置失败" in warning for warning in payload["warnings"])
    assert any("读取 Astro 建卡设置失败" in warning for warning in payload["warnings"])


def test_instrument_astro_create_warns_when_default_settings_cannot_be_saved() -> None:
    app = instrument_app(dashboard_password="secret", astro_dry_run_only=False)
    service = FakeAstroSubmitService()
    app.state.astro_alert_service = service

    with TestClient(app) as client:
        app.state.settings_repo = FailingRiskSettingsRepository()
        response = client.post(
            "/api/astro/instrument/card",
            headers={"X-Dashboard-Password": "secret"},
            json={
                "route": route(),
                "card": {
                    "max_trade_usdt": 25,
                    "save_as_default": True,
                },
                "expected_open_spread_pct": 1,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "created"
    assert len(service.calls) == 1
    assert any("保存 Astro 默认建卡设置失败" in warning for warning in payload["warnings"])
