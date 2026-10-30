"""Frozen pre-M2-4a storage oracle exercised through the real collector pipeline."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.exchanges.base import ExchangeAdapter
from app.models.market import MarketSnapshot, MarketType
from app.models.market_identity import IdentityEvidence, MarketBinding, MarketKey
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.models.settings import RiskSettings
from app.services import market_validation_review, pair_validation
from app.services.collector import AdapterPollResult, MarketCollector
from app.services.compact_markets import CompactMarkets
from app.services.market_identity import MarketIdentityRegistry
from app.services.market_validation_review import MarketValidationService
from app.services.snapshot_store import SnapshotStore


class LegacyCollector(MarketCollector):
    # Frozen verbatim from ed8eb292; independent of the new storage implementation.
    def _seed_exchange_snapshots_from_store(self) -> None:
        if self._exchange_snapshots:
            return
        for market in self.store.get_markets():
            self._exchange_snapshots.setdefault(market.exchange, []).append(market)

    def _store_adapter_result(
        self,
        exchange_name: str,
        result: AdapterPollResult,
        now: datetime,
    ) -> None:
        state = self._state_for(exchange_name)
        state.in_flight = False
        if result.errors:
            if result.markets and not result.should_cool_down:
                self._exchange_snapshots[exchange_name] = result.markets
            self._exchange_errors[exchange_name] = result.errors
            state.last_error_at = now
            state.consecutive_failures += 1
            if result.should_cool_down:
                cooldown_seconds = result.cooldown_seconds or self._cooldown_seconds(
                    state.consecutive_failures
                )
                state.status = "cooling_down"
                state.cooldown_until = now + timedelta(seconds=cooldown_seconds)
                state.next_due_at = state.cooldown_until
            else:
                state.status = "degraded"
                state.cooldown_until = None
                state.next_due_at = now + timedelta(seconds=self.poll_interval_seconds)
            return

        self._exchange_snapshots[exchange_name] = result.markets
        self._exchange_errors.pop(exchange_name, None)
        state.status = "healthy"
        state.last_success_at = now
        state.consecutive_failures = 0
        state.cooldown_until = None
        state.next_due_at = now + timedelta(seconds=self.poll_interval_seconds)

    def _combined_exchange_markets(self, adapters: list[ExchangeAdapter]) -> list[MarketSnapshot]:
        markets: list[MarketSnapshot] = []
        for adapter in adapters:
            markets.extend(self._exchange_snapshots.get(adapter.name, []))
        return markets


NOW = datetime(2026, 10, 28, tzinfo=UTC)


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz else cls.current.replace(tzinfo=None)


def market(exchange, price=10.0):
    hl = exchange == "hyperliquid"
    return MarketSnapshot(
        exchange=exchange, symbol="ANTHUSDT" if hl else "ANTHROPICUSDT",
        base="ANTH" if hl else "ANTHROPIC", raw_symbol="io:ANTH" if hl else "ANTHROPICUSDT",
        dex="io" if hl else None,
        market_type=MarketType.SPOT if exchange == "binance" else MarketType.FUTURE,
        bid=price, ask=price * 1.001, bid_size=1000.0, ask_size=1100.0,
        mark_price=price, index_price=price, timestamp=NOW, upstream_timestamp=NOW,
        funding_rate_pct=0.01 if exchange != "binance" else None,
        funding_interval_hours=8 if exchange != "binance" else None,
        volume_24h_usdt=2_000_000, estimated_fields=["funding_rate_pct"],
    )


class ScriptedAdapter(ExchangeAdapter):
    def __init__(self, name):
        self.name = name
        self.round = 0
        self.calls = []
        self.market_errors = {}

    async def fetch_spot_tickers(self):
        self.calls.append((self.round, "spot"))
        self.market_errors = {}
        if self.round == 3 and self.name == "okx":
            raise ValueError("partial spot unavailable")
        if self.round == 4 and self.name == "okx":
            raise TimeoutError("ConnectTimeout")
        if self.round == 7 or self.name != "binance":
            return []
        excluded = market("binance", 2.0).model_copy(update={
            "symbol": "EXCLUDEDUSDT", "base": "EXCLUDED", "raw_symbol": "EXCLUDEDUSDT",
        })
        return [market("binance", 98.0 + self.round), excluded]

    async def fetch_future_tickers(self):
        self.calls.append((self.round, "future"))
        if self.round == 3 and self.name == "hyperliquid":
            self.market_errors = {"hyperliquid:io:OTHER": "one market unavailable"}
        if self.round == 7 or self.name == "binance":
            return []
        price = 10.0 + self.round / 10 if self.name == "okx" else 104.0 + self.round
        if self.round == 4 and self.name == "okx":
            price = 999.0  # Partial timeout must keep the previous raw snapshot.
        return [market(self.name, price)]


def binding(row):
    return MarketBinding(
        market=MarketKey.from_snapshot(row), asset_id="anthropic", display_name="Anthropic",
        identity_status="verified", unit_status="verified", verified_at=NOW,
        valid_until=NOW + timedelta(days=1),
        quote_currency="USDC" if row.exchange == "hyperliquid" else "USDT",
        price_multiplier=10.0 if row.exchange == "okx" else 1.0,
        evidence=[IdentityEvidence(source_type="official_announcement",
                                   url="https://example.invalid/offline-fixture",
                                   summary="Offline verified fixture", fetched_at=NOW)],
    )


def setup_collector(cls):
    adapters = [ScriptedAdapter(name) for name in ("okx", "hyperliquid", "binance")]
    store = SnapshotStore()
    # Deliberately seed in the opposite order; publication must use adapter order.
    seeds = [market("binance", 98.0), market("hyperliquid", 104.0), market("okx", 10.0)]
    store.set_markets(seeds)
    repo = SimpleNamespace(discover=AsyncMock(), invalidate_opportunities=AsyncMock())
    validation = MarketValidationService(repo, store, adapters)
    bindings = [binding(row) for row in seeds]
    validation.registry = MarketIdentityRegistry(bindings, mode="shadow")
    validation.fx = (0.999, 1.001, NOW, NOW + timedelta(seconds=16))
    validation.fx_retry_at = NOW + timedelta(days=1)  # No live FX refresh.
    annotated = validation.annotate(seeds[1], NOW)
    book = OrderBookSnapshot(
        exchange="hyperliquid", symbol=annotated.symbol, market_type=MarketType.FUTURE,
        raw_symbol="io:ANTH", timestamp=NOW,
        bids=[OrderBookLevel(price=104.2, size=500)],
        asks=[OrderBookLevel(price=104.3, size=600)],
    )
    validation.books[MarketKey.from_snapshot(annotated).key] = (
        book, annotated.binding_revision, annotated.binding_fingerprint,
        annotated.quote_conversion_bid, annotated.quote_conversion_ask,
    )
    collector = cls(
        adapters, store, now_fn=lambda: Clock.current, market_validation=validation,
        risk_settings=RiskSettings(stale_after_seconds=300, huge_spread_pct=10000,
                                   excluded_symbols=["EXCLUDEDUSDT"]),
    )
    for adapter in adapters:
        collector._state_for(adapter.name).next_due_at = NOW + timedelta(seconds=5)
    return collector, validation, bindings


def output(store):
    return {
        "all_markets": [row.model_dump() for row in store.get_all_markets()],
        "markets": [row.model_dump() for row in store.get_markets()],
        "opportunities": [row.model_dump() for row in store.get_opportunities()],
        "exchange_errors": store.get_exchange_errors(),
    }


@pytest.mark.asyncio
async def test_nine_rounds_match_frozen_storage_with_real_aliases_and_validation(monkeypatch):
    monkeypatch.setattr(market_validation_review, "datetime", Clock)
    monkeypatch.setattr(pair_validation, "datetime", Clock)
    monkeypatch.setattr(Clock, "current", NOW)
    old, old_validation, old_bindings = setup_collector(LegacyCollector)
    new, new_validation, new_bindings = setup_collector(MarketCollector)
    published = []
    # Seed, due, not due, partial/degraded, partial timeout, cooldown, recovery,
    # empty success, then not due (must not resurrect old store rows).
    for round_number, seconds in enumerate((0, 5, 6, 10, 15, 16, 45, 50, 51)):
        Clock.current = NOW + timedelta(seconds=seconds)
        for collector, validation, bindings in (
            (old, old_validation, old_bindings), (new, new_validation, new_bindings),
        ):
            for adapter in collector.adapters:
                adapter.round = round_number
            if round_number == 5:
                validation.registry.replace([b for b in bindings if b.market.exchange != "hyperliquid"])
                validation.books.clear()
            elif round_number == 6:
                validation.registry.replace(bindings)
            await collector.collect_once()
            assert not validation.tasks and validation.fx_task is None
        actual, expected = output(new.store), output(old.store)
        assert actual == expected, f"publication differs in round {round_number}"
        assert new.exchange_states() == old.exchange_states()
        assert all(isinstance(batch, CompactMarkets) for batch in new._exchange_snapshots.values())
        assert [a.calls for a in new.adapters] == [a.calls for a in old.adapters]
        published.append(actual)
        rows = new.store.get_all_markets()
        if round_number < 7:
            assert [r.exchange for r in rows[:2]] == ["okx", "hyperliquid"]
            assert rows[0].bid == pytest.approx(100.0 if round_number == 0 else
                                              101.0 if round_number < 3 else
                                              103.0 if round_number < 6 else 106.0)
            assert rows[0].symbol_alias_price_multiplier == 10.0
            assert rows[1].raw_symbol == "io:ANTH" and rows[1].dex == "io"
        if round_number == 0:
            assert not any(a.calls for a in new.adapters)
            assert rows[1].bid == 104.2  # Real cached-book application.
            assert rows[1].quote_conversion_source is not None
        if round_number == 3:
            assert new.exchange_states()["okx"]["status"] == "degraded"
            assert new.exchange_states()["hyperliquid"]["status"] == "degraded"
        if round_number in (4, 5):
            assert new.exchange_states()["okx"]["status"] == "cooling_down"
        if round_number == 5:
            assert rows[1].asset_id is None and rows[1].book_timestamp is None
            assert rows[1].quote_conversion_source is None
            assert rows[1].bid == 108.0
        if round_number >= 7:
            assert actual == {"all_markets": [], "markets": [], "opportunities": [],
                              "exchange_errors": {}}
    for name in ("all_markets", "markets", "exchange_errors"):
        assert published[1][name] == published[2][name]
    assert published[0]["opportunities"]  # Comparison is not vacuous.
    assert len(published[1]["all_markets"]) > len(published[1]["markets"])
    assert [r for r, kind in new.adapters[0].calls if kind == "spot"] == [1, 3, 4, 6, 7]
    assert new_validation.discovered == old_validation.discovered
    assert new_validation.repository.discover.call_count == old_validation.repository.discover.call_count
