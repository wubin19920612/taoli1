from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.db.repositories import AnnouncementRepository
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.announcement import AnnouncementKind, AnnouncementSettings, ExchangeAnnouncement
from app.models.astro import AstroAlertActionResult
from app.models.new_listing import NewListingWatchItem
from app.models.second_level_sampling import SecondLevelMarketSample
from app.services.announcements import AnnouncementMonitor
from app.services.new_listing_monitor import NewListingMonitor, NewListingMonitorRepository
from app.services.new_listing_monitor import NewListingPrewarmer


class UnitreeFetcher:
    async def fetch(self, exchange: str, symbol: str) -> SecondLevelMarketSample:
        observed_at = datetime(2026, 8, 4, 10, 3, tzinfo=UTC)
        if exchange == "gate":
            return SecondLevelMarketSample(
                observed_at=observed_at,
                exchange=exchange,
                symbol=symbol,
                status="ok",
                future_bid=34.5,
                future_ask=34.6,
                future_bid_size=20,
                future_ask_size=20,
                latency_ms=120,
            )
        return SecondLevelMarketSample(
            observed_at=observed_at,
            exchange=exchange,
            symbol=symbol,
            status="ok",
            future_bid=68.6,
            future_ask=68.7,
            future_bid_size=15,
            future_ask_size=15,
            latency_ms=110,
        )

    async def aclose(self) -> None:
        return None


class PendingFetcher:
    async def fetch(self, exchange: str, symbol: str) -> SecondLevelMarketSample:
        observed_at = datetime(2026, 8, 4, 10, 3, tzinfo=UTC)
        if exchange == "gate":
            return SecondLevelMarketSample(
                observed_at=observed_at,
                exchange=exchange,
                symbol=symbol,
                status="ok",
                future_bid=34.5,
                future_ask=34.6,
                future_bid_size=20,
                future_ask_size=20,
                latency_ms=120,
            )
        return SecondLevelMarketSample(
            observed_at=observed_at,
            exchange=exchange,
            symbol=symbol,
                status="ok",
                future_bid=38.6,
                future_ask=38.7,
                future_bid_size=15,
                future_ask_size=15,
            latency_ms=110,
        )

    async def aclose(self) -> None:
        return None


def test_new_listing_watch_item_normalizes_parameters() -> None:
    item = NewListingWatchItem(
        symbol="unitree",
        exchanges=["Bybit", "gate", "BYBIT"],
        normal_threshold_pct=3,
        strong_threshold_pct=8,
        extreme_threshold_pct=15,
    )

    assert item.symbol == "UNITREEUSDT"
    assert item.exchanges == ["bybit", "gate"]


@pytest.mark.asyncio
async def test_new_listing_monitor_appends_astro_result_for_futures_alerts() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NewListingMonitorRepository(db)
    astro_calls: list[str] = []

    async def create_card(opportunity) -> AstroAlertActionResult:
        astro_calls.append(opportunity.symbol)
        assert opportunity.type.value == "FF"
        assert opportunity.risk_labels[0] == "NEW_LISTING"
        return AstroAlertActionResult(
            enabled=True,
            status="created",
            action="add",
            message="已创建开启卡片 UNITREE FF gate->bybit，禁开=false",
            pair_name="UNITREE",
            pair_type="FF",
        )

    item = NewListingWatchItem(
        symbol="UNITREE",
        exchanges=["bybit", "gate"],
        normal_threshold_pct=3,
        strong_threshold_pct=8,
        extreme_threshold_pct=15,
        min_executable_notional_usdt=100,
        normal_consecutive_hits=2,
        strong_consecutive_hits=1,
        extreme_consecutive_hits=1,
    )
    await repo.upsert_watch_item(item)
    monitor = NewListingMonitor(
        repo,
        fetcher=PendingFetcher(),  # type: ignore[arg-type]
        astro_alert_handler=create_card,
    )

    try:
        await monitor.collect_watch_item(item)
        events = await repo.list_events(watch_id=item.id)
    finally:
        await monitor.aclose()
        await db.close()

    assert astro_calls == ["UNITREEUSDT"]
    assert len(events) == 1
    assert "Astro: 已创建开启卡片 UNITREE FF gate->bybit，禁开=false" in events[0].message


@pytest.mark.asyncio
async def test_new_listing_monitor_skips_watch_item_before_start_at(monkeypatch) -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NewListingMonitorRepository(db)
    item = NewListingWatchItem(
        symbol="UNITREE",
        exchanges=["bybit", "gate"],
        start_at=datetime(2026, 8, 18, 10, 5, tzinfo=UTC),
    )
    await repo.upsert_watch_item(item)
    monitor = NewListingMonitor(repo, fetcher=PendingFetcher())  # type: ignore[arg-type]
    monkeypatch.setattr(
        "app.services.new_listing_monitor.utc_now",
        lambda: datetime(2026, 8, 18, 10, 0, tzinfo=UTC),
    )

    try:
        samples = await monitor.collect_due()
    finally:
        await monitor.aclose()
        await db.close()

    assert samples == []


@pytest.mark.asyncio
async def test_new_listing_monitor_skips_watch_item_after_stop_at(monkeypatch) -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NewListingMonitorRepository(db)
    item = NewListingWatchItem(
        symbol="UNITREE",
        exchanges=["bybit", "gate"],
        start_at=datetime(2026, 8, 18, 9, 55, tzinfo=UTC),
        stop_at=datetime(2026, 8, 18, 10, 5, tzinfo=UTC),
    )
    await repo.upsert_watch_item(item)
    monitor = NewListingMonitor(repo, fetcher=PendingFetcher())  # type: ignore[arg-type]
    monkeypatch.setattr(
        "app.services.new_listing_monitor.utc_now",
        lambda: datetime(2026, 8, 18, 10, 5, tzinfo=UTC),
    )

    try:
        samples = await monitor.collect_due()
        status = await monitor.status()
    finally:
        await monitor.aclose()
        await db.close()

    assert samples == []
    assert status.enabled_watch_count == 1
    assert status.active_watch_count == 0


@pytest.mark.asyncio
async def test_announcement_monitor_prewarms_new_listing_watchlist() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    announcement_repo = AnnouncementRepository(db)
    watch_repo = NewListingMonitorRepository(db)
    prewarmer = NewListingPrewarmer(
        watch_repo,
        now_fn=lambda: datetime(2026, 8, 18, 10, 0, tzinfo=UTC),
    )
    monitor = AnnouncementMonitor(
        announcement_repo,
        new_listing_prewarmer=prewarmer.prewarm_from_announcement,
    )
    announcement = ExchangeAnnouncement(
        exchange="okx",
        announcement_id="cxmt-listing",
        kind=AnnouncementKind.LISTING,
        title="OKX to list perpetual futures for CXMT equity",
        url="https://www.okx.com/help/cxmt",
        source="okx-help",
        category="announcements-new-listings",
        symbols=["CXMT"],
        market_type="futures",
        event_time=datetime(2026, 8, 18, 10, 5, tzinfo=UTC),
        summary="listing: symbols=CXMT; market=futures; event_time=2026-08-18T10:05:00+00:00",
        published_at=datetime(2026, 8, 18, 9, 55, tzinfo=UTC),
        fetched_at=datetime(2026, 8, 18, 9, 55, tzinfo=UTC),
        alert_status="pending",
    )

    try:
        created = await monitor.process(
            [announcement],
            AnnouncementSettings(record_exchanges=["okx"]),
        )
        watch_items = await watch_repo.list_watch_items()
    finally:
        await db.close()

    assert len(created) == 1
    assert len(watch_items) == 1
    assert watch_items[0].symbol == "CXMTUSDT"
    assert watch_items[0].market_type.value == "future"
    assert watch_items[0].interval_seconds == 1
    assert watch_items[0].normal_consecutive_hits == 1
    assert watch_items[0].exchanges[0] == "okx"
    assert watch_items[0].start_at == datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    assert watch_items[0].stop_at == datetime(2026, 8, 18, 12, 5, tzinfo=UTC)


@pytest.mark.asyncio
async def test_new_listing_prewarm_never_delays_an_existing_auto_watch() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NewListingMonitorRepository(db)
    now = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    prewarmer = NewListingPrewarmer(repo, now_fn=lambda: now)
    original = ExchangeAnnouncement(
        exchange="okx",
        announcement_id="cxmt-1",
        kind=AnnouncementKind.LISTING,
        title="OKX to list perpetual futures for CXMT equity",
        url="https://www.okx.com/help/cxmt-1",
        source="okx-help",
        symbols=["CXMT"],
        market_type="futures",
        event_time=datetime(2026, 8, 18, 10, 5, tzinfo=UTC),
        published_at=now,
        fetched_at=now,
    )
    corrected = original.model_copy(
        update={
            "event_time": datetime(2026, 8, 18, 10, 20, tzinfo=UTC),
            "url": "https://www.okx.com/help/cxmt-2",
        }
    )

    try:
        await prewarmer.prewarm_from_announcement(original)
        await prewarmer.prewarm_from_announcement(corrected)
        watch_items = await repo.list_watch_items()
    finally:
        await db.close()

    assert len(watch_items) == 1
    assert watch_items[0].start_at == datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    assert watch_items[0].stop_at == datetime(2026, 8, 18, 12, 20, tzinfo=UTC)


@pytest.mark.asyncio
async def test_new_listing_prewarm_backfills_stop_at_for_existing_auto_watch() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NewListingMonitorRepository(db)
    prewarmer = NewListingPrewarmer(repo)
    item = NewListingWatchItem(
        id="new-listing-prewarm-legacy",
        symbol="CXMT",
        exchanges=["okx", "gate"],
        start_at=datetime(2026, 8, 18, 10, 0, tzinfo=UTC),
    )
    await repo.upsert_watch_item(item)

    try:
        saved = await prewarmer.backfill_auto_watch_windows()
        watch_items = await repo.list_watch_items()
    finally:
        await db.close()

    assert len(saved) == 1
    assert watch_items[0].stop_at == datetime(2026, 8, 18, 12, 5, tzinfo=UTC)


@pytest.mark.asyncio
async def test_new_listing_monitor_records_and_alerts_extreme_spread() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NewListingMonitorRepository(db)
    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    item = NewListingWatchItem(
        symbol="UNITREE",
        exchanges=["bybit", "gate"],
        normal_threshold_pct=3,
        strong_threshold_pct=8,
        extreme_threshold_pct=15,
        min_executable_notional_usdt=100,
        normal_consecutive_hits=2,
        strong_consecutive_hits=1,
        extreme_consecutive_hits=1,
    )
    await repo.upsert_watch_item(item)
    monitor = NewListingMonitor(repo, fetcher=UnitreeFetcher(), alert_sender=send_message)  # type: ignore[arg-type]

    try:
        samples = await monitor.collect_watch_item(item)
        stored_samples = await repo.list_samples(watch_id=item.id)
        events = await repo.list_events(watch_id=item.id)
    finally:
        await monitor.aclose()
        await db.close()

    assert len(samples) == 1
    sample = samples[0]
    assert sample.buy_exchange == "gate"
    assert sample.sell_exchange == "bybit"
    assert sample.raw_spread_pct == pytest.approx((68.6 - 34.6) / 34.6 * 100)
    assert sample.alert_level == "extreme"
    assert sample.alert_triggered is True
    assert sample.executable_notional_usdt == pytest.approx(692)
    assert len(stored_samples) == 1
    assert stored_samples[0].risk_labels
    assert len(events) == 1
    assert events[0].level == "extreme"
    assert "UNITREEUSDT" in events[0].message
    assert len(sent_messages) == 1


@pytest.mark.asyncio
async def test_new_listing_history_warns_when_no_second_level_records() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NewListingMonitorRepository(db)
    monitor = NewListingMonitor(repo, fetcher=UnitreeFetcher())  # type: ignore[arg-type]
    now = datetime(2026, 8, 4, 10, 3, tzinfo=UTC)

    try:
        result = await monitor.history(
            watch_id=None,
            symbol="UNITREEUSDT",
            start_at=now - timedelta(hours=1),
            end_at=now,
            limit=1000,
        )
    finally:
        await monitor.aclose()
        await db.close()

    assert result.sample_count == 0
    assert result.warnings == ["该时间段没有新币极速秒级记录，无法证明当时实时盘口是否可成交。"]


def test_new_listing_monitor_api_saves_watch_item() -> None:
    app = create_app(
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
        )
    )
    headers = {"X-Dashboard-Password": "secret"}

    with TestClient(app) as client:
        response = client.post(
            "/api/new-listing-monitor/watchlist",
            headers=headers,
            json={
                "id": "unitree-watch",
                "enabled": False,
                "symbol": "unitree",
                "market_type": "future",
                "exchanges": ["Bybit", "Gate"],
                "interval_seconds": 1,
                "retention_hours": 72,
                "normal_threshold_pct": 3,
                "strong_threshold_pct": 8,
                "extreme_threshold_pct": 15,
                "min_executable_notional_usdt": 100,
                "depth_validation_notional_usdt": 300,
                "allow_low_liquidity_alert": True,
                "normal_consecutive_hits": 2,
                "strong_consecutive_hits": 1,
                "extreme_consecutive_hits": 1,
                "cooldown_seconds": 60,
                "buy_fee_pct": 0.05,
                "sell_fee_pct": 0.05,
                "slippage_buffer_pct": 0.1,
                "note": "测试新币",
                "created_at": "2026-08-04T10:00:00Z",
                "updated_at": "2026-08-04T10:00:00Z",
            },
        )
        status_response = client.get("/api/new-listing-monitor/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "UNITREEUSDT"
    assert payload["exchanges"] == ["bybit", "gate"]
    assert status_response.status_code == 200
    assert status_response.json()["watch_count"] == 1
