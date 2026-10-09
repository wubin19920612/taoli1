import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI

from app.main import _run_opportunity_radar_alert_loop
from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity_radar import OpportunityRadarSettings
from app.models.settings import RiskSettings
from app.services.opportunity_radar import OpportunityRadarAlertEngine
from app.services.snapshot_store import SnapshotStore


class FakeSettingsRepository:
    async def get_opportunity_radar_settings(self) -> OpportunityRadarSettings:
        return OpportunityRadarSettings(
            feishu_notifications_enabled=True,
            min_depth_multiple=1,
            min_alert_score=0,
            alert_consecutive_hits=1,
            alert_cooldown_seconds=0,
        )

    async def get_risk_settings(self) -> RiskSettings:
        return RiskSettings()


class FakeNotifier:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.config = SimpleNamespace(webhook_url="https://example.test/hook")
        self.messages: list[str] = []
        self.stop_event = stop_event

    async def send_text(self, message: str) -> None:
        self.messages.append(message)
        self.stop_event.set()


def future_market(
    exchange: str,
    *,
    bid: float,
    ask: float,
    mark: float,
    funding: float,
    interval: int,
    now: datetime,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        exchange=exchange,
        market_type=MarketType.FUTURE,
        bid=bid,
        ask=ask,
        bid_size=200,
        ask_size=200,
        volume_24h_usdt=5_000_000,
        funding_next_rate_pct=funding,
        funding_interval_hours=interval,
        mark_price=mark,
        index_price=100,
        timestamp=now,
        raw_symbol="BTCUSDT",
    )


async def test_radar_alert_loop_sends_confirmed_candidate_to_feishu() -> None:
    now = datetime.now(UTC)
    stop_event = asyncio.Event()
    store = SnapshotStore()
    store.set_markets(
        [
            future_market(
                "bybit",
                bid=99.95,
                ask=100.05,
                mark=98,
                funding=-0.10,
                interval=1,
                now=now,
            ),
            future_market(
                "binance",
                bid=100.25,
                ask=100.35,
                mark=100,
                funding=0.04,
                interval=4,
                now=now,
            ),
        ]
    )
    notifier = FakeNotifier(stop_event)
    app = FastAPI()
    app.state.settings_repo = FakeSettingsRepository()
    app.state.snapshot_store = store
    app.state.feishu_notifier = notifier
    app.state.opportunity_radar_alert_engine = OpportunityRadarAlertEngine()

    await asyncio.wait_for(
        _run_opportunity_radar_alert_loop(app, 60, stop_event),
        timeout=1,
    )

    assert len(notifier.messages) == 1
    assert "[机会雷达]" in notifier.messages[0]
    assert "多 bybit / 空 binance" in notifier.messages[0]
