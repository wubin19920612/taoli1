from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.new_listing import NewListingWatchItem
from app.models.second_level_sampling import SecondLevelMarketSample
from app.services.new_listing_monitor import NewListingMonitor, NewListingMonitorRepository


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
