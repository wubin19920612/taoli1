from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import connect_database
from app.db.repositories import IndexComponentRepository
from app.db.schema import initialize_schema
from app.models.index_component import (
    IndexComponent,
    IndexComponentChange,
    IndexComponentSnapshot,
    IndexComponentWatchItem,
    stable_component_hash,
)
from app.models.market import MarketSnapshot, MarketType
from app.services.index_components import (
    BitgetIndexComponentProvider,
    BinanceIndexComponentProvider,
    BybitIndexComponentProvider,
    GateIndexComponentProvider,
    IndexComponentMonitor,
    MultiIndexComponentProvider,
    OKXIndexComponentProvider,
    build_index_component_alert_message,
)


BASE_TIME = datetime(2026, 5, 27, 8, 0, tzinfo=UTC)


def component(source: str, symbol: str, weight: float | None = None, price: float | None = None) -> IndexComponent:
    return IndexComponent(source=source, symbol=symbol, weight=weight, price=price)


def snapshot(
    components: list[IndexComponent],
    *,
    exchange: str = "binance",
    symbol: str = "VANRYUSDT",
    observed_at: datetime = BASE_TIME,
) -> IndexComponentSnapshot:
    return IndexComponentSnapshot.from_components(
        exchange=exchange,
        symbol=symbol,
        components=components,
        source="test-provider",
        observed_at=observed_at,
    )


def test_component_hash_is_stable_for_equivalent_component_ordering() -> None:
    first = [
        component("gate", "VANRYUSDT", weight=0.4, price=0.101),
        component("binance", "VANRYUSDT", weight=0.6, price=0.102),
    ]
    second = [
        component("BINANCE", "vanryusdt", weight=0.6, price=0.102),
        component("gate", "VANRYUSDT", weight=0.4, price=0.101),
    ]

    assert stable_component_hash(first) == stable_component_hash(second)


def test_component_hash_ignores_volatile_component_prices() -> None:
    first = [
        component("binance", "VANRYUSDT", weight=0.6, price=0.101),
        component("gate", "VANRYUSDT", weight=0.4, price=0.102),
    ]
    second = [
        component("binance", "VANRYUSDT", weight=0.6, price=0.111),
        component("gate", "VANRYUSDT", weight=0.4, price=0.112),
    ]

    assert stable_component_hash(first) == stable_component_hash(second)


def test_index_component_alert_message_lists_weight_changes_readably() -> None:
    change = IndexComponentChange(
        exchange="gate",
        symbol="ESPORTS_USDT",
        old_hash="e19c9d81d470abcd",
        new_hash="8466c834c425abcd",
        old_components=[
            component("binance_alpha", "ESPORTS_USDT", weight=0.1),
            component("gate_futures", "ESPORTS_USDT", weight=0.3),
            component("pancake_v3", "ESPORTS_WBNB", weight=0.1),
        ],
        new_components=[
            component("binance_alpha", "ESPORTS_USDT", weight=0.05),
            component("gate_futures", "ESPORTS_USDT", weight=0.4),
            component("pancake_v3", "ESPORTS_WBNB", weight=0.05),
        ],
        added_components=[],
        removed_components=[],
        changed_components=[
            component("binance_alpha", "ESPORTS_USDT", weight=0.05),
            component("gate_futures", "ESPORTS_USDT", weight=0.4),
            component("pancake_v3", "ESPORTS_WBNB", weight=0.05),
        ],
        source="gate-index-constituents",
        alert_status="pending",
        created_at=datetime(2026, 5, 28, 1, 33, 36, tzinfo=UTC),
    )

    message = build_index_component_alert_message(change)

    assert message == "\n".join(
        [
            "⚠️ [GATE] ESPORTS_USDT 指数成分变更",
            "🕘 2026-05-28 09:33:36",
            "",
            "🔁 成分变更:",
            "• BinanceAlpha (ESPORTS_USDT): 权重 10.00% ↓→ 5.00%",
            "• GateFutures (ESPORTS_USDT): 权重 30.00% ↑→ 40.00%",
            "• PancakeV3 (ESPORTS_WBNB): 权重 10.00% ↓→ 5.00%",
        ]
    )
    assert "hash" not in message.lower()
    assert "新增" not in message
    assert "移除" not in message
    assert "来源" not in message


@pytest.mark.asyncio
async def test_repository_round_trips_snapshot_and_change_history() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        baseline = snapshot(
            [
                component("binance", "VANRYUSDT", weight=0.7),
                component("gate", "VANRYUSDT", weight=0.3),
            ]
        )
        changed = snapshot(
            [
                component("binance", "VANRYUSDT", weight=0.5),
                component("bybit", "VANRYUSDT", weight=0.5),
            ],
            observed_at=BASE_TIME + timedelta(minutes=5),
        )

        await repo.upsert_snapshot(baseline)
        loaded = await repo.get_snapshot("BINANCE", "vanryusdt")

        assert loaded is not None
        assert loaded.exchange == "binance"
        assert loaded.symbol == "VANRYUSDT"
        assert [item.source for item in loaded.components] == ["binance", "gate"]

        created = await repo.create_change(
            baseline=baseline,
            current=changed,
            added_components=[component("bybit", "VANRYUSDT", weight=0.5)],
            removed_components=[component("gate", "VANRYUSDT", weight=0.3)],
            changed_components=[component("binance", "VANRYUSDT", weight=0.5)],
            alert_status="sent",
        )
        rows = await repo.list_changes(symbol="vanryusdt", exchange="BINANCE", limit=10)

        assert rows == [created]
        assert rows[0].old_hash == baseline.component_hash
        assert rows[0].new_hash == changed.component_hash
        assert rows[0].added_components[0].source == "bybit"
        assert rows[0].removed_components[0].source == "gate"
        assert rows[0].changed_components[0].source == "binance"
        assert rows[0].alert_status == "sent"

        fuzzy_rows = await repo.list_changes(symbol="vanry", exchange="BINANCE", limit=10)
        assert fuzzy_rows == [created]

        await repo.upsert_snapshot(changed)
        fuzzy_snapshots = await repo.list_snapshots(symbol="vanry", exchange="BINANCE", limit=10)
        assert fuzzy_snapshots == [changed]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_creates_baseline_without_alerting() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append)

        changes = await monitor.process_snapshots(
            [
                snapshot(
                    [
                        component("binance", "VANRYUSDT", weight=0.7),
                        component("gate", "VANRYUSDT", weight=0.3),
                    ]
                )
            ]
        )

        assert changes == []
        assert alerts == []
        assert await repo.get_snapshot("binance", "VANRYUSDT") is not None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_records_and_alerts_component_changes() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append)
        await repo.create_watch_item(IndexComponentWatchItem(symbol="VANRY"))
        await monitor.process_snapshots(
            [
                snapshot(
                    [
                        component("binance", "VANRYUSDT", weight=0.7),
                        component("gate", "VANRYUSDT", weight=0.3),
                    ]
                )
            ]
        )

        changes = await monitor.process_snapshots(
            [
                snapshot(
                    [
                        component("binance", "VANRYUSDT", weight=0.5),
                        component("bybit", "VANRYUSDT", weight=0.5),
                    ],
                    observed_at=BASE_TIME + timedelta(minutes=5),
                )
            ]
        )

        assert len(changes) == 1
        assert changes[0].exchange == "binance"
        assert changes[0].symbol == "VANRYUSDT"
        assert [item.source for item in changes[0].added_components] == ["bybit"]
        assert [item.source for item in changes[0].removed_components] == ["gate"]
        assert [item.source for item in changes[0].changed_components] == ["binance"]
        assert changes[0].alert_status == "sent"
        assert len(alerts) == 1
        assert "⚠️ [BINANCE] VANRYUSDT 指数成分变更" in alerts[0]
        assert "🔁 成分变更:" in alerts[0]
        assert "• Binance (VANRYUSDT): 权重 70.00% ↓→ 50.00%" in alerts[0]
        assert "• Bybit (VANRYUSDT): 权重 0.00% ↑→ 50.00%" in alerts[0]
        assert "• Gate (VANRYUSDT): 权重 30.00% ↓→ 0.00%" in alerts[0]
        assert "hash" not in alerts[0].lower()
        latest = await repo.get_snapshot("binance", "VANRYUSDT")
        assert latest is not None
        assert latest.component_hash == changes[0].new_hash
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_mutes_component_change_alerts_for_unwatched_symbols() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append)
        await repo.create_watch_item(IndexComponentWatchItem(symbol="BTCUSDT"))
        await monitor.process_snapshots(
            [
                snapshot(
                    [
                        component("binance", "VANRYUSDT", weight=0.7),
                        component("gate", "VANRYUSDT", weight=0.3),
                    ]
                )
            ]
        )

        changes = await monitor.process_snapshots(
            [
                snapshot(
                    [
                        component("binance", "VANRYUSDT", weight=0.5),
                        component("bybit", "VANRYUSDT", weight=0.5),
                    ],
                    observed_at=BASE_TIME + timedelta(minutes=5),
                )
            ]
        )

        assert len(changes) == 1
        assert changes[0].alert_status == "muted"
        assert alerts == []
        rows = await repo.list_changes(symbol="VANRY", limit=10)
        assert rows[0].alert_status == "muted"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_sends_component_change_alerts_for_watched_symbols() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append)
        await repo.create_watch_item(IndexComponentWatchItem(symbol="VANRY"))
        await monitor.process_snapshots(
            [
                snapshot(
                    [
                        component("binance", "VANRYUSDT", weight=0.7),
                        component("gate", "VANRYUSDT", weight=0.3),
                    ]
                )
            ]
        )

        changes = await monitor.process_snapshots(
            [
                snapshot(
                    [
                        component("binance", "VANRYUSDT", weight=0.5),
                        component("bybit", "VANRYUSDT", weight=0.5),
                    ],
                    observed_at=BASE_TIME + timedelta(minutes=5),
                )
            ]
        )

        assert len(changes) == 1
        assert changes[0].alert_status == "sent"
        assert len(alerts) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_reports_only_watchlist_symbols() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        monitor = IndexComponentMonitor(repo)
        await repo.create_watch_item(IndexComponentWatchItem(symbol="VANRY"))
        await repo.upsert_snapshot(snapshot([component("binance", "BTCUSDT", weight=1)], symbol="BTCUSDT"))

        assert await monitor.watched_symbols() == {"VANRY"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_ignores_unchanged_hash() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append)
        first = snapshot(
            [
                component("binance", "VANRYUSDT", weight=0.7),
                component("gate", "VANRYUSDT", weight=0.3),
            ]
        )
        same = snapshot(
            [
                component("gate", "VANRYUSDT", weight=0.3),
                component("binance", "VANRYUSDT", weight=0.7),
            ],
            observed_at=BASE_TIME + timedelta(minutes=5),
        )

        await monitor.process_snapshots([first])
        changes = await monitor.process_snapshots([same])

        assert changes == []
        assert alerts == []
        latest = await repo.get_snapshot("binance", "VANRYUSDT")
        assert latest is not None
        assert latest.observed_at == BASE_TIME + timedelta(minutes=5)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_ignores_price_only_component_changes() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append)
        first = snapshot(
            [
                component("binance", "VANRYUSDT", weight=0.7, price=0.101),
                component("gate", "VANRYUSDT", weight=0.3, price=0.102),
            ]
        )
        price_only = snapshot(
            [
                component("binance", "VANRYUSDT", weight=0.7, price=0.201),
                component("gate", "VANRYUSDT", weight=0.3, price=0.202),
            ],
            observed_at=BASE_TIME + timedelta(minutes=5),
        )

        await monitor.process_snapshots([first])
        changes = await monitor.process_snapshots([price_only])

        assert changes == []
        assert alerts == []
    finally:
        await db.close()


class FakeBinanceConstituentClient:
    def __init__(self, payload_by_symbol: dict[str, dict]):
        self.payload_by_symbol = payload_by_symbol
        self.urls: list[str] = []

    async def get_json(self, url: str):
        self.urls.append(url)
        symbol = url.rsplit("symbol=", 1)[-1]
        return self.payload_by_symbol[symbol]


class FakeIndexComponentClient:
    def __init__(self, payload_by_url: dict[str, dict | list]):
        self.payload_by_url = payload_by_url
        self.urls: list[str] = []

    async def get_json(self, url: str):
        self.urls.append(url)
        return self.payload_by_url[url]


class StaticIndexComponentProvider:
    def __init__(self, snapshots: list[IndexComponentSnapshot]):
        self.snapshots = snapshots
        self.calls: list[list[MarketSnapshot]] = []

    async def fetch_components(
        self,
        markets: list[MarketSnapshot],
    ) -> list[IndexComponentSnapshot]:
        self.calls.append(markets)
        return self.snapshots


def market(
    *,
    exchange: str = "binance",
    symbol: str = "BTCUSDT",
    market_type: MarketType = MarketType.FUTURE,
    raw_symbol: str | None = None,
    mark_price: float | None = 94058.0,
    index_price: float | None = 94057.0,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        base=symbol.removesuffix("USDT"),
        quote="USDT",
        exchange=exchange,
        market_type=market_type,
        bid=1,
        ask=2,
        mark_price=mark_price,
        index_price=index_price,
        timestamp=BASE_TIME,
        raw_symbol=raw_symbol or symbol,
    )


@pytest.mark.asyncio
async def test_binance_provider_parses_index_constituents() -> None:
    client = FakeBinanceConstituentClient(
        {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "time": 1745401553408,
                "constituents": [
                    {
                        "exchange": "binance",
                        "symbol": "BTCUSDT",
                        "price": "94057.03000000",
                        "weight": "0.51282051",
                    },
                    {
                        "exchange": "coinbase",
                        "symbol": "BTC-USDT",
                        "price": "94140.58000000",
                        "weight": "0.15384615",
                    },
                ],
            }
        }
    )
    provider = BinanceIndexComponentProvider(client=client)

    snapshots = await provider.fetch_components([market(symbol="BTCUSDT")])

    assert len(snapshots) == 1
    assert snapshots[0].exchange == "binance"
    assert snapshots[0].symbol == "BTCUSDT"
    assert snapshots[0].source == "binance-fapi-constituents"
    assert snapshots[0].observed_at.isoformat() == "2025-04-23T09:45:53.408000+00:00"
    assert [item.identity() for item in snapshots[0].components] == [
        "binance:BTCUSDT",
        "coinbase:BTC-USDT",
    ]
    assert snapshots[0].components[0].weight == 0.51282051
    assert snapshots[0].components[0].price == 94057.03


@pytest.mark.asyncio
async def test_binance_provider_only_fetches_supported_future_markets_once() -> None:
    client = FakeBinanceConstituentClient(
        {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "time": 1745401553408,
                "constituents": [
                    {
                        "exchange": "binance",
                        "symbol": "BTCUSDT",
                        "price": "94057.03000000",
                        "weight": "1",
                    }
                ],
            },
        }
    )
    provider = BinanceIndexComponentProvider(client=client)

    await provider.fetch_components(
        [
            market(symbol="BTCUSDT"),
            market(symbol="BTCUSDT", raw_symbol="BTCUSDT"),
            market(exchange="okx", symbol="BTCUSDT"),
            market(symbol="ETHUSDT", market_type=MarketType.SPOT),
            market(symbol="BNBUSDT", index_price=None),
        ]
    )

    assert client.urls == [
        "https://fapi.binance.com/fapi/v1/constituents?symbol=BTCUSDT",
    ]


@pytest.mark.asyncio
async def test_binance_provider_skips_empty_constituent_payloads() -> None:
    client = FakeBinanceConstituentClient(
        {
            "BTCUSDT": {"symbol": "BTCUSDT", "time": 1745401553408, "constituents": []},
        }
    )
    provider = BinanceIndexComponentProvider(client=client)

    snapshots = await provider.fetch_components([market(symbol="BTCUSDT")])

    assert snapshots == []


@pytest.mark.asyncio
async def test_binance_provider_batches_symbols_and_remembers_attempts() -> None:
    client = FakeBinanceConstituentClient(
        {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "time": 1745401553408,
                "constituents": [
                    {"exchange": "binance", "symbol": "BTCUSDT", "weight": "1"},
                ],
            },
            "ETHUSDT": {
                "symbol": "ETHUSDT",
                "time": 1745401553408,
                "constituents": [
                    {"exchange": "binance", "symbol": "ETHUSDT", "weight": "1"},
                ],
            },
            "BNBUSDT": {
                "symbol": "BNBUSDT",
                "time": 1745401553408,
                "constituents": [
                    {"exchange": "binance", "symbol": "BNBUSDT", "weight": "1"},
                ],
            },
        }
    )
    now = BASE_TIME
    provider = BinanceIndexComponentProvider(
        client=client,
        max_symbols_per_run=2,
        refresh_interval_seconds=600,
        now_fn=lambda: now,
    )

    await provider.fetch_components(
        [
            market(symbol="BTCUSDT"),
            market(symbol="ETHUSDT"),
            market(symbol="BNBUSDT"),
        ]
    )
    await provider.fetch_components(
        [
            market(symbol="BTCUSDT"),
            market(symbol="ETHUSDT"),
            market(symbol="BNBUSDT"),
        ]
    )
    now = BASE_TIME + timedelta(minutes=11)
    await provider.fetch_components(
        [
            market(symbol="BTCUSDT"),
            market(symbol="ETHUSDT"),
            market(symbol="BNBUSDT"),
        ]
    )

    assert client.urls == [
        "https://fapi.binance.com/fapi/v1/constituents?symbol=BNBUSDT",
        "https://fapi.binance.com/fapi/v1/constituents?symbol=BTCUSDT",
        "https://fapi.binance.com/fapi/v1/constituents?symbol=ETHUSDT",
        "https://fapi.binance.com/fapi/v1/constituents?symbol=BNBUSDT",
        "https://fapi.binance.com/fapi/v1/constituents?symbol=BTCUSDT",
    ]


@pytest.mark.asyncio
async def test_okx_provider_parses_index_components() -> None:
    url = "https://www.okx.com/api/v5/market/index-components?index=BTC-USDT"
    client = FakeIndexComponentClient(
        {
            url: {
                "code": "0",
                "data": [
                    {
                        "ts": "1745401553408",
                        "components": [
                            {
                                "exch": "Binance",
                                "symbol": "BTC-USDT",
                                "symPx": "94057.03",
                                "wgt": "0.51282051",
                            },
                            {
                                "exch": "Coinbase",
                                "symbol": "BTC-USD",
                                "symPx": "94140.58",
                                "wgt": "0.15384615",
                            },
                        ],
                    }
                ],
            }
        }
    )
    provider = OKXIndexComponentProvider(client=client)

    snapshots = await provider.fetch_components(
        [market(exchange="okx", symbol="BTCUSDT", raw_symbol="BTC-USDT-SWAP")]
    )

    assert client.urls == [url]
    assert len(snapshots) == 1
    assert snapshots[0].exchange == "okx"
    assert snapshots[0].symbol == "BTCUSDT"
    assert snapshots[0].source == "okx-index-components"
    assert snapshots[0].observed_at.isoformat() == "2025-04-23T09:45:53.408000+00:00"
    assert [item.identity() for item in snapshots[0].components] == [
        "binance:BTC-USDT",
        "coinbase:BTC-USD",
    ]
    assert snapshots[0].components[0].weight == 0.51282051
    assert snapshots[0].components[0].price == 94057.03


@pytest.mark.asyncio
async def test_bybit_provider_parses_index_price_components() -> None:
    url = "https://api.bybit.com/v5/market/index-price-components?indexName=BTCUSDT"
    client = FakeIndexComponentClient(
        {
            url: {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "quote": [
                                {
                                    "exchange": "Binance",
                                    "quoteSymbol": "BTCUSDT",
                                    "price": "94057.03",
                                    "weight": "0.51282051",
                                },
                                {
                                    "exchange": "Coinbase",
                                    "quoteSymbol": "BTC-USD",
                                    "price": "94140.58",
                                    "weight": "0.15384615",
                                },
                            ],
                        }
                    ]
                },
                "time": 1745401553408,
            }
        }
    )
    provider = BybitIndexComponentProvider(client=client)

    snapshots = await provider.fetch_components([market(exchange="bybit", symbol="BTCUSDT")])

    assert client.urls == [url]
    assert len(snapshots) == 1
    assert snapshots[0].exchange == "bybit"
    assert snapshots[0].symbol == "BTCUSDT"
    assert snapshots[0].source == "bybit-index-price-components"
    assert snapshots[0].observed_at.isoformat() == "2025-04-23T09:45:53.408000+00:00"
    assert [item.identity() for item in snapshots[0].components] == [
        "binance:BTCUSDT",
        "coinbase:BTC-USD",
    ]
    assert snapshots[0].components[0].weight == 0.51282051
    assert snapshots[0].components[0].price == 94057.03


@pytest.mark.asyncio
async def test_bitget_provider_parses_index_components() -> None:
    url = "https://api.bitget.com/api/v3/market/index-components?symbol=BTCUSDT"
    client = FakeIndexComponentClient(
        {
            url: {
                "code": "00000",
                "data": {
                    "symbol": "BTCUSDT",
                    "ts": "1745401553408",
                    "components": [
                        {
                            "exchange": "Binance",
                            "symbol": "BTCUSDT",
                            "price": "94057.03",
                            "weight": "0.51282051",
                        },
                        {
                            "exchange": "Coinbase",
                            "symbol": "BTC-USD",
                            "price": "94140.58",
                            "weight": "0.15384615",
                        },
                    ],
                },
            }
        }
    )
    provider = BitgetIndexComponentProvider(client=client)

    snapshots = await provider.fetch_components([market(exchange="bitget", symbol="BTCUSDT")])

    assert client.urls == [url]
    assert len(snapshots) == 1
    assert snapshots[0].exchange == "bitget"
    assert snapshots[0].symbol == "BTCUSDT"
    assert snapshots[0].source == "bitget-index-components"
    assert snapshots[0].observed_at.isoformat() == "2025-04-23T09:45:53.408000+00:00"
    assert [item.identity() for item in snapshots[0].components] == [
        "binance:BTCUSDT",
        "coinbase:BTC-USD",
    ]
    assert snapshots[0].components[0].weight == 0.51282051
    assert snapshots[0].components[0].price == 94057.03


@pytest.mark.asyncio
async def test_gate_provider_parses_index_constituents() -> None:
    url = "https://api.gateio.ws/api/v4/futures/usdt/index_constituents/BTC_USDT"
    client = FakeIndexComponentClient(
        {
            url: {
                "index": "BTC_USDT",
                "timestamp": 1745401553,
                "constituents": [
                    {
                        "exchange": "Binance",
                        "name": "BTC_USDT",
                        "index_price": "94057.03",
                        "weight": "0.51282051",
                    },
                    {
                        "exchange": "Coinbase",
                        "name": "BTC_USD",
                        "index_price": "94140.58",
                        "weight": "0.15384615",
                    },
                ],
            }
        }
    )
    provider = GateIndexComponentProvider(client=client)

    snapshots = await provider.fetch_components(
        [market(exchange="gate", symbol="BTCUSDT", raw_symbol="BTC_USDT")]
    )

    assert client.urls == [url]
    assert len(snapshots) == 1
    assert snapshots[0].exchange == "gate"
    assert snapshots[0].symbol == "BTCUSDT"
    assert snapshots[0].source == "gate-index-constituents"
    assert snapshots[0].observed_at.isoformat() == "2025-04-23T09:45:53+00:00"
    assert [item.identity() for item in snapshots[0].components] == [
        "binance:BTC_USDT",
        "coinbase:BTC_USD",
    ]
    assert snapshots[0].components[0].weight == 0.51282051
    assert snapshots[0].components[0].price == 94057.03


@pytest.mark.asyncio
async def test_multi_provider_combines_exchange_specific_snapshots() -> None:
    first_snapshot = snapshot([component("binance", "BTCUSDT", weight=1)], exchange="binance", symbol="BTCUSDT")
    second_snapshot = snapshot([component("okx", "BTC-USDT", weight=1)], exchange="okx", symbol="BTCUSDT")
    provider = MultiIndexComponentProvider(
        [
            StaticIndexComponentProvider([first_snapshot]),
            StaticIndexComponentProvider([second_snapshot]),
        ]
    )

    snapshots = await provider.fetch_components(
        [
            market(exchange="binance", symbol="BTCUSDT"),
            market(exchange="okx", symbol="BTCUSDT", raw_symbol="BTC-USDT-SWAP"),
        ]
    )

    assert snapshots == [first_snapshot, second_snapshot]
