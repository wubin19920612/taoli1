from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.history import OpportunityHistoryRow
from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import RiskSettings
from app.services.snapshot_store import SnapshotStore


class FakeSettingsRepository:
    def __init__(self, settings: RiskSettings):
        self.settings = settings

    async def get_risk_settings(self) -> RiskSettings:
        return self.settings


class FakeHistoryRepository:
    def __init__(self, rows: list[OpportunityHistoryRow]):
        self.rows = rows
        self.calls = []

    async def list(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


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
