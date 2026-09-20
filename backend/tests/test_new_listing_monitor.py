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
from app.models.new_listing import (
    NewListingAlertEvent,
    NewListingSpreadSample,
    NewListingWatchItem,
)
from app.models.second_level_sampling import SecondLevelMarketSample
from app.services.announcements import AnnouncementMonitor
from app.services.new_listing_monitor import (
    NewListingAnnouncementCardPreparer,
    NewListingMonitor,
    NewListingMonitorRepository,
    NewListingPrewarmer,
    _direction_sample,
    _opportunity_from_new_listing_sample,
)


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


def test_new_listing_sample_keeps_hyperliquid_market_for_card() -> None:
    now = datetime.now(UTC)
    item = NewListingWatchItem(symbol="TTWO", exchanges=["binance", "hyperliquid"])
    buy = SecondLevelMarketSample(
        observed_at=now, exchange="binance", symbol="TTWOUSDT", status="ok",
        raw_future_symbol="TTWOUSDT", future_bid=99, future_ask=100,
    )
    sell = SecondLevelMarketSample(
        observed_at=now, exchange="hyperliquid", symbol="TTWOUSDT", status="ok",
        raw_future_symbol="para:TTWO", future_bid=102, future_ask=103,
    )

    sample = _direction_sample(item, buy, sell, observed_at=now)

    assert sample is not None
    assert sample.sell_raw_symbol == "para:TTWO"
    assert _opportunity_from_new_listing_sample(sample).sell_raw_symbol == "para:TTWO"


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
    prepared_items: list[tuple[str, str]] = []

    async def prepare_cards(
        announcement: ExchangeAnnouncement,
        item: NewListingWatchItem,
    ) -> list[AstroAlertActionResult]:
        prepared_items.append((announcement.announcement_id, item.symbol))
        return []

    prewarmer = NewListingPrewarmer(
        watch_repo,
        card_preparer=prepare_cards,
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
    assert watch_items[0].cooldown_seconds == 60
    assert watch_items[0].exchanges[0] == "okx"
    assert watch_items[0].start_at == datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    assert watch_items[0].stop_at == datetime(2026, 8, 18, 12, 5, tzinfo=UTC)
    assert prepared_items == [("cxmt-listing", "CXMTUSDT")]


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
        cooldown_seconds=5,
        start_at=datetime(2026, 8, 18, 10, 0, tzinfo=UTC),
    )
    manual_item = NewListingWatchItem(
        id="manual-fast-watch",
        symbol="UNITREE",
        exchanges=["bybit", "gate"],
        cooldown_seconds=5,
    )
    await repo.upsert_watch_item(item)
    await repo.upsert_watch_item(manual_item)

    try:
        saved = await prewarmer.backfill_auto_watch_windows()
        watch_items = await repo.list_watch_items()
    finally:
        await db.close()

    assert len(saved) == 1
    saved_by_id = {saved_item.id: saved_item for saved_item in watch_items}
    assert saved_by_id[item.id].stop_at == datetime(2026, 8, 18, 12, 5, tzinfo=UTC)
    assert saved_by_id[item.id].cooldown_seconds == 60
    assert saved_by_id[manual_item.id].cooldown_seconds == 5


def test_new_listing_monitor_keeps_level_fluctuations_in_the_same_cooldown() -> None:
    monitor = NewListingMonitor(object(), fetcher=PendingFetcher())  # type: ignore[arg-type]
    item = NewListingWatchItem(
        id="mcat-auto-watch",
        symbol="MCAT",
        market_type="spot",
        exchanges=["gate", "bitget"],
        normal_threshold_pct=1,
        strong_threshold_pct=3,
        extreme_threshold_pct=8,
        normal_consecutive_hits=1,
        strong_consecutive_hits=1,
        cooldown_seconds=60,
    )
    first_at = datetime(2026, 9, 10, 12, 9, 27, tzinfo=UTC)

    def sample(net_spread_pct: float, observed_at: datetime) -> NewListingSpreadSample:
        return NewListingSpreadSample(
            watch_id=item.id,
            observed_at=observed_at,
            symbol=item.symbol,
            market_type=item.market_type,
            buy_exchange="gate",
            sell_exchange="bitget",
            buy_price=0.3975,
            sell_price=0.411,
            raw_spread_pct=net_spread_pct + 0.2,
            net_spread_pct=net_spread_pct,
        )

    strong = sample(3.196, first_at)
    monitor._classify_sample(item, strong)
    normal_during_cooldown = sample(2.945, first_at + timedelta(seconds=10))
    monitor._classify_sample(item, normal_during_cooldown)
    normal_after_cooldown = sample(2.945, first_at + timedelta(seconds=60))
    monitor._classify_sample(item, normal_after_cooldown)

    assert strong.alert_level == "strong"
    assert strong.alert_triggered is True
    assert normal_during_cooldown.alert_level == "normal"
    assert normal_during_cooldown.alert_triggered is False
    assert normal_during_cooldown.no_alert_reason == "冷却中，约 50 秒后可再次提醒"
    assert normal_after_cooldown.alert_triggered is True


@pytest.mark.asyncio
async def test_new_listing_monitor_restores_route_cooldown_after_restart(monkeypatch) -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NewListingMonitorRepository(db)
    item = NewListingWatchItem(
        id="unitree-restart-watch",
        symbol="UNITREE",
        exchanges=["gate", "bybit"],
        normal_threshold_pct=3,
        strong_threshold_pct=8,
        extreme_threshold_pct=15,
        normal_consecutive_hits=1,
        strong_consecutive_hits=1,
        extreme_consecutive_hits=1,
        cooldown_seconds=60,
    )
    await repo.upsert_watch_item(item)
    current_time = [datetime(2026, 9, 12, 10, 35, tzinfo=UTC)]
    monkeypatch.setattr(
        "app.services.new_listing_monitor.utc_now",
        lambda: current_time[0],
    )

    first_monitor = NewListingMonitor(repo, fetcher=UnitreeFetcher())  # type: ignore[arg-type]
    try:
        first_samples = await first_monitor.collect_watch_item(item)
    finally:
        await first_monitor.aclose()

    current_time[0] += timedelta(seconds=10)
    restarted_monitor = NewListingMonitor(repo, fetcher=UnitreeFetcher())  # type: ignore[arg-type]
    try:
        second_samples = await restarted_monitor.collect_watch_item(item)
        events = await repo.list_events(watch_id=item.id)
    finally:
        await restarted_monitor.aclose()
        await db.close()

    assert first_samples[0].alert_triggered is True
    assert second_samples[0].alert_triggered is False
    assert second_samples[0].no_alert_reason == "冷却中，约 50 秒后可再次提醒"
    assert len(events) == 1


@pytest.mark.asyncio
async def test_schema_backfills_route_cooldown_from_existing_alert_events() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NewListingMonitorRepository(db)
    item = await repo.upsert_watch_item(
        NewListingWatchItem(
            id="legacy-flock-watch",
            symbol="FLOCK",
            exchanges=["binance", "gate"],
        )
    )
    await db.execute("DROP TABLE new_listing_alert_cooldowns")
    sent_at = datetime(2026, 9, 12, 10, 35, 5, tzinfo=UTC)
    await repo.create_event(
        NewListingAlertEvent(
            watch_id=item.id,
            symbol=item.symbol,
            market_type=item.market_type,
            level="normal",
            buy_exchange="binance",
            sell_exchange="gate",
            net_spread_pct=1.049,
            raw_spread_pct=1.249,
            executable_notional_usdt=316.21,
            message="legacy event",
            created_at=sent_at,
        )
    )

    try:
        await initialize_schema(db)
        cooldowns = await repo.list_alert_cooldowns()
    finally:
        await db.close()

    assert cooldowns == {
        "legacy-flock-watch:future:binance->gate": sent_at,
    }


@pytest.mark.asyncio
async def test_listing_announcement_prepares_bidirectional_cards_for_source_exchange() -> None:
    prepared_opportunities = []

    async def prepare_card(opportunity) -> AstroAlertActionResult:
        prepared_opportunities.append(opportunity)
        return AstroAlertActionResult(
            enabled=True,
            status="created",
            action="add",
            message="created",
        )

    preparer = NewListingAnnouncementCardPreparer(prepare_card)
    announcement = ExchangeAnnouncement(
        exchange="gate",
        announcement_id="flock-futures-listing",
        kind=AnnouncementKind.LISTING,
        title="Gate to list FLOCKUSDT perpetual futures",
        url="https://www.gate.com/announcements/flock",
        source="gate-announcements",
        symbols=["FLOCK"],
        market_type="futures",
        event_time=datetime(2026, 9, 12, 10, 40, tzinfo=UTC),
        published_at=datetime(2026, 9, 12, 10, 30, tzinfo=UTC),
        fetched_at=datetime(2026, 9, 12, 10, 30, tzinfo=UTC),
        alert_status="pending",
    )
    item = NewListingWatchItem(
        id="flock-watch",
        symbol="FLOCK",
        market_type="future",
        exchanges=["gate", "bitget", "okx"],
        normal_threshold_pct=1,
        buy_fee_pct=0.05,
        sell_fee_pct=0.05,
        slippage_buffer_pct=0.1,
    )

    first_results = await preparer.prepare_from_announcement(announcement, item)
    second_results = await preparer.prepare_from_announcement(announcement, item)

    assert len(first_results) == 4
    assert second_results == []
    assert [
        (opportunity.buy_exchange, opportunity.sell_exchange)
        for opportunity in prepared_opportunities
    ] == [
        ("gate", "bitget"),
        ("bitget", "gate"),
        ("gate", "okx"),
        ("okx", "gate"),
    ]
    assert all(opportunity.type.value == "FF" for opportunity in prepared_opportunities)
    assert all(opportunity.risk_labels == ["NEW_LISTING"] for opportunity in prepared_opportunities)
    assert all(
        opportunity.open_spread_pct == pytest.approx(1.2)
        for opportunity in prepared_opportunities
    )


@pytest.mark.asyncio
async def test_far_future_listing_announcement_prepares_cards_without_saving_watch() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = NewListingMonitorRepository(db)
    prepared_items: list[NewListingWatchItem] = []
    now = datetime(2026, 9, 12, 10, 30, tzinfo=UTC)

    async def prepare_cards(
        announcement: ExchangeAnnouncement,
        item: NewListingWatchItem,
    ) -> list[AstroAlertActionResult]:
        prepared_items.append(item)
        return []

    prewarmer = NewListingPrewarmer(
        repo,
        card_preparer=prepare_cards,
        now_fn=lambda: now,
    )
    announcement = ExchangeAnnouncement(
        exchange="okx",
        announcement_id="future-listing",
        kind=AnnouncementKind.LISTING,
        title="OKX to list FUTUREUSDT perpetual futures",
        url="https://www.okx.com/help/future-listing",
        source="okx-help",
        symbols=["FUTURE"],
        market_type="futures",
        event_time=now + timedelta(days=7),
        published_at=now,
        fetched_at=now,
        alert_status="pending",
    )

    try:
        saved = await prewarmer.prewarm_from_announcement(announcement)
        watch_items = await repo.list_watch_items()
    finally:
        await db.close()

    assert saved == []
    assert watch_items == []
    assert len(prepared_items) == 1
    assert prepared_items[0].symbol == "FUTUREUSDT"
    assert prepared_items[0].exchanges[0] == "okx"


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
