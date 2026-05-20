from datetime import UTC, datetime

import pytest

from app.exchanges.base import ExchangeAdapter
from app.models.market import MarketSnapshot, MarketType
from app.models.settings import RiskSettings
from app.services.collector import MarketCollector, default_exchange_adapters
from app.services.snapshot_store import SnapshotStore


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
