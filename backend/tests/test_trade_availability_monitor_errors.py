import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.models.market import MarketType
from app.models.trade_availability import (
    MarketTradeAvailability,
    SpotTransferAvailability,
    TradeActionStatus,
    TradeAvailabilityResult,
    TradeAvailabilityState,
    TradeAvailabilityWatch,
    TransferAvailabilityState,
)
from app.services import trade_availability as module

LOGGER_NAME = module.__name__
START = datetime(2026, 10, 27, 2, tzinfo=UTC)
MISSING_MARKET = "RuntimeError: 聚合行情中未找到指定原始市场"
DIAGNOSTIC = "RuntimeError: Hyperliquid 公开接口未返回指定 DEX 原始市场"


class MemoryRepository:
    """Persist each upsert so the next poll reads the previous error."""

    def __init__(self, *watches: TradeAvailabilityWatch) -> None:
        self.watches = {watch.id: watch for watch in watches}
        self.saved: list[TradeAvailabilityWatch] = []

    async def list(self) -> list[TradeAvailabilityWatch]:
        return list(self.watches.values())

    async def upsert(self, watch: TradeAvailabilityWatch) -> TradeAvailabilityWatch:
        self.saved.append(watch)
        self.watches[watch.id] = watch
        return watch


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        current = START

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(module, "datetime", Clock)
    return Clock


def make_watch(watch_id: str = "watch-a") -> TradeAvailabilityWatch:
    return TradeAvailabilityWatch(
        id=watch_id,
        symbol="BTCUSDT",
        exchange="okx",
        market_type=MarketType.FUTURE,
        raw_symbol="BTC-USDT-SWAP",
        last_buy_state=TradeAvailabilityState.BLOCKED,
        last_sell_state=TradeAvailabilityState.UNKNOWN,
        last_buy_reduce_only_state=TradeAvailabilityState.UNKNOWN,
        last_sell_reduce_only_state=TradeAvailabilityState.BLOCKED,
        created_at=START - timedelta(days=1),
        updated_at=START - timedelta(days=1),
    )


def make_market() -> MarketTradeAvailability:
    action = TradeActionStatus(
        state=TradeAvailabilityState.AVAILABLE,
        reason_code="AVAILABLE",
        reason="offline fixture",
    )
    return MarketTradeAvailability(
        exchange="okx",
        market_type=MarketType.FUTURE,
        symbol="BTCUSDT",
        raw_symbol="BTC-USDT-SWAP",
        coverage_tier="core",
        observed_at=START,
        market_data_updated_at=START,
        orderbook_source="offline fixture",
        public_status_code="live",
        public_status_source="offline fixture",
        buy_open=action,
        sell_open=action,
        buy_reduce_only=action,
        sell_reduce_only=action,
    )


def monitor_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == LOGGER_NAME]


@pytest.mark.parametrize("failure", ["missing_market", "fetch_exception"])
async def test_same_error_logs_one_traceback_and_persists_every_poll(clock, caplog, failure):
    watch = make_watch()
    repository = MemoryRepository(watch)
    service = AsyncMock()
    error = MISSING_MARKET
    if failure == "missing_market":
        service.fetch_status.return_value = TradeAvailabilityResult(
            query="BTCUSDT", observed_at=START,
        )
    else:
        service.fetch_status.side_effect = ValueError("offline upstream failure")
        error = "ValueError: offline upstream failure"
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    for index in range(3):
        caplog.clear()
        clock.current = START + timedelta(seconds=20 * index)
        # Recreating the monitor also proves deduplication uses persisted state.
        monitor = module.TradeAvailabilityMonitor(repository, service)
        assert await monitor.check_once() == []
        assert len(repository.saved) == index + 1
        assert repository.saved[-1] == watch.model_copy(update={
            "last_checked_at": clock.current,
            "last_error": error,
            "updated_at": clock.current,
        })
        records = monitor_records(caplog)
        assert len(records) == 1
        assert f"id={watch.id}" in records[0].getMessage()
        if index == 0:
            assert records[0].levelno == logging.ERROR
            assert records[0].exc_info is not None
            expected_type = RuntimeError if failure == "missing_market" else ValueError
            assert records[0].exc_info[0] is expected_type
        else:
            assert records[0].levelno == logging.DEBUG
            assert records[0].exc_info is None
            assert records[0].exc_text is None


async def test_changed_error_logs_another_traceback(clock, caplog):
    repository = MemoryRepository(make_watch())
    service = AsyncMock()
    service.fetch_status.side_effect = [
        RuntimeError("A"), RuntimeError("A"), RuntimeError("B"), RuntimeError("B"), ValueError("B"),
    ]
    monitor = module.TradeAvailabilityMonitor(repository, service)
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    for index in range(5):
        clock.current = START + timedelta(seconds=20 * index)
        assert await monitor.check_once() == []

    records = monitor_records(caplog)
    assert [record.levelno for record in records] == [
        logging.ERROR, logging.DEBUG, logging.ERROR, logging.DEBUG, logging.ERROR,
    ]
    assert [record.exc_info is not None for record in records] == [True, False, True, False, True]
    assert [watch.last_error for watch in repository.saved] == [
        "RuntimeError: A", "RuntimeError: A", "RuntimeError: B", "RuntimeError: B", "ValueError: B",
    ]


async def test_recovery_logs_once_and_preserves_events_notifications_and_transfers(clock, caplog):
    watch = make_watch()
    repository = MemoryRepository(watch)
    service = AsyncMock()
    result = TradeAvailabilityResult(query="BTCUSDT", observed_at=START, markets=[make_market()])
    service.fetch_status.side_effect = [RuntimeError("A"), result, result, RuntimeError("A")]
    transfer = SpotTransferAvailability(
        asset="BTC",
        deposit_state=TransferAvailabilityState.ENABLED,
        withdraw_state=TransferAvailabilityState.DISABLED,
        source="offline fixture",
        observed_at=START,
    )
    service.fetch_transfer_status.return_value = transfer
    sender = AsyncMock()
    monitor = module.TradeAvailabilityMonitor(repository, service, sender)
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    assert await monitor.check_once() == []
    service.fetch_transfer_status.assert_not_awaited()
    sender.assert_not_awaited()

    clock.current = START + timedelta(seconds=20)
    events = await monitor.check_once()
    assert len(events) == 1
    assert events[0].watch_id == watch.id
    assert events[0].recovered_sides == ["buy", "sell"]
    assert events[0].recovered_actions == [
        "buy_open", "sell_open", "buy_reduce_only", "sell_reduce_only",
    ]
    assert events[0].spot_transfer == transfer
    recovered = repository.watches[watch.id]
    assert recovered.last_error is None
    assert recovered.last_checked_at == recovered.updated_at == clock.current
    assert recovered.last_notified_at == clock.current
    assert recovered.last_buy_state == recovered.last_sell_state == TradeAvailabilityState.AVAILABLE
    assert recovered.last_buy_reduce_only_state == TradeAvailabilityState.AVAILABLE
    assert recovered.last_sell_reduce_only_state == TradeAvailabilityState.AVAILABLE
    service.fetch_transfer_status.assert_awaited_once_with(
        watch.symbol, exchange=watch.exchange, raw_symbol=watch.raw_symbol,
    )
    sender.assert_awaited_once_with(module.build_trade_recovery_message(events[0]))

    clock.current += timedelta(seconds=20)
    assert await monitor.check_once() == []
    assert repository.watches[watch.id].last_notified_at == recovered.last_notified_at
    service.fetch_transfer_status.assert_awaited_once()
    sender.assert_awaited_once()
    records = monitor_records(caplog)
    recovery_logs = [record for record in records if record.levelno == logging.INFO]
    assert len(recovery_logs) == 1
    assert "已恢复" in recovery_logs[0].getMessage()
    assert f"id={watch.id}" in recovery_logs[0].getMessage()
    assert recovery_logs[0].exc_info is None

    # The same error after a successful poll starts a new failure episode.
    caplog.clear()
    clock.current += timedelta(seconds=20)
    assert await monitor.check_once() == []
    records = monitor_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None


@pytest.mark.parametrize(("errors", "suffix"), [
    ({}, ""),
    ({"k2": "x", "k1": DIAGNOSTIC}, f"；诊断：{DIAGNOSTIC}"),
    ({"k2": "x", "k1": ""}, "；诊断："),
    ({"k1": "诊" * 199}, "；诊断：" + "诊" * 199),
    ({"k1": "诊" * 200}, "；诊断：" + "诊" * 200),
    ({"k1": "诊" * 200 + "末尾不应保留"}, "；诊断：" + "诊" * 200),
], ids=["empty", "sorted_key", "empty_diagnostic", "199_chars", "200_chars", "truncated"])
async def test_missing_market_preserves_first_sorted_diagnostic(clock, caplog, errors, suffix):
    watch = make_watch()
    repository = MemoryRepository(watch)
    service = AsyncMock()
    service.fetch_status.return_value = TradeAvailabilityResult(
        query=watch.symbol, observed_at=START, errors=errors,
    )
    monitor = module.TradeAvailabilityMonitor(repository, service)
    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)

    assert await monitor.check_once() == []
    assert repository.watches[watch.id].last_error == MISSING_MARKET + suffix
    records = monitor_records(caplog)
    assert len(records) == 1
    assert records[0].exc_info[0] is RuntimeError
    assert str(records[0].exc_info[1]) == "聚合行情中未找到指定原始市场" + suffix
    assert service.fetch_status.return_value.errors == errors


async def test_changed_diagnostic_logs_new_error(clock, caplog):
    repository = MemoryRepository(make_watch())
    service = AsyncMock()
    service.fetch_status.side_effect = [
        TradeAvailabilityResult(query="BTCUSDT", observed_at=START, errors={"k1": reason})
        for reason in ("A", "A", "B", "B")
    ]
    monitor = module.TradeAvailabilityMonitor(repository, service)
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    for _ in range(4):
        assert await monitor.check_once() == []

    records = monitor_records(caplog)
    assert [record.levelno for record in records] == [
        logging.ERROR, logging.DEBUG, logging.ERROR, logging.DEBUG,
    ]
    assert [record.exc_info is not None for record in records] == [True, False, True, False]
    assert [watch.last_error for watch in repository.saved] == [
        MISSING_MARKET + "；诊断：" + reason for reason in ("A", "A", "B", "B")
    ]


async def test_repeated_failure_does_not_suppress_another_watch(clock, caplog):
    first = make_watch()
    second = make_watch("watch-b").model_copy(update={
        "symbol": "ETHUSDT", "raw_symbol": "ETH-USDT-SWAP",
    })
    repository = MemoryRepository(first)
    service = AsyncMock()
    service.fetch_status.side_effect = RuntimeError("same failure")
    monitor = module.TradeAvailabilityMonitor(repository, service)
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    assert await monitor.check_once() == []
    caplog.clear()
    repository.watches[second.id] = second
    clock.current += timedelta(seconds=20)
    assert await monitor.check_once() == []

    records = monitor_records(caplog)
    assert len(records) == 2
    assert records[0].levelno == logging.DEBUG
    assert f"id={first.id}" in records[0].getMessage()
    assert records[0].exc_info is None
    assert records[1].levelno == logging.ERROR
    assert f"id={second.id}" in records[1].getMessage()
    assert records[1].exc_info is not None
    assert len(repository.saved) == 3
    assert repository.watches[first.id].last_error == repository.watches[second.id].last_error
