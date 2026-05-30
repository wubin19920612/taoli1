from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import connect_database
from app.db.repositories import PhonePriceAlertEventRepository, PhonePriceAlertRuleRepository
from app.db.schema import initialize_schema
from app.models.market import MarketSnapshot, MarketType
from app.models.phone_alert import (
    PhonePriceAlertCondition,
    PhonePriceAlertEvent,
    PhonePriceAlertPriceField,
    PhonePriceAlertRule,
)
from app.services.phone_price_alerts import PhonePriceAlertEngine


def market(
    *,
    symbol: str = "BTCUSDT",
    exchange: str = "binance",
    market_type: MarketType = MarketType.FUTURE,
    bid: float = 99,
    ask: float = 101,
    mark_price: float | None = 100,
    index_price: float | None = 100,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        base=symbol.removesuffix("USDT"),
        quote="USDT",
        exchange=exchange,
        market_type=market_type,
        bid=bid,
        ask=ask,
        mark_price=mark_price,
        index_price=index_price,
        timestamp=datetime(2026, 5, 27, 10, 0, tzinfo=UTC),
        raw_symbol=symbol,
    )


@pytest.mark.asyncio
async def test_phone_price_alert_rule_and_event_repository_roundtrip() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        rule_repo = PhonePriceAlertRuleRepository(db)
        event_repo = PhonePriceAlertEventRepository(db)
        rule = PhonePriceAlertRule(
            name="BTC contract breakout",
            symbol="btc-usdt",
            exchange="BINANCE",
            market_type=MarketType.FUTURE,
            price_field=PhonePriceAlertPriceField.MARK_PRICE,
            condition=PhonePriceAlertCondition.ABOVE,
            target_price=110_000,
            cooldown_seconds=600,
        )

        created = await rule_repo.create(rule)
        loaded = await rule_repo.get(created.id)
        updated = await rule_repo.upsert(created.model_copy(update={"target_price": 111_000}))
        listed = await rule_repo.list()
        event = await event_repo.create(
            PhonePriceAlertEvent(
                rule_id=created.id,
                symbol="BTCUSDT",
                exchange="binance",
                market_type=MarketType.FUTURE,
                price_field=PhonePriceAlertPriceField.MARK_PRICE,
                condition=PhonePriceAlertCondition.ABOVE,
                target_price=111_000,
                observed_price=111_100,
                status="sent",
                message="BTCUSDT reached 111100",
                created_at=datetime(2026, 5, 27, 10, 1, tzinfo=UTC),
            )
        )
        events = await event_repo.list(limit=5)

        assert loaded is not None
        assert loaded.symbol == "BTCUSDT"
        assert loaded.exchange == "binance"
        assert updated.target_price == 111_000
        assert [item.id for item in listed] == [created.id]
        assert events[0].id == event.id
        assert events[0].observed_price == 111_100
    finally:
        await db.close()


def test_phone_price_alert_engine_triggers_above_threshold_with_mark_price() -> None:
    engine = PhonePriceAlertEngine()
    rule = PhonePriceAlertRule(
        name="BTC up",
        symbol="BTCUSDT",
        exchange="binance",
        condition=PhonePriceAlertCondition.ABOVE,
        target_price=100,
        cooldown_seconds=300,
    )

    matches = engine.evaluate([market(mark_price=100.5)], [rule], now=datetime(2026, 5, 27, 10, 0, tzinfo=UTC))

    assert len(matches) == 1
    assert matches[0].rule.id == rule.id
    assert matches[0].observed_price == 100.5
    assert matches[0].market.exchange == "binance"


def test_phone_price_alert_engine_triggers_below_threshold_with_mid_price_fallback() -> None:
    engine = PhonePriceAlertEngine()
    rule = PhonePriceAlertRule(
        name="BTC down",
        symbol="BTCUSDT",
        price_field=PhonePriceAlertPriceField.MARK_PRICE,
        condition=PhonePriceAlertCondition.BELOW,
        target_price=100,
        cooldown_seconds=300,
    )

    matches = engine.evaluate(
        [market(bid=98, ask=100, mark_price=None)],
        [rule],
        now=datetime(2026, 5, 27, 10, 0, tzinfo=UTC),
    )

    assert len(matches) == 1
    assert matches[0].observed_price == 99
    assert matches[0].resolved_price_field == PhonePriceAlertPriceField.MID_PRICE


def test_phone_price_alert_engine_suppresses_repeats_until_cooldown_expires() -> None:
    engine = PhonePriceAlertEngine()
    rule = PhonePriceAlertRule(
        name="BTC up",
        symbol="BTCUSDT",
        condition=PhonePriceAlertCondition.ABOVE,
        target_price=100,
        cooldown_seconds=300,
    )
    now = datetime(2026, 5, 27, 10, 0, tzinfo=UTC)

    first = engine.evaluate([market(mark_price=101)], [rule], now=now)
    second = engine.evaluate([market(mark_price=102)], [rule], now=now + timedelta(seconds=60))
    third = engine.evaluate([market(mark_price=103)], [rule], now=now + timedelta(seconds=301))

    assert len(first) == 1
    assert second == []
    assert len(third) == 1
