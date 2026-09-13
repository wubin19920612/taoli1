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
    assert payload["can_submit"] is False
    assert "BTCUSDT 已在全局黑名单" in payload["blockers"][0]


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


def test_instrument_astro_preview_rejects_ignored_exchange() -> None:
    app = instrument_app()

    with TestClient(app) as client:
        app.state.settings_repo = RiskSettingsRepository(
            RiskSettings(ignored_exchanges=["okx"])
        )
        response = client.post("/api/astro/instrument/preview", json=route())

    assert response.status_code == 422
    assert "okx 已在全局忽略交易所列表" in response.json()["detail"]


def test_instrument_astro_preview_rejects_stale_market_snapshot() -> None:
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

    assert response.status_code == 422
    assert "买入侧 okx future 行情已过期" in response.json()["detail"]
    assert "已停止建卡" in response.json()["detail"]


def test_instrument_astro_create_rejects_spread_drift_after_preview() -> None:
    app = instrument_app(dashboard_password="secret")

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

    assert response.status_code == 409
    assert "预览后可成交价差变化过大" in response.json()["detail"]
