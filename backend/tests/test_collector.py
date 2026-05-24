import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.exchanges.base import ExchangeAdapter
from app.models.market import MarketSnapshot, MarketType
from app.models.settings import RiskSettings
from app.services.collector import MarketCollector, default_exchange_adapters
from app.services.snapshot_store import SnapshotStore


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class FixedAdapter(ExchangeAdapter):
    name = "fixed"

    def __init__(
        self,
        markets: list[MarketSnapshot] | None = None,
        exc: Exception | None = None,
        name: str = "fixed",
    ):
        self.markets = markets or []
        self.exc = exc
        self.name = name
        self.spot_calls = 0
        self.future_calls = 0

    async def fetch_spot_tickers(self):
        self.spot_calls += 1
        if self.exc:
            raise self.exc
        return self.markets

    async def fetch_future_tickers(self):
        self.future_calls += 1
        if self.exc:
            raise self.exc
        return []


class MutableAdapter(ExchangeAdapter):
    def __init__(self, name: str, symbol: str):
        self.name = name
        self.symbol = symbol
        self.spot_calls = 0
        self.future_calls = 0

    async def fetch_spot_tickers(self):
        self.spot_calls += 1
        return [market_on(self.name, self.symbol)]

    async def fetch_future_tickers(self):
        self.future_calls += 1
        return []


class SwitchablePoolTimeoutAdapter(ExchangeAdapter):
    def __init__(self, name: str, symbol: str):
        self.name = name
        self.symbol = symbol
        self.fail = False
        self.spot_calls = 0
        self.future_calls = 0
        self.reset_calls = 0

    async def fetch_spot_tickers(self):
        self.spot_calls += 1
        if self.fail:
            raise TimeoutError("PoolTimeout")
        return [market_on(self.name, self.symbol)]

    async def fetch_future_tickers(self):
        self.future_calls += 1
        return []

    async def reset_client(self) -> None:
        self.reset_calls += 1


class PartialConnectTimeoutAdapter(ExchangeAdapter):
    def __init__(self, name: str, symbol: str):
        self.name = name
        self.symbol = symbol
        self.fail_spot = False
        self.future_symbol = f"{symbol}F"
        self.spot_calls = 0
        self.future_calls = 0

    async def fetch_spot_tickers(self):
        self.spot_calls += 1
        if self.fail_spot:
            raise TimeoutError("ConnectTimeout")
        return [market_on(self.name, self.symbol)]

    async def fetch_future_tickers(self):
        self.future_calls += 1
        return [market_on(self.name, self.future_symbol).model_copy(update={"market_type": MarketType.FUTURE})]


class BlockingAdapter(ExchangeAdapter):
    name = "slow"

    def __init__(self):
        self.spot_calls = 0
        self.future_calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch_spot_tickers(self):
        self.spot_calls += 1
        self.started.set()
        await self.release.wait()
        return [market_on(self.name, "BTCUSDT")]

    async def fetch_future_tickers(self):
        self.future_calls += 1
        return []


class PartiallyRecoveringAdapter(ExchangeAdapter):
    name = "partial"

    def __init__(self):
        self.spot_calls = 0
        self.future_calls = 0
        self.reset_calls = 0
        self.recovered = False

    async def fetch_spot_tickers(self):
        self.spot_calls += 1
        if not self.recovered:
            raise TimeoutError("PoolTimeout")
        return [market("ETHUSDT")]

    async def fetch_future_tickers(self):
        self.future_calls += 1
        return []

    async def reset_client(self) -> None:
        self.reset_calls += 1
        self.recovered = True


class FlakyAdapter(ExchangeAdapter):
    name = "flaky"

    def __init__(self):
        self.calls = 0
        self.reset_calls = 0
        self.recovered = False

    async def fetch_spot_tickers(self):
        self.calls += 1
        if not self.recovered:
            raise TimeoutError("PoolTimeout")
        return [market("ETHUSDT")]

    async def fetch_future_tickers(self):
        return []

    async def reset_client(self) -> None:
        self.reset_calls += 1
        self.recovered = True


class FakeHistoryRecorder:
    def __init__(self):
        self.calls = []

    async def record(self, opportunities, now=None, force=False):
        self.calls.append((opportunities, now, force))
        return len(opportunities)


def market(symbol: str = "BTCUSDT") -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        base=symbol.removesuffix("USDT"),
        quote="USDT",
        exchange="fixed",
        market_type=MarketType.SPOT,
        bid=99,
        ask=100,
        volume_24h_usdt=1_000_000,
        timestamp=datetime.now(UTC),
        raw_symbol=symbol,
    )


def market_on(exchange: str, symbol: str = "BTCUSDT") -> MarketSnapshot:
    return market(symbol).model_copy(update={"exchange": exchange, "raw_symbol": symbol})


def test_default_exchange_adapters_include_hyperliquid() -> None:
    names = [adapter.name for adapter in default_exchange_adapters()]

    assert "hyperliquid" in names


@pytest.mark.asyncio
async def test_collector_keeps_last_good_snapshot_when_all_exchanges_fail() -> None:
    store = SnapshotStore()
    store.set_markets([market()])
    collector = MarketCollector([FixedAdapter(exc=TimeoutError("timeout"))], store)

    result = await collector.collect_once()

    assert result.markets == store.get_markets()
    assert store.get_markets()[0].symbol == "BTCUSDT"
    assert result.exchange_errors["fixed:spot"] == "timeout"


@pytest.mark.asyncio
async def test_collector_retries_after_all_timeout_failure() -> None:
    store = SnapshotStore()
    flaky = FlakyAdapter()
    collector = MarketCollector([flaky], store)

    result = await collector.collect_once()

    assert result.markets and result.markets[0].symbol == "ETHUSDT"
    assert result.exchange_errors == {}
    assert flaky.calls == 2
    assert flaky.reset_calls == 1


@pytest.mark.asyncio
async def test_collector_retries_pool_timeout_before_reusing_stale_snapshot() -> None:
    store = SnapshotStore()
    store.set_markets([market("BTCUSDT")])
    flaky = FlakyAdapter()
    collector = MarketCollector([flaky], store)

    result = await collector.collect_once()

    assert result.markets and result.markets[0].symbol == "ETHUSDT"
    assert result.exchange_errors == {}
    assert flaky.calls == 2
    assert flaky.reset_calls == 1


@pytest.mark.asyncio
async def test_collector_resets_partial_pool_timeout_adapter_while_keeping_other_markets() -> None:
    store = SnapshotStore()
    stable = FixedAdapter([market("BTCUSDT")], name="stable")
    recovering = PartiallyRecoveringAdapter()
    collector = MarketCollector([stable, recovering], store)

    result = await collector.collect_once()

    assert result.exchange_errors == {}
    assert {row.symbol for row in result.markets} == {"BTCUSDT", "ETHUSDT"}
    assert recovering.reset_calls == 1
    assert recovering.spot_calls == 2


@pytest.mark.asyncio
async def test_collector_refreshes_due_exchanges_while_failed_exchange_cools_down() -> None:
    clock = FakeClock()
    store = SnapshotStore()
    stable = MutableAdapter("stable", "BTCUSDT")
    flaky = SwitchablePoolTimeoutAdapter("flaky", "ETHUSDT")
    collector = MarketCollector(
        [stable, flaky],
        store,
        poll_interval_seconds=8,
        now_fn=clock.now,
    )
    await collector.collect_once()

    stable.symbol = "SOLUSDT"
    flaky.fail = True
    clock.advance(8)
    result = await collector.collect_once()

    assert {row.symbol for row in result.markets} == {"SOLUSDT", "ETHUSDT"}
    assert stable.spot_calls == 2
    assert flaky.spot_calls == 3
    assert flaky.reset_calls == 1
    assert result.exchange_errors["flaky:spot"] == "PoolTimeout"
    states = collector.exchange_states()
    assert states["stable"]["status"] == "healthy"
    assert states["flaky"]["status"] == "cooling_down"
    assert states["flaky"]["consecutive_failures"] == 1
    assert states["flaky"]["cooldown_until"] == datetime(2026, 5, 21, 12, 0, 23, tzinfo=UTC)


@pytest.mark.asyncio
async def test_collector_skips_cooling_exchange_and_reuses_last_snapshot() -> None:
    clock = FakeClock()
    store = SnapshotStore()
    stable = MutableAdapter("stable", "BTCUSDT")
    flaky = SwitchablePoolTimeoutAdapter("flaky", "ETHUSDT")
    collector = MarketCollector(
        [stable, flaky],
        store,
        poll_interval_seconds=8,
        now_fn=clock.now,
    )
    await collector.collect_once()
    flaky.fail = True
    clock.advance(8)
    await collector.collect_once()
    flaky_spot_calls_after_failure = flaky.spot_calls
    stable.symbol = "XRPUSDT"

    clock.advance(8)
    result = await collector.collect_once()

    assert {row.symbol for row in result.markets} == {"XRPUSDT", "ETHUSDT"}
    assert stable.spot_calls == 3
    assert flaky.spot_calls == flaky_spot_calls_after_failure
    assert result.exchange_errors["flaky:spot"] == "PoolTimeout"


@pytest.mark.asyncio
async def test_collector_cools_down_partial_connect_timeout_even_with_markets() -> None:
    clock = FakeClock()
    store = SnapshotStore()
    adapter = PartialConnectTimeoutAdapter("partial-timeout", "ETHUSDT")
    collector = MarketCollector(
        [adapter],
        store,
        poll_interval_seconds=8,
        now_fn=clock.now,
    )
    await collector.collect_once()

    adapter.fail_spot = True
    adapter.future_symbol = "SOLUSDT"
    clock.advance(8)
    result = await collector.collect_once()

    assert {row.symbol for row in result.markets} == {"ETHUSDT", "ETHUSDTF"}
    assert result.exchange_errors["partial-timeout:spot"] == "ConnectTimeout"
    state = collector.exchange_states()["partial-timeout"]
    assert state["status"] == "cooling_down"
    assert state["consecutive_failures"] == 1
    assert state["cooldown_until"] == datetime(2026, 5, 21, 12, 0, 23, tzinfo=UTC)


@pytest.mark.asyncio
async def test_collector_does_not_start_duplicate_poll_when_exchange_is_in_flight() -> None:
    store = SnapshotStore()
    store.set_markets([market_on("slow", "ETHUSDT")])
    adapter = BlockingAdapter()
    collector = MarketCollector([adapter], store)

    first_collect = asyncio.create_task(collector.collect_once())
    await adapter.started.wait()
    overlapping_result = await collector.collect_once()

    assert [row.symbol for row in overlapping_result.markets] == ["ETHUSDT"]
    assert adapter.spot_calls == 1

    adapter.release.set()
    completed_result = await first_collect

    assert [row.symbol for row in completed_result.markets] == ["BTCUSDT"]
    assert adapter.spot_calls == 1


@pytest.mark.asyncio
async def test_collector_applies_per_exchange_backoff_for_consecutive_failures() -> None:
    clock = FakeClock()
    store = SnapshotStore()
    flaky = SwitchablePoolTimeoutAdapter("flaky", "ETHUSDT")
    collector = MarketCollector(
        [flaky],
        store,
        poll_interval_seconds=8,
        now_fn=clock.now,
    )
    await collector.collect_once()
    flaky.fail = True

    clock.advance(8)
    await collector.collect_once()
    assert collector.exchange_states()["flaky"]["cooldown_until"] == datetime(
        2026, 5, 21, 12, 0, 23, tzinfo=UTC
    )

    clock.advance(15)
    await collector.collect_once()
    assert collector.exchange_states()["flaky"]["cooldown_until"] == datetime(
        2026, 5, 21, 12, 0, 53, tzinfo=UTC
    )

    clock.advance(30)
    await collector.collect_once()
    state = collector.exchange_states()["flaky"]
    assert state["consecutive_failures"] == 3
    assert state["cooldown_until"] == datetime(2026, 5, 21, 12, 1, 53, tzinfo=UTC)


@pytest.mark.asyncio
async def test_collector_restores_exchange_to_healthy_after_cooldown_success() -> None:
    clock = FakeClock()
    store = SnapshotStore()
    flaky = SwitchablePoolTimeoutAdapter("flaky", "ETHUSDT")
    collector = MarketCollector(
        [flaky],
        store,
        poll_interval_seconds=8,
        now_fn=clock.now,
    )
    await collector.collect_once()
    flaky.fail = True
    clock.advance(8)
    await collector.collect_once()

    flaky.fail = False
    flaky.symbol = "SOLUSDT"
    clock.advance(15)
    result = await collector.collect_once()

    assert [row.symbol for row in result.markets] == ["SOLUSDT"]
    assert result.exchange_errors == {}
    state = collector.exchange_states()["flaky"]
    assert state["status"] == "healthy"
    assert state["consecutive_failures"] == 0
    assert state["cooldown_until"] is None
    assert state["last_success_at"] == datetime(2026, 5, 21, 12, 0, 23, tzinfo=UTC)


@pytest.mark.asyncio
async def test_collector_does_not_label_low_volume_when_all_volume_is_missing() -> None:
    store = SnapshotStore()
    buy_leg = MarketSnapshot(
        symbol="IRYSUSDT",
        base="IRYS",
        quote="USDT",
        exchange="binance",
        market_type=MarketType.FUTURE,
        bid=1,
        ask=1.01,
        volume_24h_usdt=None,
        timestamp=datetime.now(UTC),
        raw_symbol="IRYSUSDT",
    )
    sell_leg = buy_leg.model_copy(
        update={
            "exchange": "gate",
            "bid": 1.05,
            "ask": 1.06,
            "volume_24h_usdt": None,
        }
    )
    store.set_markets([buy_leg, sell_leg])
    collector = MarketCollector([FixedAdapter(markets=[buy_leg, sell_leg])], store)

    result = await collector.collect_once()

    assert result.opportunities
    assert "LOW_VOLUME" not in result.opportunities[0].risk_labels


@pytest.mark.asyncio
async def test_collector_skips_ignored_exchanges_and_blacklisted_symbols() -> None:
    store = SnapshotStore()
    active = FixedAdapter([market_on("binance", "BTCUSDT"), market_on("binance", "BADUSDT")])
    ignored = FixedAdapter([market_on("gate", "ETHUSDT")], name="gate")

    async def load_settings() -> RiskSettings:
        return RiskSettings(excluded_symbols=["BADUSDT"], ignored_exchanges=["gate"])

    collector = MarketCollector([active, ignored], store, risk_settings_loader=load_settings)

    result = await collector.collect_once()

    assert [item.symbol for item in result.markets] == ["BTCUSDT"]
    assert [item.symbol for item in store.get_markets()] == ["BTCUSDT"]
    assert active.spot_calls == 1
    assert active.future_calls == 1
    assert ignored.spot_calls == 0
    assert ignored.future_calls == 0


@pytest.mark.asyncio
async def test_collector_records_filtered_opportunities_to_history() -> None:
    store = SnapshotStore()
    history = FakeHistoryRecorder()
    spot = market_on("binance", "BTCUSDT").model_copy(update={"market_type": MarketType.SPOT})
    future = market_on("okx", "BTCUSDT").model_copy(
        update={"market_type": MarketType.FUTURE, "bid": 102, "ask": 103}
    )
    collector = MarketCollector(
        [FixedAdapter([spot, future])],
        store,
        history_recorder=history,
    )

    result = await collector.collect_once()

    assert result.opportunities
    assert len(history.calls) == 1
    assert history.calls[0][0] == result.opportunities
