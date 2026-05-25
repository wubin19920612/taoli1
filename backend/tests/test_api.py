from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.main import create_app
from app.models.history import OpportunityHistoryRow
from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.alert import AlertEvent, AlertRule
from app.models.orderbook import DepthValidationResult
from app.models.settings import AlertMessageTemplateSettings, AstroCardSettings, LivePilotSettings, RiskSettings
from app.services.astro_alerts import AstroAlertService
from app.services.service_control import DockerServiceController, ServiceControlConfig, ServiceControlError
from app.services.snapshot_store import SnapshotStore


class FakeSettingsRepository:
    def __init__(
        self,
        settings: RiskSettings,
        alert_template: AlertMessageTemplateSettings | None = None,
        astro_card_settings: AstroCardSettings | None = None,
        live_pilot_settings: LivePilotSettings | None = None,
    ):
        self.settings = settings
        self.alert_template = alert_template or AlertMessageTemplateSettings()
        self.astro_card_settings = astro_card_settings
        self.live_pilot_settings = live_pilot_settings or LivePilotSettings()

    async def get_risk_settings(self) -> RiskSettings:
        return self.settings

    async def get_alert_message_template(self) -> AlertMessageTemplateSettings:
        return self.alert_template

    async def get_astro_card_settings(self) -> AstroCardSettings:
        return self.astro_card_settings or AstroCardSettings()

    async def find_astro_card_settings(self) -> AstroCardSettings | None:
        return self.astro_card_settings

    async def set_astro_card_settings(self, settings: AstroCardSettings) -> AstroCardSettings:
        self.astro_card_settings = settings
        return settings

    async def get_live_pilot_settings(self) -> LivePilotSettings:
        return self.live_pilot_settings

    async def set_live_pilot_settings(self, settings: LivePilotSettings) -> LivePilotSettings:
        self.live_pilot_settings = settings
        return settings


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

    async def list(self) -> list[AlertRule]:
        return [self.rule]

    async def get(self, rule_id: str) -> AlertRule | None:
        self.calls.append(rule_id)
        return self.rule if rule_id == self.rule.id else None


class FakeAlertRulesRepository:
    def __init__(self, rules: list[AlertRule]):
        self.rules = rules

    async def list(self) -> list[AlertRule]:
        return self.rules


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


class FakeCollector:
    def exchange_states(self) -> dict[str, dict[str, object]]:
        return {
            "binance": {
                "status": "healthy",
                "last_success_at": datetime(2026, 5, 21, 12, 0, tzinfo=UTC),
                "last_error_at": None,
                "consecutive_failures": 0,
                "cooldown_until": None,
                "next_due_at": datetime(2026, 5, 21, 12, 0, 8, tzinfo=UTC),
            },
            "gate": {
                "status": "cooling_down",
                "last_success_at": datetime(2026, 5, 21, 11, 59, tzinfo=UTC),
                "last_error_at": datetime(2026, 5, 21, 12, 0, tzinfo=UTC),
                "consecutive_failures": 1,
                "cooldown_until": datetime(2026, 5, 21, 12, 0, 15, tzinfo=UTC),
                "next_due_at": datetime(2026, 5, 21, 12, 0, 15, tzinfo=UTC),
            },
        }


class FakeAstroSubmitService:
    def __init__(self):
        self.calls: list[Opportunity] = []
        self.requests: list[object] = []

    async def handle_manual_create(self, opportunity: Opportunity, card_request=None):
        self.calls.append(opportunity)
        self.requests.append(card_request)
        from app.models.astro import AstroAlertActionResult

        return AstroAlertActionResult(
            enabled=True,
            status="created",
            action="add",
            message="已创建暂停卡片 BTC FF binance->okx，禁开=true",
            pair_name="BTC",
            pair_type="FF",
        )


class FakeOrderBookValidator:
    def __init__(self, result: DepthValidationResult):
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def validate(
        self,
        opportunity: Opportunity,
        risk_settings: RiskSettings,
        card_settings: AstroCardSettings | None = None,
        override_notional_usdt: float | None = None,
    ) -> DepthValidationResult:
        self.calls.append(
            {
                "opportunity": opportunity,
                "risk_settings": risk_settings,
                "card_settings": card_settings,
                "override_notional_usdt": override_notional_usdt,
            }
        )
        return self.result


class FakeAstroPairClient:
    def __init__(self):
        self.added: list[dict] = []

    async def list_pairs(self) -> list[dict]:
        return []

    async def add_pair(self, pair: dict) -> dict:
        self.added.append(pair)
        return {"code": 0}

    async def update_pair(self, pair: dict) -> dict:
        raise AssertionError("update_pair should not be called")


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
        close_spread_pct=0.4,
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


def test_health_endpoint_reports_exchange_poll_states() -> None:
    app = create_app()
    app.state.market_collector = FakeCollector()
    app.state.settings_repo = FakeSettingsRepository(RiskSettings(ignored_exchanges=["gate"]))
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert list(payload["exchange_states"]) == ["binance"]
    assert payload["exchange_states"]["binance"]["status"] == "healthy"
    assert payload["exchange_states"]["binance"]["last_success_at"] == "2026-05-21T12:00:00Z"
    assert payload["exchange_states"]["binance"]["cooldown_until"] is None


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


def test_history_stats_endpoint_returns_spread_distribution_and_chart_points() -> None:
    app = create_app()
    base_time = datetime(2026, 5, 19, 1, 0, tzinfo=UTC)

    def row(index: int, open_spread_pct: float, next_funding_pct: float | None):
        observed_at = base_time + timedelta(minutes=index)
        return OpportunityHistoryRow(
            observed_at=observed_at,
            opportunity_id="opp-stats",
            type=OpportunityType.FF,
            symbol="BTCUSDT",
            buy_exchange="binance",
            buy_market_type=MarketType.FUTURE,
            sell_exchange="okx",
            sell_market_type=MarketType.FUTURE,
            open_spread_pct=open_spread_pct,
            close_spread_pct=open_spread_pct - 0.2,
            fee_adjusted_open_pct=open_spread_pct - 0.1,
            spread_width_pct=0.2,
            funding_rate_buy_pct=0.01,
            funding_rate_sell_pct=0.02,
            funding_next_rate_buy_pct=0.01,
            funding_next_rate_sell_pct=0.01 + (next_funding_pct or 0),
            net_funding_pct=0.01,
            net_funding_next_pct=next_funding_pct,
            buy_volume_24h_usdt=10_000_000,
            sell_volume_24h_usdt=11_000_000,
            risk_labels=[],
        )

    chronological_rows = [
        row(0, 0.1, None),
        row(1, 0.2, 0.01),
        row(2, 0.4, 0.02),
        row(3, 0.9, 0.04),
    ]
    history_repo = FakeHistoryRepository(list(reversed(chronological_rows)))
    app.state.history_repo = history_repo
    client = TestClient(app)

    response = client.get(
        "/api/history/opportunities/stats?"
        "symbol=btc-usdt&opportunity_id=opp-stats&type=ff&hours=12&point_limit=3"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 4
    assert payload["symbol"] == "BTCUSDT"
    assert payload["opportunity_id"] == "opp-stats"
    assert payload["type"] == "FF"
    assert payload["first_seen_at"] == "2026-05-19T01:00:00Z"
    assert payload["last_seen_at"] == "2026-05-19T01:03:00Z"
    assert payload["latest"]["open_spread_pct"] == 0.9
    assert payload["open_spread_pct"]["current"] == 0.9
    assert payload["open_spread_pct"]["mean"] == pytest.approx(0.4)
    assert payload["open_spread_pct"]["median"] == pytest.approx(0.3)
    assert payload["open_spread_pct"]["p05"] == pytest.approx(0.115)
    assert payload["open_spread_pct"]["p95"] == pytest.approx(0.825)
    assert payload["net_funding_next_pct"]["mean"] == pytest.approx(0.023333333)
    assert [point["open_spread_pct"] for point in payload["points"]] == [0.1, 0.4, 0.9]
    assert history_repo.calls[0]["symbol"] == "BTCUSDT"
    assert history_repo.calls[0]["opportunity_id"] == "opp-stats"
    assert history_repo.calls[0]["type"] == "FF"
    assert history_repo.calls[0]["limit"] == 10000


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


def test_astro_card_settings_endpoint_roundtrips() -> None:
    app = create_app(settings=Settings(dashboard_password="secret", database_url="sqlite:///:memory:"))

    with TestClient(app) as client:
        response = client.get("/api/settings/astro-card")
        assert response.status_code == 200
        payload = response.json()
        assert payload["max_trade_usdt"] == 10
        assert payload["leverage"] == 1
        assert payload["close_position_buffer_pct"] == 0.1
        assert payload["unfavorable_funding_weight"] == 1
        assert payload["close_position_floor_pct"] == 0

        payload["max_trade_usdt"] = 75
        payload["leverage"] = 4
        payload["max_notional"] = 75
        payload["close_position_buffer_pct"] = 0.2
        payload["unfavorable_funding_weight"] = 1.5
        payload["close_position_floor_pct"] = 0.01

        unauthenticated = client.put("/api/settings/astro-card", json=payload)
        assert unauthenticated.status_code == 401

        saved = client.put(
            "/api/settings/astro-card",
            headers={"X-Dashboard-Password": "secret"},
            json=payload,
        )
        assert saved.status_code == 200
        assert saved.json()["max_trade_usdt"] == 75
        assert saved.json()["leverage"] == 4
        assert saved.json()["close_position_buffer_pct"] == 0.2

        reloaded = client.get("/api/settings/astro-card")
        assert reloaded.status_code == 200
        assert reloaded.json()["max_notional"] == 75
        assert reloaded.json()["unfavorable_funding_weight"] == 1.5
        assert reloaded.json()["close_position_floor_pct"] == 0.01


def test_live_pilot_settings_endpoint_roundtrips() -> None:
    app = create_app(settings=Settings(dashboard_password="secret", database_url="sqlite:///:memory:"))

    with TestClient(app) as client:
        response = client.get("/api/settings/live-pilot")
        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled"] is False
        assert payload["max_symbols"] == 10
        assert payload["notional_per_symbol_usdt"] == 100
        assert payload["create_cards_enabled"] is True
        assert payload["exclude_ss"] is True

        payload["enabled"] = True
        payload["max_symbols"] = 7
        payload["notional_per_symbol_usdt"] = 125
        payload["min_next_funding_edge_pct"] = -0.03
        payload["prefer_hyperliquid"] = False
        payload["exclude_ss"] = False

        unauthenticated = client.put("/api/settings/live-pilot", json=payload)
        assert unauthenticated.status_code == 401

        saved = client.put(
            "/api/settings/live-pilot",
            headers={"X-Dashboard-Password": "secret"},
            json=payload,
        )
        assert saved.status_code == 200
        assert saved.json()["enabled"] is True
        assert saved.json()["max_symbols"] == 7
        assert saved.json()["notional_per_symbol_usdt"] == 125

        reloaded = client.get("/api/settings/live-pilot")
        assert reloaded.status_code == 200
        assert reloaded.json()["min_next_funding_edge_pct"] == -0.03
        assert reloaded.json()["prefer_hyperliquid"] is False
        assert reloaded.json()["exclude_ss"] is False


def test_live_pilot_preview_endpoint_returns_selected_test_symbols() -> None:
    store = SnapshotStore()
    btc_normal = make_opportunity().model_copy(
        update={
            "id": "btc-normal",
            "symbol": "BTCUSDT",
            "buy_exchange": "binance",
            "sell_exchange": "okx",
            "fee_adjusted_open_pct": 0.8,
            "funding_next_rate_buy_pct": 0,
            "funding_next_rate_sell_pct": 0,
            "net_funding_next_pct": 0,
        }
    )
    btc_hyper = make_opportunity().model_copy(
        update={
            "id": "btc-hyper",
            "symbol": "BTCUSDT",
            "buy_exchange": "hyperliquid",
            "sell_exchange": "okx",
            "fee_adjusted_open_pct": 0.5,
            "funding_next_rate_buy_pct": 0,
            "funding_next_rate_sell_pct": 0,
            "net_funding_next_pct": 0,
        }
    )
    eth = make_opportunity().model_copy(
        update={
            "id": "eth",
            "symbol": "ETHUSDT",
            "fee_adjusted_open_pct": 0.7,
            "funding_next_rate_buy_pct": 0,
            "funding_next_rate_sell_pct": -0.1,
            "net_funding_next_pct": -0.1,
        }
    )
    xrp_negative = make_opportunity().model_copy(
        update={
            "id": "xrp-negative",
            "symbol": "XRPUSDT",
            "fee_adjusted_open_pct": 1.2,
            "funding_next_rate_buy_pct": 0,
            "funding_next_rate_sell_pct": -0.4,
            "net_funding_next_pct": -0.4,
        }
    )
    store.set_opportunities([btc_normal, btc_hyper, eth, xrp_negative])
    app = create_app(snapshot_store=store)
    app.state.alert_rule_repo = FakeAlertRulesRepository(
        [
            AlertRule(
                name="live pilot",
                types=["FF"],
                min_open_spread_pct=0.0,
                min_fee_adjusted_open_pct=0.0,
                min_volume_24h_usdt=0,
            )
        ]
    )
    app.state.settings_repo = FakeSettingsRepository(
        RiskSettings(),
        live_pilot_settings=LivePilotSettings(
            enabled=True,
            max_symbols=2,
            notional_per_symbol_usdt=100,
            min_next_funding_edge_pct=-0.3,
            prefer_hyperliquid=True,
        ),
    )
    client = TestClient(app)

    response = client.get("/api/settings/live-pilot/preview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["enabled"] is True
    assert payload["total_opportunities"] == 4
    assert payload["eligible_symbols"] == 2
    assert payload["skipped_negative_funding"] == 1
    assert payload["skipped_type"] == 0
    assert payload["budget_usdt"] == 200
    assert [item["symbol"] for item in payload["items"]] == ["ETHUSDT", "BTCUSDT"]
    assert payload["items"][1]["opportunity_id"] == "btc-hyper"
    assert payload["items"][1]["uses_hyperliquid"] is True
    assert payload["items"][0]["next_funding_edge_pct"] == -0.1
    assert payload["items"][0]["notional_usdt"] == 100


def test_live_pilot_preview_endpoint_applies_saved_risk_volume_threshold() -> None:
    store = SnapshotStore()
    liquid = make_opportunity().model_copy(
        update={
            "id": "liquid",
            "symbol": "BTCUSDT",
            "buy_volume_24h_usdt": 2_000_000,
            "sell_volume_24h_usdt": 3_000_000,
        }
    )
    low_volume = make_opportunity().model_copy(
        update={
            "id": "low-volume",
            "symbol": "LOWUSDT",
            "fee_adjusted_open_pct": 1.2,
            "buy_volume_24h_usdt": 200_000,
            "sell_volume_24h_usdt": 250_000,
        }
    )
    store.set_opportunities([low_volume, liquid])
    app = create_app(snapshot_store=store)
    app.state.alert_rule_repo = FakeAlertRulesRepository(
        [
            AlertRule(
                name="live pilot",
                types=["FF"],
                min_open_spread_pct=0.0,
                min_fee_adjusted_open_pct=0.0,
                min_volume_24h_usdt=0,
            )
        ]
    )
    app.state.settings_repo = FakeSettingsRepository(
        RiskSettings(min_volume_24h_usdt=1_000_000, ticker_collision_symbols=[]),
        live_pilot_settings=LivePilotSettings(enabled=True, max_symbols=10),
    )
    client = TestClient(app)

    response = client.get("/api/settings/live-pilot/preview")

    assert response.status_code == 200
    payload = response.json()
    assert [item["symbol"] for item in payload["items"]] == ["BTCUSDT"]
    assert payload["skipped_risk"] == 1


def test_live_pilot_preview_endpoint_applies_alert_rule_thresholds() -> None:
    store = SnapshotStore()
    below_rule = make_opportunity().model_copy(
        update={
            "id": "below-rule",
            "symbol": "LOWEDGEUSDT",
            "open_spread_pct": 0.40,
            "fee_adjusted_open_pct": 0.30,
            "net_funding_next_pct": 0.00,
        }
    )
    above_rule = make_opportunity().model_copy(
        update={
            "id": "above-rule",
            "symbol": "HIGHEDGEUSDT",
            "open_spread_pct": 0.70,
            "fee_adjusted_open_pct": 0.55,
            "net_funding_next_pct": 0.00,
        }
    )
    store.set_opportunities([below_rule, above_rule])
    app = create_app(snapshot_store=store)
    app.state.alert_rule_repo = FakeAlertRuleRepository(
        AlertRule(
            name="live pilot threshold",
            types=["FF"],
            min_open_spread_pct=0.5,
            min_fee_adjusted_open_pct=0.5,
            min_volume_24h_usdt=1_000_000,
        )
    )
    app.state.settings_repo = FakeSettingsRepository(
        RiskSettings(ticker_collision_symbols=[]),
        live_pilot_settings=LivePilotSettings(enabled=True, max_symbols=10),
    )
    client = TestClient(app)

    response = client.get("/api/settings/live-pilot/preview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_opportunities"] == 1
    assert payload["eligible_symbols"] == 1
    assert [item["symbol"] for item in payload["items"]] == ["HIGHEDGEUSDT"]


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
    assert "1. 20:38:53 | 价差 0.863% | 净估算 0.663% | 资金差（周期） 0.01% | 综合 0.673%" in text
    assert "3. 20:39:17 | 价差 1.007% | 净估算 0.807% | 资金差（周期） 0.01% | 综合 0.817%" in text


def test_alert_history_endpoint_rebuilds_stored_full_messages_with_utc_plus_8() -> None:
    app = create_app()
    rule = AlertRule(
        id="rule-1",
        name="FF 价差",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
    )
    stored_event = AlertEvent(
        id="evt-1",
        rule_id="rule-1",
        opportunity_id="opp-1",
        symbol="BTCUSDT",
        status="sent",
        message=(
            "【告警触发】\n"
            "规则：FF 价差\n\n"
            "【连续监测】\n"
            "1. 12:39:17 | 价差 1.007% | 净估算 0.807% | 资金差 0.01% | 综合 0.817%"
        ),
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
        buy_funding_interval_hours=8,
        sell_funding_interval_hours=8,
        net_funding_hourly_pct=-0.00375,
        net_funding_daily_pct=-0.09,
        net_funding_next_hourly_pct=0.00125,
        net_funding_next_daily_pct=0.03,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=12_000_000,
        risk_labels=["FUNDING_AGAINST"],
    )
    app.state.alert_rule_repo = FakeAlertRuleRepository(rule)
    app.state.alert_event_repo = FakeAlertEventRepository([stored_event])
    app.state.history_repo = FakeHistoryRepository([history_row])
    client = TestClient(app)

    response = client.get("/api/alerts/events")

    assert response.status_code == 200
    text = response.json()[0]["message"]
    assert "1. 20:39:17 | 价差 1.007% | 净估算 0.807%" in text
    assert "1. 12:39:17 |" not in text
    assert "资金差（周期） 0.01%" in text


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


def test_astro_preview_endpoint_returns_dry_run_pair_for_seeded_opportunity() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(snapshot_store=store)
    client = TestClient(app)

    response = client.get("/api/astro/preview/opp")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "dry_run"
    assert payload["can_submit"] is True
    assert payload["pair"]["name"] == "BTC"
    assert payload["pair"]["type"] == "FF"
    assert payload["pair"]["openPosition"] == "0.005000"
    assert payload["sdk_payload"]["action"] == "add"
    assert any(item["field"] == "openPosition" for item in payload["assumptions"])


def test_astro_preview_endpoint_uses_saved_card_defaults() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(
        snapshot_store=store,
        settings=Settings(dashboard_password="secret", database_url="sqlite:///:memory:"),
    )

    with TestClient(app) as client:
        saved = client.put(
            "/api/settings/astro-card",
            headers={"X-Dashboard-Password": "secret"},
            json={
                "max_trade_usdt": 55,
                "leverage": 3,
                "min_notional": 11,
                "max_notional": 55,
                "close_position_buffer_pct": 0.2,
                "unfavorable_funding_weight": 1,
                "close_position_floor_pct": 0,
            },
        )
        assert saved.status_code == 200

        response = client.get("/api/astro/preview/opp")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pair"]["maxTradeUSDT"] == "55"
    assert payload["pair"]["leverage"] == "3"
    assert payload["pair"]["minNotional"] == "11"
    assert payload["pair"]["maxNotional"] == "55"


def test_astro_preview_endpoint_uses_env_defaults_before_settings_are_saved() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(
        snapshot_store=store,
        settings=Settings(
            database_url="sqlite:///:memory:",
            astro_default_max_trade_usdt=44,
            astro_default_leverage=5,
            astro_default_min_notional=13,
            astro_default_max_notional=44,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/astro/preview/opp")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pair"]["maxTradeUSDT"] == "44"
    assert payload["pair"]["leverage"] == "5"
    assert payload["pair"]["minNotional"] == "13"
    assert payload["pair"]["maxNotional"] == "44"


def test_astro_preview_endpoint_uses_saved_defaults_even_when_they_match_model_defaults() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(
        snapshot_store=store,
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
            astro_default_max_trade_usdt=44,
            astro_default_leverage=5,
            astro_default_min_notional=13,
            astro_default_max_notional=44,
        ),
    )

    with TestClient(app) as client:
        saved = client.put(
            "/api/settings/astro-card",
            headers={"X-Dashboard-Password": "secret"},
            json={
                "max_trade_usdt": 10,
                "leverage": 1,
                "min_notional": 10,
                "max_notional": 10,
                "close_position_buffer_pct": 0.1,
                "unfavorable_funding_weight": 1,
                "close_position_floor_pct": 0,
            },
        )
        assert saved.status_code == 200

        response = client.get("/api/astro/preview/opp")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pair"]["maxTradeUSDT"] == "10"
    assert payload["pair"]["leverage"] == "1"
    assert payload["pair"]["minNotional"] == "10"
    assert payload["pair"]["maxNotional"] == "10"


def test_astro_preview_endpoint_returns_404_for_missing_opportunity() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/astro/preview/missing")

    assert response.status_code == 404


def test_astro_status_endpoint_reports_dry_run_configuration() -> None:
    app = create_app(
        settings=Settings(
            astro_sdk_base_url="https://127.0.0.1:8443",
            astro_admin_prefix="admin",
            astro_api_key="secret",
            astro_dry_run_only=True,
        )
    )
    client = TestClient(app)

    response = client.get("/api/astro/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["dry_run_only"] is True
    assert payload["api_key_configured"] is True
    assert payload["pair_path"] == "/admin/api/config/sdk-update-pair"
    assert payload["message_path"] == "/admin/api/config/sdk-send-message"


def test_astro_manual_card_create_endpoint_returns_action_result_for_seeded_opportunity() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(
        snapshot_store=store,
        settings=Settings(dashboard_password="secret", astro_manual_card_create=True),
    )
    service = FakeAstroSubmitService()
    app.state.astro_alert_service = service
    client = TestClient(app)

    response = client.post(
        "/api/astro/opportunities/opp/card",
        headers={"X-Dashboard-Password": "secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "created"
    assert payload["action"] == "add"
    assert payload["pair_name"] == "BTC"
    assert service.calls[0].id == "opp"
    assert service.requests[0] is None


def test_astro_manual_card_create_endpoint_forwards_overrides_and_saves_defaults() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(
        snapshot_store=store,
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
            astro_manual_card_create=True,
        ),
    )
    service = FakeAstroSubmitService()
    app.state.astro_alert_service = service

    with TestClient(app) as client:
        response = client.post(
            "/api/astro/opportunities/opp/card",
            headers={"X-Dashboard-Password": "secret"},
            json={
                "max_trade_usdt": 88,
                "leverage": 2,
                "min_notional": 12,
                "max_notional": 88,
                "save_as_default": True,
            },
        )
        assert response.status_code == 200

        saved = client.get("/api/settings/astro-card")

    assert service.requests[0] is not None
    assert service.requests[0].max_trade_usdt == 88
    assert service.requests[0].leverage == 2
    assert service.requests[0].min_notional == 12
    assert service.requests[0].max_notional == 88
    assert service.requests[0].save_as_default is True
    assert saved.status_code == 200
    assert saved.json()["max_trade_usdt"] == 88
    assert saved.json()["leverage"] == 2
    assert saved.json()["min_notional"] == 12
    assert saved.json()["max_notional"] == 88


def test_astro_manual_card_create_endpoint_skips_when_order_book_validation_fails() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(
        snapshot_store=store,
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
            astro_manual_card_create=True,
        ),
    )
    service = FakeAstroSubmitService()
    validator = FakeOrderBookValidator(
        DepthValidationResult(
            passed=False,
            target_notional_usdt=1000,
            buy_filled_usdt=500,
            sell_filled_usdt=1000,
            buy_vwap=100,
            sell_vwap=100.5,
            quoted_open_pct=0.5,
            executable_open_pct=0.1,
            effective_executable_edge_pct=-0.05,
            slippage_loss_pct=0.4,
            blockers=["buy side depth filled 500.00/1000.00 USDT"],
            warnings=[],
        )
    )
    app.state.astro_alert_service = service
    app.state.orderbook_validator = validator

    with TestClient(app) as client:
        response = client.post(
            "/api/astro/opportunities/opp/card",
            headers={"X-Dashboard-Password": "secret"},
            json={"max_trade_usdt": 80, "max_notional": 80},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    assert payload["action"] == "order_book_validation"
    assert "buy side depth filled" in payload["message"]
    assert service.calls == []
    assert validator.calls[0]["override_notional_usdt"] == 80
    card_settings = validator.calls[0]["card_settings"]
    assert isinstance(card_settings, AstroCardSettings)
    assert card_settings.max_trade_usdt == 80


def test_astro_manual_card_create_endpoint_uses_saved_defaults_with_real_service() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(
        snapshot_store=store,
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
            astro_manual_card_create=True,
            astro_dry_run_only=False,
        ),
    )
    client_backend = FakeAstroPairClient()
    app.state.astro_alert_service = AstroAlertService(
        client_backend,
        app.state.settings,
        add_restart_delay_seconds=0,
    )

    with TestClient(app) as client:
        saved = client.put(
            "/api/settings/astro-card",
            headers={"X-Dashboard-Password": "secret"},
            json={
                "max_trade_usdt": 77,
                "leverage": 3,
                "min_notional": 14,
                "max_notional": 77,
                "close_position_buffer_pct": 0.1,
                "unfavorable_funding_weight": 1,
                "close_position_floor_pct": 0,
            },
        )
        assert saved.status_code == 200

        response = client.post(
            "/api/astro/opportunities/opp/card",
            headers={"X-Dashboard-Password": "secret"},
        )

    assert response.status_code == 200
    assert client_backend.added[0]["maxTradeUSDT"] == "77"
    assert client_backend.added[0]["leverage"] == "3"
    assert client_backend.added[0]["minNotional"] == "14"
    assert client_backend.added[0]["maxNotional"] == "77"


def test_live_pilot_auto_create_service_uses_pilot_settings_for_real_service() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(
        snapshot_store=store,
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
            astro_alert_auto_create=True,
            astro_dry_run_only=False,
        ),
    )
    client_backend = FakeAstroPairClient()
    app.state.astro_alert_service = AstroAlertService(
        client_backend,
        app.state.settings,
        add_restart_delay_seconds=0,
    )
    validator = FakeOrderBookValidator(
        DepthValidationResult(
            passed=True,
            target_notional_usdt=100,
            buy_filled_usdt=100,
            sell_filled_usdt=100,
            buy_vwap=100,
            sell_vwap=100.5,
            quoted_open_pct=0.5,
            executable_open_pct=0.5,
            effective_executable_edge_pct=0.35,
            slippage_loss_pct=0,
            blockers=[],
            warnings=[],
        )
    )
    app.state.orderbook_validator = validator

    with TestClient(app) as client:
        saved = client.put(
            "/api/settings/live-pilot",
            headers={"X-Dashboard-Password": "secret"},
            json={
                "enabled": True,
                "max_symbols": 10,
                "notional_per_symbol_usdt": 100,
                "min_next_funding_edge_pct": -0.05,
                "prefer_hyperliquid": True,
                "create_cards_enabled": True,
            },
        )
        assert saved.status_code == 200

        app.state.astro_alert_service.live_pilot_settings = LivePilotSettings.model_validate(saved.json())
        result = client_backend.added
        assert result == []

    import asyncio

    asyncio.run(app.state.astro_alert_service.handle_alert(make_opportunity()))

    assert client_backend.added[0]["status"] is True
    assert client_backend.added[0]["disableOpen"] is False
    assert client_backend.added[0]["maxTradeUSDT"] == "100"
    assert client_backend.added[0]["maxNotional"] == "100"


def test_astro_manual_card_create_endpoint_requires_dashboard_password() -> None:
    store = SnapshotStore()
    store.set_opportunities([make_opportunity()])
    app = create_app(snapshot_store=store, settings=Settings(dashboard_password="secret"))
    client = TestClient(app)

    response = client.post("/api/astro/opportunities/opp/card")

    assert response.status_code == 401


def test_astro_manual_card_create_endpoint_returns_404_for_missing_opportunity() -> None:
    app = create_app(settings=Settings(dashboard_password="secret"))
    client = TestClient(app)

    response = client.post(
        "/api/astro/opportunities/missing/card",
        headers={"X-Dashboard-Password": "secret"},
    )

    assert response.status_code == 404


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
