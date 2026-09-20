from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import connect_database
from app.db.repositories import IndexComponentRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.index_component import (
    IndexComponent,
    IndexComponentChange,
    IndexComponentSnapshot,
    IndexComponentAutoWatchSettings,
    IndexComponentWatchItem,
    stable_component_hash,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.pair_spread import PairSpreadPreset
from app.models.settings import FloatingWatchMutation
from app.services.index_components import (
    BitgetIndexComponentProvider,
    BinanceIndexComponentProvider,
    BybitIndexComponentProvider,
    GateIndexComponentProvider,
    IndexComponentMonitor,
    IndexComponentAutoWatchService,
    MultiIndexComponentProvider,
    OKXIndexComponentProvider,
    build_index_component_alert_message,
)
from app.services.pair_spread_presets import PairSpreadPresetRepository


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
async def test_component_change_alert_includes_live_index_trend_and_followup_after_15_minutes() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    clock = [BASE_TIME - timedelta(minutes=15)]
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        await repo.create_watch_item(IndexComponentWatchItem(symbol="VANRY"))
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append, now_fn=lambda: clock[0])

        def market(price: float) -> MarketSnapshot:
            return MarketSnapshot(
                symbol="VANRYUSDT", base="VANRY", exchange="binance",
                market_type=MarketType.FUTURE, bid=price, ask=price,
                index_price=price, mark_price=price, raw_symbol="VANRYUSDT",
                timestamp=clock[0],
            )

        await monitor.observe_markets([market(98)])
        await monitor.process_snapshots([snapshot([component("binance", "VANRYUSDT", 1)])])
        clock[0] = BASE_TIME - timedelta(minutes=5)
        await monitor.observe_markets([market(99)])
        clock[0] = BASE_TIME
        await monitor.observe_markets([market(100)])
        changes = await monitor.process_snapshots(
            [snapshot([component("binance", "VANRYUSDT", 0.7),
                       component("gate", "VANRYUSDT", 0.3)])]
        )
        assert changes[0].alert_status == "sent"
        assert "发现变更时指数价 100" in alerts[0]
        assert "检测前约5分钟 上涨 +1.010%" in alerts[0]
        assert "检测前约15分钟 上涨 +2.041%" in alerts[0]
        assert "近况不代表变更后走势" in alerts[0]

        # A new monitor simulates restart: the follow-up must survive in the database.
        restarted = IndexComponentMonitor(repo, alert_sender=alerts.append, now_fn=lambda: clock[0])
        clock[0] = BASE_TIME + timedelta(minutes=5)
        await restarted.observe_markets([market(101)])
        assert len(alerts) == 1
        clock[0] = BASE_TIME + timedelta(minutes=15)
        await restarted.observe_markets([market(103)])
        assert len(alerts) == 2
        assert "发现成分变更后的指数走势（实测）" in alerts[1]
        assert "发现变更后约5分钟 101（上涨 +1.000%）" in alerts[1]
        assert "发现变更后约15分钟 103（上涨 +3.000%）" in alerts[1]
        await restarted.observe_markets([market(104)])
        assert len(alerts) == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_component_trend_never_uses_stale_prices_or_notifies_after_unwatch() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    clock = [BASE_TIME]
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        item = await repo.create_watch_item(IndexComponentWatchItem(symbol="VANRY"))
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append, now_fn=lambda: clock[0])
        stale = MarketSnapshot(
            symbol="VANRYUSDT", base="VANRY", exchange="binance",
            market_type=MarketType.FUTURE, bid=100, ask=100,
            index_price=100, raw_symbol="VANRYUSDT",
            timestamp=BASE_TIME - timedelta(minutes=10),
        )
        await monitor.observe_markets([stale])
        await monitor.process_snapshots([snapshot([component("binance", "VANRYUSDT", 1)])])
        await monitor.process_snapshots([snapshot([component("gate", "VANRYUSDT", 1)])])
        assert "暂无新鲜指数价" in alerts[0]
        assert await repo.list_due_trend_followups(clock[0] + timedelta(hours=1)) == []

        fresh = stale.model_copy(update={"timestamp": clock[0]})
        await monitor.observe_markets([fresh])
        await monitor.process_snapshots([snapshot([component("binance", "VANRYUSDT", 1)])])
        assert "发现变更时指数价 100" in alerts[1]
        assert "样本不足" in alerts[1]
        await repo.delete_watch_item(item.id)
        clock[0] += timedelta(minutes=15)
        await monitor.observe_markets([fresh.model_copy(update={"timestamp": clock[0], "index_price": 110})])
        assert len(alerts) == 2
        assert await repo.list_due_trend_followups(clock[0]) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_index_trend_uses_real_sample_time_and_unscaled_exchange_price() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    clock = [BASE_TIME + timedelta(seconds=37)]
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        await repo.create_watch_item(IndexComponentWatchItem(symbol="1000PEPE"))
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append, now_fn=lambda: clock[0])

        def aliased_market(price: float) -> MarketSnapshot:
            return MarketSnapshot(
                symbol="PEPEUSDT", base="PEPE", exchange="binance",
                market_type=MarketType.FUTURE, bid=price, ask=price,
                index_price=price, raw_symbol="1000PEPEUSDT", timestamp=clock[0],
                symbol_alias_original_symbol="1000PEPEUSDT",
                symbol_alias_price_multiplier=10,
            )

        await monitor.observe_markets([aliased_market(100)])
        await monitor.process_snapshots([snapshot(
            [component("binance", "1000PEPEUSDT", 1)], symbol="1000PEPEUSDT"
        )])
        await monitor.process_snapshots([snapshot(
            [component("bybit", "1000PEPEUSDT", 1)], symbol="1000PEPEUSDT"
        )])
        assert "发现变更时指数价 10" in alerts[0]

        clock[0] = BASE_TIME + timedelta(minutes=15, seconds=20)
        await monitor.observe_markets([aliased_market(110)])
        clock[0] = BASE_TIME + timedelta(minutes=15, seconds=37)
        await monitor.observe_markets([aliased_market(110)])
        assert len(alerts) == 1  # The 15-minute sample is 17 seconds before the target.
        clock[0] = BASE_TIME + timedelta(minutes=16)
        await monitor.observe_markets([aliased_market(120)])
        assert len(alerts) == 2
        assert "发现变更后约15分钟 12（上涨 +20.000%）" in alerts[1]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_index_change_alert_survives_trend_history_failure(monkeypatch) -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        await repo.create_watch_item(IndexComponentWatchItem(symbol="VANRY"))
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append, now_fn=lambda: BASE_TIME)
        live = MarketSnapshot(
            symbol="VANRYUSDT", base="VANRY", exchange="binance",
            market_type=MarketType.FUTURE, bid=100, ask=100,
            index_price=100, raw_symbol="VANRYUSDT", timestamp=BASE_TIME,
        )
        await monitor.observe_markets([live])
        await monitor.process_snapshots([snapshot([component("binance", "VANRYUSDT", 1)])])

        async def unavailable(*args):
            raise RuntimeError("history temporarily unavailable")

        monkeypatch.setattr(repo, "list_index_prices", unavailable)
        changes = await monitor.process_snapshots([snapshot([component("gate", "VANRYUSDT", 1)])])
        assert changes[0].alert_status == "sent"
        assert "指数价数据暂不可用" in alerts[0]
        assert await repo.get_snapshot("binance", "VANRYUSDT") == snapshot(
            [component("gate", "VANRYUSDT", 1)]
        )
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
async def test_auto_watch_tracks_floating_pairs_running_cards_and_positions() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        settings = SettingsRepository(db)
        presets = PairSpreadPresetRepository(db)
        await repo.create_watch_item(IndexComponentWatchItem(symbol="MANUAL"))
        await repo.create_watch_item(IndexComponentWatchItem(symbol="VANRY"))
        await settings.mutate_floating_watch_settings(
            FloatingWatchMutation(action="add", item_type="symbol", value="ttwo")
        )
        await settings.mutate_floating_watch_settings(
            FloatingWatchMutation(action="add", item_type="pair", value="pair-1")
        )
        await presets.upsert(PairSpreadPreset(
            id="pair-1", leg1_exchange="binance", leg1_symbol="OPENAIUSDT",
            leg2_exchange="hyperliquid", leg2_symbol="io:OAI",
            saved_at=BASE_TIME,
        ))

        class Astro:
            pairs = [
                {"name": "BTC", "type": "FF", "status": True, "aExPosition": "0"},
                {"name": "VANRY", "type": "FF", "status": False, "aExPosition": "-1"},
                {"name": "OPENAI-OAI", "type": "FR", "status": True},
                {"name": "UNUSED", "type": "FF", "status": False},
            ]
            fail = False

            async def list_pairs(self):
                if self.fail:
                    raise RuntimeError("Astro temporarily unavailable")
                return self.pairs

        astro = Astro()
        auto = IndexComponentAutoWatchService(repo, settings, presets, astro)
        alerts: list[str] = []
        monitor = IndexComponentMonitor(repo, alert_sender=alerts.append, auto_watch=auto)
        assert await monitor.watched_symbols() == {"MANUAL", "VANRY"}

        enabled = await auto.configure(IndexComponentAutoWatchSettings(enabled=True))
        by_source = {}
        for item in enabled.items:
            by_source.setdefault(item.source, set()).add(item.symbol)
        assert by_source == {
            "floating_symbols": {"TTWOUSDT"},
            "floating_pairs": {"OPENAIUSDT", "OAIUSDT"},
            "astro_cards": {"BTCUSDT", "OPENAIUSDT", "OAIUSDT"},
            "positions": {"VANRYUSDT"},
        }
        assert await repo.is_symbol_watched("TTWOUSDT")
        assert await repo.is_symbol_watched("VANRYUSDT")
        assert not await repo.is_symbol_watched("TTWOXUSDT")
        await monitor.process_snapshots([
            snapshot([component("binance", "TTWOUSDT", weight=0.7)], symbol="TTWOUSDT")
        ])
        notified = await monitor.process_snapshots([
            snapshot(
                [component("binance", "TTWOUSDT", weight=0.6)],
                symbol="TTWOUSDT", observed_at=BASE_TIME + timedelta(minutes=5),
            )
        ])
        assert notified[0].alert_status == "sent"
        assert len(alerts) == 1

        await settings.mutate_floating_watch_settings(
            FloatingWatchMutation(action="remove", item_type="symbol", value="ttwo")
        )
        await settings.mutate_floating_watch_settings(
            FloatingWatchMutation(action="remove", item_type="pair", value="pair-1")
        )
        astro.pairs = [{"name": "BTC", "type": "FF", "status": False}]
        await auto.sync(force=True)
        assert await monitor.watched_symbols() == {"MANUAL", "VANRY"}
        assert not await repo.is_symbol_watched("TTWOUSDT")
        assert await repo.is_symbol_watched("MANUALUSDT")
        assert await repo.is_symbol_watched("VANRYUSDT")
        muted = await monitor.process_snapshots([
            snapshot(
                [component("binance", "TTWOUSDT", weight=0.5)],
                symbol="TTWOUSDT", observed_at=BASE_TIME + timedelta(minutes=10),
            )
        ])
        assert muted[0].alert_status == "muted"
        assert len(alerts) == 1

        await auto.configure(IndexComponentAutoWatchSettings(enabled=False))
        assert await repo.list_auto_watch_items() == []
        assert {item.symbol for item in await repo.list_watch_items()} == {"MANUAL", "VANRY"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_auto_watch_preserves_positions_when_astro_fails() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = IndexComponentRepository(db)
        settings = SettingsRepository(db)
        presets = PairSpreadPresetRepository(db)

        class Astro:
            fail = False

            async def list_pairs(self):
                if self.fail:
                    raise RuntimeError("offline")
                return [{"name": "TTWO", "type": "FF", "status": False, "bExPosition": "2"}]

        astro = Astro()
        auto = IndexComponentAutoWatchService(repo, settings, presets, astro)
        await auto.configure(IndexComponentAutoWatchSettings(enabled=True))
        astro.fail = True
        status = await auto.sync(force=True)

        assert status.error and "Astro" in status.error
        assert [(item.source, item.symbol) for item in status.items] == [
            ("positions", "TTWOUSDT")
        ]
        assert await repo.is_symbol_watched("TTWOUSDT")
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
async def test_bitget_provider_parses_component_list_payload() -> None:
    url = "https://api.bitget.com/api/v3/market/index-components?symbol=ESPORTSUSDT"
    client = FakeIndexComponentClient(
        {
            url: {
                "code": "00000",
                "data": {
                    "symbol": "ESPORTSUSDT",
                    "ts": "1745401553408",
                    "componentList": [
                        {
                            "exchange": "BITGET_FUTURE",
                            "spotPair": "ESPORTS/USDT",
                            "equivalentPrice": "0.24936",
                            "weight": "0.6",
                        },
                        {
                            "exchange": "1INCH_BSC",
                            "spotPair": "ESPORTS/USD",
                            "equivalentPrice": "0.24254",
                            "weight": "0.35",
                        },
                        {
                            "exchange": "BINANCE_INDEX",
                            "spotPair": "ESPORTS/USDT",
                            "equivalentPrice": "0.24264",
                            "weight": "0.05",
                        },
                    ],
                },
            }
        }
    )
    provider = BitgetIndexComponentProvider(client=client)

    snapshots = await provider.fetch_components([market(exchange="bitget", symbol="ESPORTSUSDT")])

    assert client.urls == [url]
    assert len(snapshots) == 1
    assert snapshots[0].exchange == "bitget"
    assert snapshots[0].symbol == "ESPORTSUSDT"
    assert [item.identity() for item in snapshots[0].components] == [
        "1inch_bsc:ESPORTS/USD",
        "binance_index:ESPORTS/USDT",
        "bitget_future:ESPORTS/USDT",
    ]
    assert [item.weight for item in snapshots[0].components] == [0.35, 0.05, 0.6]
    assert [item.price for item in snapshots[0].components] == [0.24254, 0.24264, 0.24936]


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
