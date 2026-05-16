from datetime import UTC, datetime

import pytest

from app.exchanges.base import ExchangeAdapter
from app.models.market import MarketSnapshot, MarketType
from app.services.collector import MarketCollector
from app.services.snapshot_store import SnapshotStore


class FixedAdapter(ExchangeAdapter):
    name = "fixed"

    def __init__(self, markets: list[MarketSnapshot] | None = None, exc: Exception | None = None):
        self.markets = markets or []
        self.exc = exc

    async def fetch_spot_tickers(self):
        if self.exc:
            raise self.exc
        return self.markets

    async def fetch_future_tickers(self):
        if self.exc:
            raise self.exc
        return []


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


@pytest.mark.asyncio
async def test_collector_keeps_last_good_snapshot_when_all_exchanges_fail() -> None:
    store = SnapshotStore()
    store.set_markets([market()])
    collector = MarketCollector([FixedAdapter(exc=TimeoutError("timeout"))], store)

    result = await collector.collect_once()

    assert result.markets == store.get_markets()
    assert store.get_markets()[0].symbol == "BTCUSDT"
    assert result.exchange_errors["fixed:spot"] == "timeout"
