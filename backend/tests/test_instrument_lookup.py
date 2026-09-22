from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.instrument import INSTRUMENT_LOOKUP_EXCHANGES
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
    assert len(payload["exchanges"]) == len(INSTRUMENT_LOOKUP_EXCHANGES)
    exchanges = {item["exchange"]: item for item in payload["exchanges"]}
    assert "htx" not in exchanges
    assert exchanges["binance"]["spot"]["bid"] == 99_999
    assert exchanges["binance"]["future"]["ask"] == 100_101
    assert exchanges["okx"]["spot"] is None
    assert exchanges["bitget"]["error"] == "temporary timeout"
    assert exchanges["lighter"]["future"] is None
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


def test_instrument_lookup_resolves_hyperliquid_raw_alias_and_exact_dex() -> None:
    now = datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
    store = SnapshotStore()
    store.set_all_markets(
        [
            market("ANTHROPICUSDT", "bitget", MarketType.FUTURE, 2_090, now),
            market("ANTHROPICUSDT", "hyperliquid", MarketType.FUTURE, 216, now).model_copy(
                update={"raw_symbol": "io:ANTH"}
            ),
            market(
                "ANTHROPICUSDT",
                "hyperliquid",
                MarketType.FUTURE,
                999,
                now + timedelta(seconds=5),
            ).model_copy(update={"raw_symbol": "xyz:ANTH"}),
        ]
    )
    app = create_app(
        snapshot_store=store,
        settings=Settings(database_url="sqlite:///:memory:"),
    )

    with TestClient(app) as client:
        response = client.get("/api/instruments/ANTH?dex=io")
        inferred_response = client.get("/api/instruments/ANTH")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "ANTHROPICUSDT"
    exchanges = {item["exchange"]: item for item in payload["exchanges"]}
    assert exchanges["bitget"]["future"]["raw_symbol"] == "ANTHROPICUSDT"
    assert exchanges["hyperliquid"]["future"]["raw_symbol"] == "io:ANTH"
    assert exchanges["hyperliquid"]["future"]["bid"] == 215
    assert inferred_response.status_code == 200
    inferred_exchanges = {
        item["exchange"]: item for item in inferred_response.json()["exchanges"]
    }
    assert inferred_exchanges["hyperliquid"]["future"]["raw_symbol"] == "io:ANTH"


def test_anthropic_lookup_lists_lighter_hl_and_route_only_rh_evidence() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    store = SnapshotStore()
    store.set_all_markets(
        [
            market("ANTHROPICUSDT", "lighter", MarketType.FUTURE, 2_170, now).model_copy(
                update={
                    "raw_symbol": "ANTHROPIC",
                    "contract_size_multiplier": 1,
                    "data_source": "Lighter public orderBookDetails + WebSocket order_book",
                }
            ),
            market("ANTHROPICUSDT", "binance", MarketType.FUTURE, 2_160, now).model_copy(
                update={"raw_symbol": "ANTHROPICUSDT"}
            ),
            market("ANTHROPICUSDT", "hyperliquid", MarketType.FUTURE, 2_165, now).model_copy(
                update={"raw_symbol": "io:ANTH", "dex": "io"}
            ),
        ]
    )
    app = create_app(
        snapshot_store=store,
        settings=Settings(database_url="sqlite:///:memory:"),
    )

    class Astro:
        async def list_pairs(self):
            return [
                {
                    "id": "lighter-rh",
                    "name": "ANTHROPIC",
                    "type": "FF",
                    "buyEx": "lighter",
                    "sellEx": "rh-lighter",
                },
                {
                    "id": "hl-rh",
                    "name": "ANTH-ANTHROPIC",
                    "type": "FR",
                    "buyEx": "hl",
                    "sellEx": "rh-lighter",
                    "aHlDex": "io",
                },
            ]

    app.state.astro_client = Astro()

    with TestClient(app) as client:
        responses = [
            client.get(f"/api/instruments/{symbol}")
            for symbol in ("ANTH", "ANTHROPIC", "ANTHROPICUSDT")
        ]

    assert all(response.status_code == 200 for response in responses)
    for response in responses:
        payload = response.json()
        assert payload["symbol"] == "ANTHROPICUSDT"
        identities = {
            (item["exchange"], item["market_type"], item["raw_symbol"], item["dex"])
            for item in payload["markets"]
        }
        assert identities == {
            ("binance", "future", "ANTHROPICUSDT", None),
            ("hyperliquid", "future", "io:ANTH", "io"),
            ("lighter", "future", "ANTHROPIC", None),
        }
        lighter = next(item for item in payload["markets"] if item["exchange"] == "lighter")
        assert lighter["contract_size_multiplier"] == 1
        assert lighter["data_status"] == "live"
        route_only = [item for item in payload["astro_routes"] if item["route"] == "rh-lighter"]
        assert len(route_only) == 2
        assert all(item["status"] == "route_only" for item in route_only)
        assert all(item["live_data_supported"] is False for item in route_only)
        assert all("不复制 Lighter 行情" in item["reason"] for item in route_only)


def test_instrument_lookup_lists_multiple_hyperliquid_dex_candidates() -> None:
    now = datetime.now(UTC)
    store = SnapshotStore()
    store.set_all_markets(
        [
            market("ASSETUSDT", "hyperliquid", MarketType.FUTURE, 100, now).model_copy(
                update={"raw_symbol": "io:COIN", "dex": "io"}
            ),
            market("ASSETUSDT", "hyperliquid", MarketType.FUTURE, 101, now).model_copy(
                update={"raw_symbol": "xyz:COIN2", "dex": "xyz"}
            ),
        ]
    )
    app = create_app(
        snapshot_store=store,
        settings=Settings(database_url="sqlite:///:memory:"),
    )

    class SettingsRepository:
        async def get_risk_settings(self) -> RiskSettings:
            from app.models.settings import SymbolAlias

            return RiskSettings(
                symbol_aliases=[
                    SymbolAlias(
                        exchange="hyperliquid",
                        dex="io",
                        symbol="COIN",
                        canonical_symbol="ASSET",
                        market_type=MarketType.FUTURE,
                    ),
                    SymbolAlias(
                        exchange="hyperliquid",
                        dex="xyz",
                        symbol="COIN2",
                        canonical_symbol="ASSET",
                        market_type=MarketType.FUTURE,
                    ),
                ]
            )

    app.state.settings_repo = SettingsRepository()

    with TestClient(app) as client:
        response = client.get("/api/instruments/ASSET")

    assert response.status_code == 200
    markets = response.json()["markets"]
    assert {(item["dex"], item["raw_symbol"]) for item in markets} == {
        ("io", "io:COIN"),
        ("xyz", "xyz:COIN2"),
    }
    assert response.json()["market_count"] == 2


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
