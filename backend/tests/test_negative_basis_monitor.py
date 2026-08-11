from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.market import MarketSnapshot, MarketType
from app.models.negative_basis import NegativeBasisAutoScanSettings, NegativeBasisWatchItem
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadCurrentSnapshot,
    PairSpreadHourlyVolumePoint,
    PairSpreadKlinePoint,
    PairSpreadLegQuery,
    PairSpreadPoint,
    PairSpreadPriceField,
    PairSpreadQueryResult,
)
from app.models.settings import SymbolAlias
from app.services.negative_basis_monitor import (
    GateContractStatPoint,
    NegativeBasisMonitor,
    NegativeBasisMonitorRepository,
    build_negative_basis_analysis,
)
from app.services.pair_spread_query import build_pair_spread_points
from app.services.snapshot_store import SnapshotStore
from app.services.symbol_aliases import apply_symbol_aliases


def _current_leg(
    *,
    exchange: str,
    market_type: MarketType,
    price: float,
    volume: float | None = None,
    oi: float | None = None,
    long_pct: float | None = None,
    short_pct: float | None = None,
    lsr: float | None = None,
    funding: float | None = None,
) -> PairSpreadCurrentLeg:
    return PairSpreadCurrentLeg(
        exchange=exchange,
        symbol="PROMUSDT",
        market_type=market_type,
        raw_symbol="PROMUSDT" if exchange != "gate" else "PROM_USDT",
        price=price,
        price_field=PairSpreadPriceField.MID_PRICE,
        mark_price=price if market_type == MarketType.FUTURE else None,
        index_price=None,
        mid_price=price,
        last_price=price,
        volume_24h_usdt=volume,
        open_interest_usdt=oi,
        open_interest_contracts=None,
        long_account_pct=long_pct,
        short_account_pct=short_pct,
        long_account_count=None,
        short_account_count=None,
        long_short_ratio=lsr,
        funding_rate_pct=funding,
        funding_next_rate_pct=funding,
        funding_next_time=None,
        funding_interval_hours=4 if market_type == MarketType.FUTURE else None,
        funding_rate_upper_pct=None,
        funding_rate_lower_pct=None,
        timestamp=datetime(2026, 8, 11, 0, 6, tzinfo=UTC),
    )


def _prom_like_result() -> PairSpreadQueryResult:
    base = datetime(2026, 8, 11, 0, 0, tzinfo=UTC)
    spot_klines = [
        PairSpreadKlinePoint(bucket_at=base + timedelta(minutes=index), close=1.04, volume_usdt=1000)
        for index in range(5)
    ]
    future_klines = [
        PairSpreadKlinePoint(bucket_at=base + timedelta(minutes=index), close=1.0, volume_usdt=500)
        for index in range(5)
    ]
    points = build_pair_spread_points(spot_klines, future_klines)
    current_spot = _current_leg(
        exchange="binance",
        market_type=MarketType.SPOT,
        price=1.04,
        volume=2_500_000,
    )
    current_future = _current_leg(
        exchange="gate",
        market_type=MarketType.FUTURE,
        price=1.0,
        volume=900_000,
        oi=1350,
        long_pct=62,
        short_pct=38,
        lsr=1.63,
        funding=-2.0,
    )
    current_spread_abs = current_future.price - current_spot.price
    current_spread_pct = current_spread_abs / ((current_spot.price + current_future.price) / 2) * 100
    return PairSpreadQueryResult(
        leg1=PairSpreadLegQuery(exchange="binance", symbol="PROMUSDT", market_type=MarketType.SPOT),
        leg2=PairSpreadLegQuery(exchange="gate", symbol="PROMUSDT", market_type=MarketType.FUTURE),
        hours=4,
        interval_minutes=1,
        interval_seconds=60,
        leg2_multiplier=1,
        observed_at=base + timedelta(minutes=6),
        point_count=len(points),
        first_seen_at=points[0].bucket_at,
        last_seen_at=points[-1].bucket_at,
        spread_abs={"min": None, "max": None, "mean": None, "current": None},
        spread_pct={"min": None, "max": None, "mean": None, "current": None},
        current=PairSpreadCurrentSnapshot(
            observed_at=base + timedelta(minutes=6),
            leg1=current_spot,
            leg2=current_future,
            spread_abs=current_spread_abs,
            spread_pct=current_spread_pct,
        ),
        points=points,
        hourly_volume=[
            PairSpreadHourlyVolumePoint(
                bucket_at=base - timedelta(hours=1),
                leg1_volume_usdt=100_000,
                leg2_volume_usdt=50_000,
                total_volume_usdt=150_000,
                volume_diff_usdt=-50_000,
                volume_ratio=0.5,
            ),
            PairSpreadHourlyVolumePoint(
                bucket_at=base,
                leg1_volume_usdt=420_000,
                leg2_volume_usdt=160_000,
                total_volume_usdt=580_000,
                volume_diff_usdt=-260_000,
                volume_ratio=0.38,
            ),
        ],
        funding_history=[],
        realtime_funding=[],
        warnings=[],
    )


def _gate_stats() -> list[GateContractStatPoint]:
    base = datetime(2026, 8, 11, 0, 0, tzinfo=UTC)
    return [
        GateContractStatPoint(bucket_at=base, open_interest_usdt=1000, long_short_ratio=1.1),
        GateContractStatPoint(
            bucket_at=base + timedelta(minutes=5),
            open_interest_usdt=1350,
            long_account_pct=62,
            short_account_pct=38,
            long_short_ratio=1.63,
            funding_rate_pct=-2.0,
        ),
    ]


def test_negative_basis_watch_item_defaults_to_same_symbol() -> None:
    item = NegativeBasisWatchItem(symbol="prom", spot_exchange="Binance", future_exchange="Gate")

    assert item.symbol == "PROMUSDT"
    assert item.spot_symbol == "PROMUSDT"
    assert item.future_symbol == "PROMUSDT"
    assert item.future_multiplier == 1
    assert item.spot_leg().market_type == MarketType.SPOT
    assert item.future_leg().market_type == MarketType.FUTURE


def test_negative_basis_auto_scan_settings_normalizes_blocklist() -> None:
    settings = NegativeBasisAutoScanSettings(
        blocked_exchanges=["Gate", "gate", " Binance "],
        blocked_symbols=["prom", "PROM_USDT", " dexe-usdt "],
    )

    assert settings.blocked_exchanges == ["gate", "binance"]
    assert settings.blocked_symbols == ["PROMUSDT", "DEXEUSDT"]
    assert settings.blocked_exchange_symbols == ["gate:EDGEUSDT"]


def test_negative_basis_analysis_detects_strong_prom_like_setup() -> None:
    item = NegativeBasisWatchItem(symbol="PROM", spot_exchange="binance", future_exchange="gate")

    result = build_negative_basis_analysis(item, _prom_like_result(), gate_stats=_gate_stats())

    assert result.signal_level == "strong"
    assert result.spot_premium.current == pytest.approx(3.92156862745)
    assert result.thresholds[0].first_consecutive_at == datetime(2026, 8, 11, 0, 2, tzinfo=UTC)
    current_hour = result.hourly_stats[-1]
    assert current_hour.spot_volume_growth == pytest.approx(4.2)
    assert current_hour.open_interest_change_pct == pytest.approx(35.0)
    assert any("现货小时成交额放大" in reason for reason in result.reasons)
    assert any("合约 OI 小时变化" in reason for reason in result.reasons)


class FakePairSpreadQueryService:
    async def query(self, *args, **kwargs) -> PairSpreadQueryResult:  # noqa: ANN002, ANN003
        return _prom_like_result()

    async def aclose(self) -> None:
        return None


class FakeGateStatsClient:
    async def fetch(self, *args, **kwargs) -> list[GateContractStatPoint]:  # noqa: ANN002, ANN003
        return _gate_stats()

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_negative_basis_monitor_records_and_alerts() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NegativeBasisMonitorRepository(db)
    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    item = NegativeBasisWatchItem(symbol="PROM", spot_exchange="binance", future_exchange="gate")
    await repo.upsert_watch_item(item)
    monitor = NegativeBasisMonitor(
        repo,
        query_service_factory=FakePairSpreadQueryService,  # type: ignore[arg-type]
        gate_stats_client=FakeGateStatsClient(),  # type: ignore[arg-type]
        alert_sender=send_message,
    )

    try:
        result = await monitor.collect_watch_item(item)
        samples = await repo.list_samples(watch_id=item.id)
        events = await repo.list_events(watch_id=item.id)
    finally:
        await monitor.aclose()
        await db.close()

    assert result.signal_level == "strong"
    assert len(samples) == 1
    assert samples[0].signal_level == "strong"
    assert samples[0].open_interest_change_pct == pytest.approx(35)
    assert len(events) == 1
    assert "PROMUSDT" in events[0].message
    assert len(sent_messages) == 1


@pytest.mark.asyncio
async def test_negative_basis_monitor_auto_discovers_spot_premium_candidates() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NegativeBasisMonitorRepository(db)
    store = SnapshotStore()
    observed_at = datetime(2026, 8, 11, 0, 6, tzinfo=UTC)
    store.set_markets(
        [
            MarketSnapshot(
                symbol="PROMUSDT",
                base="PROM",
                exchange="binance",
                market_type=MarketType.SPOT,
                bid=1.039,
                ask=1.041,
                volume_24h_usdt=2_000_000,
                timestamp=observed_at,
                raw_symbol="PROMUSDT",
            ),
            MarketSnapshot(
                symbol="PROMUSDT",
                base="PROM",
                exchange="gate",
                market_type=MarketType.FUTURE,
                bid=0.999,
                ask=1.001,
                mark_price=1.0,
                volume_24h_usdt=800_000,
                timestamp=observed_at,
                raw_symbol="PROM_USDT",
            ),
        ]
    )
    monitor = NegativeBasisMonitor(
        repo,
        snapshot_store=store,
        gate_stats_client=FakeGateStatsClient(),  # type: ignore[arg-type]
    )

    try:
        candidates = await monitor.discover_auto_candidates(force=True)
        watchlist = await repo.list_watch_items()
    finally:
        await monitor.aclose()
        await db.close()

    assert len(candidates) == 1
    assert candidates[0].symbol == "PROMUSDT"
    assert candidates[0].signal_level == "strong"
    assert candidates[0].spot_premium_pct == pytest.approx(3.92156862745)
    assert len(watchlist) == 1
    assert watchlist[0].auto_managed is True
    assert watchlist[0].spot_exchange == "binance"
    assert watchlist[0].future_exchange == "gate"
    assert watchlist[0].spot_symbol == "PROMUSDT"
    assert watchlist[0].future_symbol == "PROMUSDT"
    assert watchlist[0].future_multiplier == 1


@pytest.mark.asyncio
async def test_negative_basis_monitor_auto_discovery_uses_symbol_alias_multiplier() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NegativeBasisMonitorRepository(db)
    store = SnapshotStore()
    observed_at = datetime(2026, 8, 11, 0, 6, tzinfo=UTC)
    markets = apply_symbol_aliases(
        [
            MarketSnapshot(
                symbol="NEXUSDT",
                base="NEX",
                exchange="bitget",
                market_type=MarketType.SPOT,
                bid=0.01039,
                ask=0.01041,
                volume_24h_usdt=2_000_000,
                timestamp=observed_at,
                raw_symbol="NEXUSDT",
            ),
            MarketSnapshot(
                symbol="10000NEXUSDT",
                base="10000NEX",
                exchange="gate",
                market_type=MarketType.FUTURE,
                bid=99.9,
                ask=100.1,
                mark_price=100.0,
                volume_24h_usdt=800_000,
                timestamp=observed_at,
                raw_symbol="10000NEX_USDT",
            ),
        ],
        [
            SymbolAlias(
                exchange="bitget",
                symbol="NEX",
                canonical_symbol="10000NEX",
                market_type=MarketType.SPOT,
                price_multiplier=10_000,
            )
        ],
    )
    store.set_markets(markets)
    monitor = NegativeBasisMonitor(
        repo,
        snapshot_store=store,
        gate_stats_client=FakeGateStatsClient(),  # type: ignore[arg-type]
    )

    try:
        candidates = await monitor.discover_auto_candidates(force=True)
        watchlist = await repo.list_watch_items()
    finally:
        await monitor.aclose()
        await db.close()

    assert len(candidates) == 1
    assert candidates[0].symbol == "10000NEXUSDT"
    assert candidates[0].spot_symbol == "NEXUSDT"
    assert candidates[0].future_symbol == "10000NEXUSDT"
    assert candidates[0].future_multiplier == pytest.approx(10_000)
    assert candidates[0].spot_price == pytest.approx(104)
    assert candidates[0].future_price == pytest.approx(100)
    assert any(
        "现货映射 NEXUSDT->10000NEXUSDT" in reason
        for reason in candidates[0].selection_reasons
    )
    assert len(watchlist) == 1
    assert watchlist[0].symbol == "10000NEXUSDT"
    assert watchlist[0].spot_symbol == "NEXUSDT"
    assert watchlist[0].future_symbol == "10000NEXUSDT"
    assert watchlist[0].future_multiplier == pytest.approx(10_000)


@pytest.mark.asyncio
async def test_negative_basis_monitor_auto_scan_blocks_gate_edge_same_name() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NegativeBasisMonitorRepository(db)
    store = SnapshotStore()
    observed_at = datetime(2026, 8, 11, 0, 6, tzinfo=UTC)
    store.set_markets(
        [
            MarketSnapshot(
                symbol="EDGEUSDT",
                base="EDGE",
                exchange="okx",
                market_type=MarketType.SPOT,
                bid=1.0,
                ask=1.01,
                volume_24h_usdt=1_500_000,
                timestamp=observed_at,
                raw_symbol="EDGE-USDT",
            ),
            MarketSnapshot(
                symbol="EDGEUSDT",
                base="EDGE",
                exchange="gate",
                market_type=MarketType.FUTURE,
                bid=0.49,
                ask=0.51,
                mark_price=0.5,
                volume_24h_usdt=2_000_000,
                timestamp=observed_at,
                raw_symbol="EDGE_USDT",
            ),
        ]
    )
    monitor = NegativeBasisMonitor(
        repo,
        snapshot_store=store,
        gate_stats_client=FakeGateStatsClient(),  # type: ignore[arg-type]
    )

    try:
        candidates = await monitor.discover_auto_candidates(force=True)
        watchlist = await repo.list_watch_items()
    finally:
        await monitor.aclose()
        await db.close()

    assert candidates == []
    assert watchlist == []


@pytest.mark.asyncio
async def test_negative_basis_monitor_exchange_symbol_block_uses_original_symbol() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NegativeBasisMonitorRepository(db)
    store = SnapshotStore()
    observed_at = datetime(2026, 8, 11, 0, 6, tzinfo=UTC)
    markets = apply_symbol_aliases(
        [
            MarketSnapshot(
                symbol="EDGEUSDT",
                base="EDGE",
                exchange="okx",
                market_type=MarketType.SPOT,
                bid=1.039,
                ask=1.041,
                volume_24h_usdt=1_500_000,
                timestamp=observed_at,
                raw_symbol="EDGE-USDT",
            ),
            MarketSnapshot(
                symbol="EDGEXUSDT",
                base="EDGEX",
                exchange="gate",
                market_type=MarketType.FUTURE,
                bid=0.999,
                ask=1.001,
                mark_price=1.0,
                volume_24h_usdt=2_000_000,
                timestamp=observed_at,
                raw_symbol="EDGEX_USDT",
            ),
        ],
        [SymbolAlias(exchange="gate", symbol="EDGEXUSDT", canonical_symbol="EDGEUSDT")],
    )
    store.set_markets(markets)
    monitor = NegativeBasisMonitor(
        repo,
        snapshot_store=store,
        gate_stats_client=FakeGateStatsClient(),  # type: ignore[arg-type]
    )

    try:
        candidates = await monitor.discover_auto_candidates(force=True)
        watchlist = await repo.list_watch_items()
    finally:
        await monitor.aclose()
        await db.close()

    assert len(candidates) == 1
    assert candidates[0].symbol == "EDGEUSDT"
    assert candidates[0].future_symbol == "EDGEXUSDT"
    assert len(watchlist) == 1
    assert watchlist[0].future_symbol == "EDGEXUSDT"


@pytest.mark.asyncio
async def test_negative_basis_monitor_auto_scan_keeps_best_route_per_symbol() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NegativeBasisMonitorRepository(db)
    store = SnapshotStore()
    observed_at = datetime(2026, 8, 11, 0, 6, tzinfo=UTC)
    store.set_markets(
        [
            MarketSnapshot(
                symbol="ALLOUSDT",
                base="ALLO",
                exchange="aster",
                market_type=MarketType.SPOT,
                bid=2.54,
                ask=2.56,
                volume_24h_usdt=3_000_000,
                timestamp=observed_at,
                raw_symbol="ALLOUSDT",
            ),
            MarketSnapshot(
                symbol="ALLOUSDT",
                base="ALLO",
                exchange="gate",
                market_type=MarketType.FUTURE,
                bid=0.299,
                ask=0.301,
                mark_price=0.3,
                volume_24h_usdt=500_000,
                timestamp=observed_at,
                raw_symbol="ALLO_USDT",
            ),
            MarketSnapshot(
                symbol="ALLOUSDT",
                base="ALLO",
                exchange="binance",
                market_type=MarketType.FUTURE,
                bid=0.301,
                ask=0.303,
                mark_price=0.302,
                volume_24h_usdt=30_000_000,
                timestamp=observed_at,
                raw_symbol="ALLOUSDT",
            ),
        ]
    )
    monitor = NegativeBasisMonitor(
        repo,
        snapshot_store=store,
        gate_stats_client=FakeGateStatsClient(),  # type: ignore[arg-type]
    )

    try:
        candidates = await monitor.discover_auto_candidates(force=True)
        watchlist = await repo.list_watch_items()
    finally:
        await monitor.aclose()
        await db.close()

    assert len(candidates) == 1
    assert candidates[0].symbol == "ALLOUSDT"
    assert candidates[0].future_exchange == "binance"
    assert candidates[0].selection_score > 0
    assert len(watchlist) == 1
    assert watchlist[0].id == "auto:aster:binance:ALLOUSDT"


@pytest.mark.asyncio
async def test_negative_basis_monitor_blocklist_removes_auto_items_but_keeps_manual() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NegativeBasisMonitorRepository(db)
    store = SnapshotStore()
    observed_at = datetime(2026, 8, 11, 0, 6, tzinfo=UTC)
    store.set_markets(
        [
            MarketSnapshot(
                symbol="PROMUSDT",
                base="PROM",
                exchange="binance",
                market_type=MarketType.SPOT,
                bid=1.039,
                ask=1.041,
                volume_24h_usdt=2_000_000,
                timestamp=observed_at,
                raw_symbol="PROMUSDT",
            ),
            MarketSnapshot(
                symbol="PROMUSDT",
                base="PROM",
                exchange="gate",
                market_type=MarketType.FUTURE,
                bid=0.999,
                ask=1.001,
                mark_price=1.0,
                volume_24h_usdt=800_000,
                timestamp=observed_at,
                raw_symbol="PROM_USDT",
            ),
        ]
    )
    manual = NegativeBasisWatchItem(
        id="manual-prom",
        symbol="PROM",
        spot_exchange="binance",
        future_exchange="gate",
    )
    await repo.upsert_watch_item(manual)
    monitor = NegativeBasisMonitor(
        repo,
        snapshot_store=store,
        gate_stats_client=FakeGateStatsClient(),  # type: ignore[arg-type]
    )

    try:
        first_candidates = await monitor.discover_auto_candidates(force=True)
        watchlist_after_scan = await repo.list_watch_items()
        settings = await monitor.block_auto_symbol("prom")
        second_candidates = await monitor.discover_auto_candidates(force=True)
        watchlist_after_block = await repo.list_watch_items()
    finally:
        await monitor.aclose()
        await db.close()

    assert len(first_candidates) == 1
    assert {item.id for item in watchlist_after_scan} == {
        "manual-prom",
        "auto:binance:gate:PROMUSDT",
    }
    assert settings.blocked_symbols == ["PROMUSDT"]
    assert second_candidates == []
    assert [item.id for item in watchlist_after_block] == ["manual-prom"]


def test_negative_basis_monitor_api_saves_watch_item() -> None:
    app = create_app(
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
        )
    )
    headers = {"X-Dashboard-Password": "secret"}

    with TestClient(app) as client:
        response = client.post(
            "/api/negative-basis-monitor/watchlist",
            headers=headers,
            json={
                "id": "prom-watch",
                "enabled": False,
                "symbol": "prom",
                "spot_exchange": "Binance",
                "future_exchange": "Gate",
                "spot_symbol": None,
                "future_symbol": None,
                "future_multiplier": 1,
                "interval_seconds": 60,
                "lookback_hours": 4,
                "retention_hours": 720,
                "watch_threshold_pct": 0.5,
                "building_threshold_pct": 1,
                "confirmed_threshold_pct": 2,
                "strong_threshold_pct": 3,
                "extreme_threshold_pct": 10,
                "watch_consecutive_hits": 3,
                "building_consecutive_hits": 3,
                "confirmed_consecutive_hits": 3,
                "strong_consecutive_hits": 2,
                "extreme_consecutive_hits": 1,
                "spot_volume_growth_threshold": 3,
                "oi_confirmed_growth_pct": 20,
                "oi_strong_growth_pct": 30,
                "min_spot_hourly_volume_usdt": 0,
                "alert_min_level": "watch",
                "cooldown_seconds": 900,
                "note": "PROM 负基差埋伏",
                "created_at": "2026-08-11T00:00:00Z",
                "updated_at": "2026-08-11T00:00:00Z",
            },
        )
        status_response = client.get("/api/negative-basis-monitor/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "PROMUSDT"
    assert payload["spot_symbol"] == "PROMUSDT"
    assert payload["future_symbol"] == "PROMUSDT"
    assert payload["spot_exchange"] == "binance"
    assert payload["future_exchange"] == "gate"
    assert status_response.status_code == 200
    assert status_response.json()["watch_count"] == 1


def test_negative_basis_monitor_api_updates_auto_scan_blocklist() -> None:
    app = create_app(
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
        )
    )
    headers = {"X-Dashboard-Password": "secret"}

    with TestClient(app) as client:
        block_symbol_response = client.post(
            "/api/negative-basis-monitor/auto-scan/block-symbol",
            headers=headers,
            params={"symbol": "prom"},
        )
        block_exchange_response = client.post(
            "/api/negative-basis-monitor/auto-scan/block-exchange",
            headers=headers,
            params={"exchange": "Gate"},
        )
        block_exchange_symbol_response = client.post(
            "/api/negative-basis-monitor/auto-scan/block-exchange-symbol",
            headers=headers,
            params={"exchange": "Binance", "symbol": "PROM"},
        )
        status_response = client.get("/api/negative-basis-monitor/status")
        unblock_symbol_response = client.delete(
            "/api/negative-basis-monitor/auto-scan/block-symbol",
            headers=headers,
            params={"symbol": "PROMUSDT"},
        )
        unblock_exchange_symbol_response = client.delete(
            "/api/negative-basis-monitor/auto-scan/block-exchange-symbol",
            headers=headers,
            params={"exchange": "binance", "symbol": "PROMUSDT"},
        )

    assert block_symbol_response.status_code == 200
    assert block_symbol_response.json()["blocked_symbols"] == ["PROMUSDT"]
    assert block_exchange_response.status_code == 200
    assert block_exchange_response.json()["blocked_exchanges"] == ["gate"]
    assert block_exchange_symbol_response.status_code == 200
    assert "binance:PROMUSDT" in block_exchange_symbol_response.json()["blocked_exchange_symbols"]
    assert status_response.status_code == 200
    assert status_response.json()["auto_scan_settings"]["blocked_symbols"] == ["PROMUSDT"]
    assert status_response.json()["auto_scan_settings"]["blocked_exchanges"] == ["gate"]
    assert "binance:PROMUSDT" in status_response.json()["auto_scan_settings"]["blocked_exchange_symbols"]
    assert "gate:EDGEUSDT" in status_response.json()["auto_scan_settings"]["blocked_exchange_symbols"]
    assert unblock_symbol_response.status_code == 200
    assert unblock_symbol_response.json()["blocked_symbols"] == []
    assert unblock_exchange_symbol_response.status_code == 200
    assert "binance:PROMUSDT" not in unblock_exchange_symbol_response.json()["blocked_exchange_symbols"]
