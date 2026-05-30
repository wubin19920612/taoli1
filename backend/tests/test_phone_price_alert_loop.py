import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI

from app.main import _run_phone_price_alert_loop
from app.models.market import MarketSnapshot, MarketType
from app.models.phone_alert import PhonePriceAlertCondition, PhonePriceAlertEvent, PhonePriceAlertRule
from app.services.snapshot_store import SnapshotStore


def market() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        quote="USDT",
        exchange="binance",
        market_type=MarketType.FUTURE,
        bid=109_900,
        ask=110_100,
        mark_price=110_050,
        index_price=110_025,
        timestamp=datetime(2026, 5, 27, 10, 0, tzinfo=UTC),
        raw_symbol="BTCUSDT",
    )


class FakePhoneRuleRepo:
    def __init__(self, rules: list[PhonePriceAlertRule]):
        self.rules = rules

    async def list(self) -> list[PhonePriceAlertRule]:
        return self.rules


class FakePhoneEventRepo:
    def __init__(self, stop_event: asyncio.Event):
        self.stop_event = stop_event
        self.events: list[PhonePriceAlertEvent] = []

    async def create(self, event: PhonePriceAlertEvent) -> PhonePriceAlertEvent:
        self.events.append(event)
        self.stop_event.set()
        return event


class FakeFeishuPhoneNotifier:
    def __init__(self):
        self.texts: list[str] = []

    async def send_phone_urgent_text(self, text: str) -> None:
        self.texts.append(text)


@pytest.mark.asyncio
async def test_phone_price_alert_loop_sends_phone_urgent_and_records_event() -> None:
    stop_event = asyncio.Event()
    app = FastAPI()
    rule = PhonePriceAlertRule(
        id="phone-rule-1",
        name="BTC breakout",
        symbol="BTCUSDT",
        exchange="binance",
        market_type=MarketType.FUTURE,
        condition=PhonePriceAlertCondition.ABOVE,
        target_price=110_000,
        cooldown_seconds=300,
    )
    store = SnapshotStore()
    store.set_markets([market()])
    event_repo = FakePhoneEventRepo(stop_event)
    notifier = FakeFeishuPhoneNotifier()

    app.state.snapshot_store = store
    app.state.phone_price_alert_rule_repo = FakePhoneRuleRepo([rule])
    app.state.phone_price_alert_event_repo = event_repo
    app.state.feishu_notifier = notifier

    await asyncio.wait_for(_run_phone_price_alert_loop(app, 60, stop_event), timeout=2)

    assert len(notifier.texts) == 1
    assert "BTC breakout" in notifier.texts[0]
    assert "BTCUSDT" in notifier.texts[0]
    assert "110050" in notifier.texts[0]
    assert len(event_repo.events) == 1
    assert event_repo.events[0].rule_id == "phone-rule-1"
    assert event_repo.events[0].status == "sent"
    assert event_repo.events[0].observed_price == 110_050
