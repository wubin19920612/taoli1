from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.market import MarketSnapshot, MarketType
from app.models.settings import RiskSettings
from app.services.snapshot_store import SnapshotStore


def market(
    symbol: str,
    exchange: str,
    market_type: MarketType,
    price: float,
    timestamp: datetime,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        base=symbol.removesuffix("USDT"),
        exchange=exchange,
        market_type=market_type,
        bid=price - 1,
        ask=price + 1,
        volume_24h_usdt=1_000_000,
        funding_rate_pct=0.01 if market_type == MarketType.FUTURE else None,
        timestamp=timestamp,
        raw_symbol=symbol,
    )


def test_instrument_lookup_groups_exact_symbol_across_all_exchanges() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    store = SnapshotStore()
    store.set_all_markets(
        [
            market("BTCUSDT", "binance", MarketType.SPOT, 100_000, now),
            market("BTCUSDT", "binance", MarketType.FUTURE, 100_100, now + timedelta(seconds=1)),
            market("BTCUSDT", "gate", MarketType.FUTURE, 100_200, now),
            market("BTCUSDT", "htx", MarketType.SPOT, 100_300, now + timedelta(seconds=2)),
            market("WBTCUSDT", "okx", MarketType.SPOT, 99_900, now),
        ]
    )
    store.set_exchange_errors({"bitget:future": "temporary timeout"})
    app = create_app(
        snapshot_store=store,
        settings=Settings(database_url="sqlite:///:memory:"),
    )

    with TestClient(app) as client:
        response = client.get("/api/instruments/btc")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "BTCUSDT"
    assert payload["exchange_count"] == 2
    assert payload["market_count"] == 3
    assert len(payload["exchanges"]) == 7
    exchanges = {item["exchange"]: item for item in payload["exchanges"]}
    assert "htx" not in exchanges
    assert exchanges["binance"]["spot"]["bid"] == 99_999
    assert exchanges["binance"]["future"]["ask"] == 100_101
    assert exchanges["okx"]["spot"] is None
    assert exchanges["bitget"]["error"] == "temporary timeout"
    assert payload["observed_at"] == (now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    assert len(payload["spreads"]) == 3
    assert payload["spreads"][0]["buy_exchange"] == "binance"
    assert payload["spreads"][0]["buy_market_type"] == "spot"
    assert payload["spreads"][0]["sell_exchange"] == "gate"
    assert payload["spreads"][0]["sell_market_type"] == "future"
    assert payload["spreads"][0]["astro_supported"] is True


def test_instrument_lookup_falls_back_to_filtered_markets_for_injected_stores() -> None:
    now = datetime(2026, 9, 11, 4, 0, tzinfo=UTC)
    store = SnapshotStore()
    store.set_markets([market("ETHUSDT", "okx", MarketType.SPOT, 4_000, now)])
    app = create_app(
        snapshot_store=store,
        settings=Settings(database_url="sqlite:///:memory:"),
    )

    with TestClient(app) as client:
        response = client.get("/api/instruments/ETH-USDT")

    assert response.status_code == 200
    assert response.json()["market_count"] == 1


def test_instrument_lookup_resolves_exchange_alias_to_canonical_symbol() -> None:
    now = datetime(2026, 9, 11, 4, 0, tzinfo=UTC)
    store = SnapshotStore()
    store.set_all_markets(
        [
            market("EDGEUSDT", "binance", MarketType.FUTURE, 4.5, now),
            market("EDGEUSDT", "gate", MarketType.FUTURE, 4.6, now).model_copy(
                update={"raw_symbol": "EDGEX_USDT"}
            ),
        ]
    )
    app = create_app(
        snapshot_store=store,
        settings=Settings(database_url="sqlite:///:memory:"),
    )

    class SettingsRepository:
        async def get_risk_settings(self) -> RiskSettings:
            return RiskSettings()

    app.state.settings_repo = SettingsRepository()

    with TestClient(app) as client:
        response = client.get("/api/instruments/EDGEX")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "EDGEUSDT"
    assert payload["base"] == "EDGE"
    assert payload["exchange_count"] == 2
    exchanges = {item["exchange"]: item for item in payload["exchanges"]}
    assert exchanges["gate"]["future"]["raw_symbol"] == "EDGEX_USDT"


def test_instrument_lookup_keeps_ignored_exchange_basics_but_excludes_its_spreads() -> None:
    now = datetime.now(UTC)
    store = SnapshotStore()
    store.set_all_markets(
        [
            market("BTCUSDT", "binance", MarketType.FUTURE, 100_000, now),
            market("BTCUSDT", "gate", MarketType.FUTURE, 100_100, now),
        ]
    )
    app = create_app(
        snapshot_store=store,
        settings=Settings(database_url="sqlite:///:memory:"),
    )

    class SettingsRepository:
        async def get_risk_settings(self) -> RiskSettings:
            return RiskSettings(ignored_exchanges=["gate"])

    with TestClient(app) as client:
        app.state.settings_repo = SettingsRepository()
        response = client.get("/api/instruments/BTCUSDT")

    assert response.status_code == 200
    payload = response.json()
    gate = next(item for item in payload["exchanges"] if item["exchange"] == "gate")
    assert gate["future"] is not None
    assert payload["spreads"] == []
