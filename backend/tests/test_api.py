from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.main import create_app
from app.models.history import OpportunityHistoryRow
from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.alert import AlertEvent, AlertRule
from app.models.settings import AlertMessageTemplateSettings, RiskSettings
from app.services.service_control import DockerServiceController, ServiceControlConfig, ServiceControlError
from app.services.snapshot_store import SnapshotStore


class FakeSettingsRepository:
    def __init__(
        self,
        settings: RiskSettings,
        alert_template: AlertMessageTemplateSettings | None = None,
    ):
        self.settings = settings
        self.alert_template = alert_template or AlertMessageTemplateSettings()

    async def get_risk_settings(self) -> RiskSettings:
        return self.settings

    async def get_alert_message_template(self) -> AlertMessageTemplateSettings:
        return self.alert_template


class FakeHistoryRepository:
    def __init__(self, rows: list[OpportunityHistoryRow]):
        self.rows = rows
        self.calls = []

    async def list(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows

    async def list_before(self, **kwargs):
        self.calls.append(kwargs)
        return list(reversed(self.rows))


class FakeAlertRuleRepository:
    def __init__(self, rule: AlertRule):
        self.rule = rule
        self.calls = []

    async def get(self, rule_id: str) -> AlertRule | None:
        self.calls.append(rule_id)
        return self.rule if rule_id == self.rule.id else None


class FakeAlertEventRepository:
    def __init__(self, events: list[AlertEvent]):
        self.events = events
        self.calls = []

    async def list(self, limit: int = 100) -> list[AlertEvent]:
        self.calls.append(limit)
        return self.events


class FakeServiceControl:
    def __init__(self):
        self.calls: list[str] = []

    async def get_status(self) -> dict[str, object]:
        return {"enabled": True, "services": ["backend", "frontend"]}

    async def restart(self, service: str) -> dict[str, object]:
        self.calls.append(service)
        return {"service": service, "status": "queued"}


def make_opportunity() -> Opportunity:
    return Opportunity(
        id="opp",
        type=OpportunityType.FF,
        symbol="BTCUSDT",
        buy_exchange="binance",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=0.5,
        close_spread_pct=0.6,
        fee_adjusted_open_pct=0.3,
        spread_width_pct=0.1,
        buy_bid=99,
        buy_ask=100,
        sell_bid=101,
        sell_ask=102,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=20_000_000,
        funding_rate_buy_pct=0,
        funding_rate_sell_pct=0.02,
        net_funding_pct=0.02,
        mark_index_diff_buy_pct=0.01,
        mark_index_diff_sell_pct=0.01,
        risk_labels=[],
        last_seen_at=datetime.now(UTC),
    )


def make_market(symbol: str, exchange: str = "binance") -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        base=symbol.removesuffix("USDT"),
        quote="USDT",
        exchange=exchange,
        market_type=MarketType.SPOT,
        bid=99,
        ask=100,
        volume_24h_usdt=1_000_000,
        timestamp=datetime.now(UTC),
        raw_symbol=symbol,
    )


def test_health_endpoint() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_opportunities_endpoint_returns_seeded_rows() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(snapshot_store=store)
    client = TestClient(app)

    response = client.get("/api/opportunities?type=FF")

    assert response.status_code == 200
    assert response.json()[0]["symbol"] == "BTCUSDT"


def test_opportunities_endpoint_excludes_selected_types() -> None:
    sf = make_opportunity().model_copy(
        update={
            "id": "sf",
            "type": OpportunityType.SF,
            "symbol": "SFUSDT",
        }
    )
    ss = make_opportunity().model_copy(
        update={
            "id": "ss",
            "type": OpportunityType.SS,
            "symbol": "SSUSDT",
        }
    )
    ff = make_opportunity().model_copy(
        update={
            "id": "ff",
            "type": OpportunityType.FF,
            "symbol": "FFUSDT",
        }
    )
    store = SnapshotStore()
    store.set_opportunities([sf, ss, ff])
    app = create_app(snapshot_store=store)
    client = TestClient(app)

    response = client.get("/api/opportunities?exclude_types=SF,SS")

    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()] == ["FFUSDT"]


def test_opportunities_endpoint_hides_non_actionable_rows_by_default() -> None:
    clean = make_opportunity().model_copy(update={"id": "clean", "symbol": "BTCUSDT"})
    funding_warning = make_opportunity().model_copy(
        update={
            "id": "funding-warning",
            "symbol": "ETHUSDT",
            "risk_labels": ["FUNDING_AGAINST"],
        }
    )
    obvious_bad = make_opportunity().model_copy(
        update={
            "id": "bad",
            "symbol": "EDGEUSDT",
            "risk_labels": ["HUGE_SPREAD_VERIFY", "LOW_VOLUME"],
        }
    )
    store = SnapshotStore()
    store.set_opportunities([obvious_bad, clean, funding_warning])
    app = create_app(snapshot_store=store)
    client = TestClient(app)

    response = client.get("/api/opportunities")

    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()] == ["BTCUSDT", "ETHUSDT"]

    response = client.get("/api/opportunities?include_risky=true")

    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()] == [
        "EDGEUSDT",
        "BTCUSDT",
        "ETHUSDT",
    ]


def test_opportunities_endpoint_allows_selecting_hidden_risk_labels() -> None:
    huge_spread = make_opportunity().model_copy(
        update={
            "id": "huge",
            "symbol": "EDGEUSDT",
            "risk_labels": ["HUGE_SPREAD_VERIFY"],
        }
    )
    low_volume = make_opportunity().model_copy(
        update={
            "id": "low",
            "symbol": "LOWUSDT",
            "risk_labels": ["LOW_VOLUME"],
        }
    )
    store = SnapshotStore()
    store.set_opportunities([huge_spread, low_volume])
    app = create_app(snapshot_store=store)
    client = TestClient(app)

    response = client.get("/api/opportunities?hidden_risk_labels=LOW_VOLUME")

    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()] == ["EDGEUSDT"]


def test_opportunities_endpoint_filters_min_volume_in_k_units() -> None:
    liquid = make_opportunity().model_copy(
        update={
            "id": "liquid",
            "symbol": "BTCUSDT",
            "buy_volume_24h_usdt": 2_000_000,
            "sell_volume_24h_usdt": 3_000_000,
        }
    )
    illiquid = make_opportunity().model_copy(
        update={
            "id": "illiquid",
            "symbol": "MICROUSDT",
            "buy_volume_24h_usdt": 900_000,
            "sell_volume_24h_usdt": 3_000_000,
        }
    )
    store = SnapshotStore()
    store.set_opportunities([illiquid, liquid])
    app = create_app(snapshot_store=store)
    client = TestClient(app)

    response = client.get("/api/opportunities?include_risky=true&min_volume_24h_k=1000")

    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()] == ["BTCUSDT"]


def test_opportunities_endpoint_keeps_rows_with_only_missing_volume_when_filtering() -> None:
    missing = make_opportunity().model_copy(
        update={
            "id": "missing",
            "symbol": "IRYSUSDT",
            "buy_volume_24h_usdt": None,
            "sell_volume_24h_usdt": None,
        }
    )
    explicit_zero = make_opportunity().model_copy(
        update={
            "id": "zero",
            "symbol": "METAUSDT",
            "buy_volume_24h_usdt": 0.0,
            "sell_volume_24h_usdt": None,
        }
    )
    liquid = make_opportunity().model_copy(
        update={
            "id": "liquid",
            "symbol": "BTCUSDT",
            "buy_volume_24h_usdt": 2_000_000,
            "sell_volume_24h_usdt": 3_000_000,
        }
    )
    store = SnapshotStore()
    store.set_opportunities([explicit_zero, missing, liquid])
    app = create_app(snapshot_store=store)
    client = TestClient(app)

    response = client.get("/api/opportunities?include_risky=true&min_volume_24h_k=1000")

    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()] == ["IRYSUSDT", "BTCUSDT"]


def test_opportunities_endpoint_applies_global_symbol_and_exchange_exclusions() -> None:
    clean = make_opportunity().model_copy(update={"id": "clean", "symbol": "BTCUSDT"})
    blacklisted = make_opportunity().model_copy(
        update={"id": "blacklisted", "symbol": "BADUSDT"}
    )
    ignored_exchange = make_opportunity().model_copy(
        update={"id": "ignored", "symbol": "ETHUSDT", "buy_exchange": "gate"}
    )
    store = SnapshotStore()
    store.set_opportunities([blacklisted, ignored_exchange, clean])
    app = create_app(snapshot_store=store)
    app.state.settings_repo = FakeSettingsRepository(
        RiskSettings(excluded_symbols=["BADUSDT"], ignored_exchanges=["gate"])
    )
    client = TestClient(app)

    response = client.get("/api/opportunities?include_risky=true")

    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()] == ["BTCUSDT"]


def test_health_endpoint_applies_global_symbol_and_exchange_exclusions() -> None:
    clean = make_opportunity().model_copy(update={"id": "clean", "symbol": "BTCUSDT"})
    blacklisted = make_opportunity().model_copy(
        update={"id": "blacklisted", "symbol": "BADUSDT"}
    )
    ignored_exchange = make_opportunity().model_copy(
        update={"id": "ignored", "symbol": "ETHUSDT", "sell_exchange": "gate"}
    )
    store = SnapshotStore()
    store.set_markets([
        make_market("BTCUSDT"),
        make_market("BADUSDT"),
        make_market("ETHUSDT", exchange="gate"),
    ])
    store.set_opportunities([blacklisted, ignored_exchange, clean])
    store.set_exchange_errors({"gate:spot": "timeout", "binance:spot": "timeout"})
    app = create_app(snapshot_store=store)
    app.state.settings_repo = FakeSettingsRepository(
        RiskSettings(excluded_symbols=["BADUSDT"], ignored_exchanges=["gate"])
    )
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["markets"] == 1
    assert response.json()["opportunities"] == 1
    assert response.json()["exchange_errors"] == {"binance:spot": "timeout"}


def test_history_endpoint_returns_compact_spread_and_funding_rows() -> None:
    app = create_app()
    row = OpportunityHistoryRow(
        observed_at=datetime(2026, 5, 19, 1, 0, tzinfo=UTC),
        opportunity_id="opp",
        type=OpportunityType.FF,
        symbol="BTCUSDT",
        buy_exchange="binance",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=0.8,
        close_spread_pct=0.4,
        fee_adjusted_open_pct=0.65,
        spread_width_pct=0.4,
        funding_rate_buy_pct=0.01,
        funding_rate_sell_pct=0.03,
        net_funding_pct=0.02,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=11_000_000,
        risk_labels=[],
    )
    history_repo = FakeHistoryRepository([row])
    app.state.history_repo = history_repo
    client = TestClient(app)

    response = client.get("/api/history/opportunities?symbol=btcusdt&hours=6&limit=50")

    assert response.status_code == 200
    assert response.json()[0]["symbol"] == "BTCUSDT"
    assert response.json()[0]["net_funding_pct"] == 0.02
    assert history_repo.calls[0]["symbol"] == "BTCUSDT"
    assert history_repo.calls[0]["limit"] == 50


def test_alert_message_template_settings_endpoint_roundtrips() -> None:
    app = create_app(settings=Settings(dashboard_password="secret", database_url="sqlite:///:memory:"))

    with TestClient(app) as client:
        response = client.get("/api/settings/alert-message-template")
        assert response.status_code == 200
        payload = response.json()
        assert payload["include_trigger_summary"] is True
        assert payload["include_observations"] is True

        payload["include_funding"] = False
        payload["include_observations"] = False
        payload["observation_limit"] = 2

        unauthenticated = client.put("/api/settings/alert-message-template", json=payload)
        assert unauthenticated.status_code == 401

        saved = client.put(
            "/api/settings/alert-message-template",
            headers={"X-Dashboard-Password": "secret"},
            json=payload,
        )
        assert saved.status_code == 200
        assert saved.json()["include_funding"] is False
        assert saved.json()["observation_limit"] == 2

        reloaded = client.get("/api/settings/alert-message-template")
        assert reloaded.status_code == 200
        assert reloaded.json()["include_observations"] is False


def test_alert_history_endpoint_enriches_legacy_short_messages() -> None:
    app = create_app()
    rule = AlertRule(
        id="rule-1",
        name="FF 价差",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        max_data_age_seconds=600,
        excluded_risk_labels=[],
        consecutive_hits=3,
        cooldown_seconds=300,
    )
    legacy_event = AlertEvent(
        id="evt-1",
        rule_id="rule-1",
        opportunity_id="opp-1",
        symbol="BTCUSDT",
        status="sent",
        message="BTCUSDT FF 1.007%",
        created_at=datetime(2026, 5, 20, 12, 39, 17, tzinfo=UTC),
    )
    history_rows = [
        OpportunityHistoryRow(
            observed_at=datetime(2026, 5, 20, 12, 38, 53, tzinfo=UTC),
            opportunity_id="opp-1",
            type=OpportunityType.FF,
            symbol="BTCUSDT",
            buy_exchange="binance",
            buy_market_type=MarketType.FUTURE,
            sell_exchange="okx",
            sell_market_type=MarketType.FUTURE,
            open_spread_pct=0.863,
            close_spread_pct=0.611,
            fee_adjusted_open_pct=0.663,
            spread_width_pct=0.252,
            funding_rate_buy_pct=0.01,
            funding_rate_sell_pct=-0.02,
            funding_next_rate_buy_pct=0.015,
            funding_next_rate_sell_pct=0.025,
            funding_next_time_buy=datetime(2026, 5, 20, 16, 0, tzinfo=UTC),
            funding_next_time_sell=datetime(2026, 5, 20, 16, 0, tzinfo=UTC),
            net_funding_pct=-0.03,
            net_funding_next_pct=0.01,
            buy_funding_interval_hours=8,
            sell_funding_interval_hours=8,
            net_funding_hourly_pct=-0.00375,
            net_funding_daily_pct=-0.09,
            net_funding_next_hourly_pct=0.00125,
            net_funding_next_daily_pct=0.03,
            buy_volume_24h_usdt=10_000_000,
            sell_volume_24h_usdt=12_000_000,
            risk_labels=["FUNDING_AGAINST"],
        ),
        OpportunityHistoryRow(
            observed_at=datetime(2026, 5, 20, 12, 39, 5, tzinfo=UTC),
            opportunity_id="opp-1",
            type=OpportunityType.FF,
            symbol="BTCUSDT",
            buy_exchange="binance",
            buy_market_type=MarketType.FUTURE,
            sell_exchange="okx",
            sell_market_type=MarketType.FUTURE,
            open_spread_pct=0.947,
            close_spread_pct=0.632,
            fee_adjusted_open_pct=0.747,
            spread_width_pct=0.315,
            funding_rate_buy_pct=0.01,
            funding_rate_sell_pct=-0.02,
            funding_next_rate_buy_pct=0.015,
            funding_next_rate_sell_pct=0.025,
            funding_next_time_buy=datetime(2026, 5, 20, 16, 0, tzinfo=UTC),
            funding_next_time_sell=datetime(2026, 5, 20, 16, 0, tzinfo=UTC),
            net_funding_pct=-0.03,
            net_funding_next_pct=0.01,
            buy_funding_interval_hours=8,
            sell_funding_interval_hours=8,
            net_funding_hourly_pct=-0.00375,
            net_funding_daily_pct=-0.09,
            net_funding_next_hourly_pct=0.00125,
            net_funding_next_daily_pct=0.03,
            buy_volume_24h_usdt=10_000_000,
            sell_volume_24h_usdt=12_000_000,
            risk_labels=["FUNDING_AGAINST"],
        ),
        OpportunityHistoryRow(
            observed_at=datetime(2026, 5, 20, 12, 39, 17, tzinfo=UTC),
            opportunity_id="opp-1",
            type=OpportunityType.FF,
            symbol="BTCUSDT",
            buy_exchange="binance",
            buy_market_type=MarketType.FUTURE,
            sell_exchange="okx",
            sell_market_type=MarketType.FUTURE,
            open_spread_pct=1.007,
            close_spread_pct=0.644,
            fee_adjusted_open_pct=0.807,
            spread_width_pct=0.363,
            funding_rate_buy_pct=0.01,
            funding_rate_sell_pct=-0.02,
            funding_next_rate_buy_pct=0.015,
            funding_next_rate_sell_pct=0.025,
            funding_next_time_buy=datetime(2026, 5, 20, 16, 0, tzinfo=UTC),
            funding_next_time_sell=datetime(2026, 5, 20, 16, 0, tzinfo=UTC),
            net_funding_pct=-0.03,
            net_funding_next_pct=0.01,
            buy_funding_interval_hours=8,
            sell_funding_interval_hours=8,
            net_funding_hourly_pct=-0.00375,
            net_funding_daily_pct=-0.09,
            net_funding_next_hourly_pct=0.00125,
            net_funding_next_daily_pct=0.03,
            buy_volume_24h_usdt=10_000_000,
            sell_volume_24h_usdt=12_000_000,
            risk_labels=["FUNDING_AGAINST"],
        ),
    ]
    app.state.alert_rule_repo = FakeAlertRuleRepository(rule)
    app.state.alert_event_repo = FakeAlertEventRepository([legacy_event])
    app.state.history_repo = FakeHistoryRepository(history_rows)
    client = TestClient(app)

    response = client.get("/api/alerts/events")

    assert response.status_code == 200
    text = response.json()[0]["message"]
    assert "【告警触发】" in text
    assert "价差对：BTCUSDT | binance future -> okx future" in text
    assert "【连续监测】" in text
    assert "1. 12:38:53 | 价差 0.863% | 净估算 0.663% | 资金差 0.01% | 综合 0.673%" in text
    assert "3. 12:39:17 | 价差 1.007% | 净估算 0.807% | 资金差 0.01% | 综合 0.817%" in text


def test_alert_history_endpoint_applies_global_message_template() -> None:
    app = create_app()
    rule = AlertRule(
        id="rule-1",
        name="compact template",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    legacy_event = AlertEvent(
        id="evt-1",
        rule_id="rule-1",
        opportunity_id="opp-1",
        symbol="BTCUSDT",
        status="sent",
        message="BTCUSDT FF 1.007%",
        created_at=datetime(2026, 5, 20, 12, 39, 17, tzinfo=UTC),
    )
    history_row = OpportunityHistoryRow(
        observed_at=datetime(2026, 5, 20, 12, 39, 17, tzinfo=UTC),
        opportunity_id="opp-1",
        type=OpportunityType.FF,
        symbol="BTCUSDT",
        buy_exchange="binance",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=1.007,
        close_spread_pct=0.644,
        fee_adjusted_open_pct=0.807,
        spread_width_pct=0.363,
        funding_rate_buy_pct=0.01,
        funding_rate_sell_pct=-0.02,
        funding_next_rate_buy_pct=0.015,
        funding_next_rate_sell_pct=0.025,
        funding_next_time_buy=datetime(2026, 5, 20, 16, 0, tzinfo=UTC),
        funding_next_time_sell=datetime(2026, 5, 20, 16, 0, tzinfo=UTC),
        net_funding_pct=-0.03,
        net_funding_next_pct=0.01,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=12_000_000,
        risk_labels=["FUNDING_AGAINST"],
    )
    app.state.alert_rule_repo = FakeAlertRuleRepository(rule)
    app.state.alert_event_repo = FakeAlertEventRepository([legacy_event])
    app.state.history_repo = FakeHistoryRepository([history_row])
    app.state.settings_repo = FakeSettingsRepository(
        RiskSettings(),
        AlertMessageTemplateSettings(
            include_rule_details=False,
            include_funding=False,
            include_volume=False,
            include_risk=False,
            include_observations=False,
        ),
    )
    client = TestClient(app)

    response = client.get("/api/alerts/events")

    assert response.status_code == 200
    text = response.json()[0]["message"]
    assert "【告警触发】" in text
    assert "compact template" in text
    assert "价差对：BTCUSDT | binance future -> okx future" in text
    assert "开仓 1.007%" in text
    assert "【规则参数】" not in text
    assert "资金费率" not in text
    assert "成交额" not in text
    assert "风险" not in text
    assert "【连续监测】" not in text


def test_service_control_endpoints_report_status_and_restart_service() -> None:
    app = create_app(settings=Settings(dashboard_password="secret"))
    controller = FakeServiceControl()
    app.state.service_controller = controller
    client = TestClient(app)

    headers = {"X-Dashboard-Password": "secret"}

    status_response = client.get("/api/admin/service-control", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["enabled"] is True
    assert status_response.json()["services"] == ["backend", "frontend"]

    restart_response = client.post("/api/admin/service-control/backend/restart", headers=headers)
    assert restart_response.status_code == 200
    assert restart_response.json()["service"] == "backend"
    assert controller.calls == ["backend"]


def test_service_control_endpoints_require_password_even_when_dashboard_password_is_blank() -> None:
    app = create_app(settings=Settings())
    controller = FakeServiceControl()
    app.state.service_controller = controller
    client = TestClient(app)

    status_response = client.get("/api/admin/service-control")
    assert status_response.status_code == 403

    restart_response = client.post("/api/admin/service-control/backend/restart")
    assert restart_response.status_code == 403
    assert controller.calls == []


@pytest.mark.asyncio
async def test_service_control_restart_fails_closed_without_compose_project_name() -> None:
    controller = DockerServiceController(
        ServiceControlConfig(
            enabled=True,
            environment="development",
            compose_project_name=None,
            docker_socket_path="/var/run/docker.sock",
        )
    )
    controller._compose_project_name = AsyncMock(return_value=None)  # type: ignore[method-assign]
    controller._request = AsyncMock(return_value=[])  # type: ignore[method-assign]

    with pytest.raises(ServiceControlError) as exc_info:
        await controller.restart("backend")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_service_control_frontend_restart_is_reported_as_restarted() -> None:
    controller = DockerServiceController(
        ServiceControlConfig(
            enabled=True,
            environment="development",
            compose_project_name="taoli1",
            docker_socket_path="/var/run/docker.sock",
        )
    )
    controller._find_service_container = AsyncMock(return_value={"Id": "container-1"})  # type: ignore[method-assign]
    controller._request = AsyncMock(return_value={})  # type: ignore[method-assign]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("app.services.service_control.os.path.exists", lambda _: True)
        result = await controller.restart("frontend")

    assert result.status == "restarted"
    assert result.message == "Frontend restart triggered."
    assert controller._request.await_count == 1
