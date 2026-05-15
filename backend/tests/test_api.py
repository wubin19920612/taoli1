from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.services.snapshot_store import SnapshotStore


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
