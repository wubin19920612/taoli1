"""PM R1-R4 regressions through collection, validation cache and publication."""

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from test_gate_bbo import NOW, ready_runtime

from app.exchanges.gate import GateAdapter
from app.models.market import MarketSnapshot, MarketType
from app.models.market_identity import MarketKey
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.services.collector import MarketCollector
from app.services.market_validation_review import MarketValidationService
from app.services.quote_age import market_time_source
from app.services.snapshot_consumers import LatestSnapshotSlot
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import _has_executable_book


def adapter_for(rt, symbols=("BTC_USDT",)):
    adapter = GateAdapter(client=Mock(), bbo_runtime=rt)
    adapter.get_json = AsyncMock(return_value=[{
        "currency_pair": raw, "contract": raw, "highest_bid": "90", "lowest_ask": "91",
        "quote_volume": "5000000", "volume_24h_quote": "5000000",
    } for raw in symbols])
    adapter._fetch_contract_info = AsyncMock(return_value={
        raw: {"quanto_multiplier": "0.01"} for raw in symbols
    })
    return adapter


@pytest.mark.parametrize("kind", [MarketType.SPOT, MarketType.FUTURE])
@pytest.mark.parametrize("state", ["ws", "disabled", "silent", "old"])
@pytest.mark.parametrize("cache_offset", [None, -8, 0, 1])
async def test_final_publication_preserves_gate_quote_contract(monkeypatch, kind, state, cache_offset):
    rt = ready_runtime(monkeypatch)
    if state == "disabled":
        rt.settings = replace(rt.settings, gate_bbo_enabled=False)
    elif state == "silent":
        rt._connections[str(kind)]["last_message_at"] = 94
    elif state == "old":
        key = (str(kind), "BTC_USDT")
        quote = list(rt._quotes[key])
        quote[4] -= 121_000
        rt._quotes[key] = tuple(quote)
    adapter = adapter_for(rt)
    monkeypatch.setattr("app.exchanges.gate.utc_now", lambda: NOW)
    # Keep the complete collector path, isolate only the other market category.
    other = "fetch_future_tickers" if kind == MarketType.SPOT else "fetch_spot_tickers"
    monkeypatch.setattr(adapter, other, AsyncMock(return_value=[]))
    store, slot = SnapshotStore(), LatestSnapshotSlot()
    validation = MarketValidationService(SimpleNamespace(discover=AsyncMock()), store, [adapter])

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(seconds=2)

    monkeypatch.setattr("app.services.market_validation_review.datetime", Clock)
    seed = MarketSnapshot(symbol="BTCUSDT", base="BTC", exchange="gate", market_type=kind,
                          raw_symbol="BTC_USDT", bid=90, ask=91, timestamp=NOW)
    if cache_offset is not None:
        annotated = validation.annotate(seed, NOW)
        book = OrderBookSnapshot(
            exchange="gate", symbol="BTCUSDT", raw_symbol="BTC_USDT", market_type=kind,
            bids=[OrderBookLevel(price=80, size=20)], asks=[OrderBookLevel(price=81, size=30)],
            timestamp=NOW + timedelta(seconds=cache_offset), timestamp_source="upstream",
        )
        validation.books[MarketKey.from_snapshot(seed).key] = (
            book, annotated.binding_revision, annotated.binding_fingerprint, 1.0, 1.0,
        )
    collector = MarketCollector([adapter], store, market_validation=validation,
                               now_fn=lambda: NOW, snapshot_slots=[slot])
    result = await collector.collect_once()
    published = await asyncio.wait_for(slot.get_newer(0, asyncio.Event()), timeout=1)
    assert not result.exchange_errors
    assert len(published.markets) == 1
    assert len(store.get_all_markets()) == 1
    newer_rest = state == "ws" and cache_offset == 1
    for row in [*published.markets, *store.get_all_markets(), *result.markets]:
        expected_source = "upstream" if newer_rest else "ws" if state == "ws" else "received_batch"
        assert row.quote_time_source == expected_source
        assert market_time_source(row) == expected_source
        assert row.is_estimated is (state != "ws")
        assert row.estimated_fields == ([] if state == "ws" else ["bid", "ask"])
        assert (row.bid, row.ask) == ((80, 81) if newer_rest else (100, 101) if state == "ws" else (90, 91))
        assert _has_executable_book(row) is (state == "ws")
        if state == "ws":
            scale = .01 if kind == MarketType.FUTURE else 1
            assert (row.bid_size, row.ask_size) == ((20, 30) if newer_rest else (2 * scale, 3 * scale))
            assert row.upstream_timestamp == NOW + timedelta(seconds=1 if newer_rest else 0)
    await validation.aclose()


@pytest.mark.parametrize("kind", [MarketType.SPOT, MarketType.FUTURE])
@pytest.mark.parametrize("method", ["get_fresh", "record_merge"])
@pytest.mark.parametrize("error", [OSError, RuntimeError, ValueError])
async def test_bbo_exception_isolates_one_row_and_counts(monkeypatch, kind, method, error):
    rt = ready_runtime(monkeypatch)
    original = getattr(rt, method)
    calls = 0

    def fail_middle(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error("injected")
        return original(*args)

    monkeypatch.setattr(rt, method, fail_middle)
    adapter = adapter_for(rt, ("BTC_USDT", "BTC_USDT", "BTC_USDT"))
    rows = await (adapter.fetch_spot_tickers() if kind == MarketType.SPOT else adapter.fetch_future_tickers())
    assert len(rows) == 3
    assert [row.quote_time_source for row in rows] == ["ws", "received_batch", "ws"]
    assert [row.is_estimated for row in rows] == [False, True, False]
    assert rows[1].estimated_fields == ["bid", "ask"]
    assert (rows[1].bid, rows[1].ask, rows[1].bid_size, rows[1].ask_size) == (90, 91, None, None)
    assert rt.status()["merge_errors"] == 1
    assert rt.status()["last_merge_error"] == {"stage": method, "type": error.__name__}


async def test_merge_errors_are_counted_without_log_flood(monkeypatch):
    rt = ready_runtime(monkeypatch)
    monkeypatch.setattr(rt, "get_fresh", Mock(side_effect=OSError("private details")))
    log = Mock()
    monkeypatch.setattr("app.services.gate_bbo_runtime.logger.warning", log)
    adapter = adapter_for(rt, tuple("BTC_USDT" for _ in range(100)))
    for _ in range(2):
        assert len(await adapter.fetch_spot_tickers()) == 100
    assert rt.status()["merge_errors"] == 200
    assert log.call_count == 1
    payload = json.loads(log.call_args.args[0])["gate_bbo"]
    assert payload["merge_errors"] == 1
    assert "private details" not in log.call_args.args[0]
    monkeypatch.setattr("app.services.gate_bbo_runtime.monotonic", lambda: 160)
    await adapter.fetch_spot_tickers()
    assert rt.status()["merge_errors"] == 300
    assert log.call_count == 2


@pytest.mark.parametrize("kind", [MarketType.SPOT, MarketType.FUTURE])
async def test_collection_constructs_once_without_model_copy(monkeypatch, kind):
    rt = ready_runtime(monkeypatch)
    adapter = adapter_for(rt, ("BTC_USDT", "OTHER_USDT"))
    monkeypatch.setattr(MarketSnapshot, "model_copy", Mock(side_effect=AssertionError("extra copy")))
    rows = await (adapter.fetch_spot_tickers() if kind == MarketType.SPOT else adapter.fetch_future_tickers())
    assert [row.quote_time_source for row in rows] == ["ws", "received_batch"]


async def test_logging_failure_cannot_discard_fallback_tickers(monkeypatch):
    rt = ready_runtime(monkeypatch)
    monkeypatch.setattr(rt, "get_fresh", Mock(side_effect=OSError("injected")))
    monkeypatch.setattr("app.services.gate_bbo_runtime.logger.warning",
                        Mock(side_effect=OSError("log unavailable")))
    rows = await adapter_for(rt).fetch_spot_tickers()
    assert len(rows) == 1
    assert rows[0].quote_time_source == "received_batch"
    assert rows[0].is_estimated and rows[0].estimated_fields == ["bid", "ask"]
    assert rt.status()["merge_errors"] == 1


@pytest.mark.parametrize("source", ["ws", "received_batch"])
@pytest.mark.parametrize("upstream", [None, NOW])
def test_explicit_time_source_helper(source, upstream):
    row = MarketSnapshot(symbol="BTCUSDT", base="BTC", exchange="gate", market_type="spot",
                         raw_symbol="BTC_USDT", bid=90, ask=91, timestamp=NOW,
                         quote_time_source=source, upstream_timestamp=upstream)
    assert market_time_source(row) == source


def test_manual_fresh_book_can_still_qualify_gate_fallback():
    # The no-upgrade rule is for background cache reuse, not an explicit fresh review.
    row = MarketSnapshot(symbol="BTCUSDT", base="BTC", exchange="gate", market_type="spot",
                         raw_symbol="BTC_USDT", bid=90, ask=91, timestamp=NOW,
                         quote_time_source="received_batch", is_estimated=True,
                         estimated_fields=["bid", "ask"])
    book = OrderBookSnapshot(exchange="gate", symbol=row.symbol, raw_symbol=row.raw_symbol,
                             market_type=row.market_type, timestamp=NOW,
                             bids=[OrderBookLevel(price=100, size=2)],
                             asks=[OrderBookLevel(price=101, size=3)])
    reviewed = MarketValidationService._book_row(row, book)
    assert (reviewed.bid, reviewed.ask) == (100, 101)
    assert reviewed.is_estimated is False
    assert reviewed.estimated_fields == []
