"""M2-3b: offline comparisons against cache logic frozen from dafc8b4."""

import asyncio
import gc
import time
import weakref
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from itertools import combinations
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_market_identity_validation import NOW, binding, market

from app.models.market_identity import (
    MarketKey,
    MarketValidationSettings,
    ValidationIssue,
    utc,
)
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.models.settings import RiskSettings
from app.runtime.cache_census import deep_size
from app.services import market_validation_review, pair_validation
from app.services.market_identity import MarketIdentityRegistry
from app.services.market_validation_review import MarketValidationService
from app.services.pair_validation import validate_pair


class LegacyCacheService(MarketValidationService):
    # Frozen verbatim from dafc8b4; do not follow production cache changes.
    def __init__(self, repository, store, adapters=()):
        self.repository = repository
        self.store = store
        self.registry = MarketIdentityRegistry(mode="shadow")
        self.settings = MarketValidationSettings()
        self.adapters = {a.name.lower(): a for a in adapters}
        self.books = OrderedDict()
        self.reviews = OrderedDict()
        self.tasks = {}
        self.failures = OrderedDict()
        self.discovered = set()
        self.semaphore = asyncio.Semaphore(3)
        self.book_semaphore = asyncio.Semaphore(3)
        self.review_inflight = {}
        self.review_attempted = OrderedDict()
        self.fx = None
        self.fx_task = None
        self.fx_retry_at = datetime.min.replace(tzinfo=UTC)
        self._refresh_lock = asyncio.Lock()
        self._mutation_epoch = 0
        self._last_incident = {}
        self.collision_symbols = set()
        self.invalidated_ids = set()

    @staticmethod
    def _bounded_put(cache, key, value, limit=1000):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    def _cached_book(self, row, now, stale_after):
        try:
            cached = self.books.get(MarketKey.from_snapshot(row).key)
        except ValueError:
            return row
        if not cached:
            return row
        book, revision, fingerprint, fx_bid, fx_ask = cached
        if (
            revision != row.binding_revision
            or fingerprint != row.binding_fingerprint
            or (now - utc(book.timestamp)).total_seconds() > stale_after
        ):
            return row
        if (fx_bid, fx_ask) != (row.quote_conversion_bid, row.quote_conversion_ask):
            return row
        return self._book_row(row, book)

    @staticmethod
    def _book_row(row, book):
        return row.model_copy(
            update={
                "bid": book.bids[0].price,
                "ask": book.asks[0].price,
                "bid_size": book.bids[0].size,
                "ask_size": book.asks[0].size,
                "book_timestamp": book.timestamp,
                "quote_time_source": book.timestamp_source,
                "upstream_timestamp": book.timestamp,
                "quote_kind": "book",
                "is_estimated": False,
                "estimated_fields": [f for f in row.estimated_fields if f not in {"bid", "ask"}],
                "data_source": (row.data_source or row.exchange) + " + exact l2Book",
            }
        )

    async def prepare(self, markets, risk):
        now = datetime.now(UTC)
        unknown = []
        for row in markets:
            try:
                key = MarketKey.from_snapshot(row).key
            except ValueError:
                continue
            if key not in self.discovered:
                unknown.append(row)
                self.discovered.add(key)
        if unknown:
            await self.repository.discover(unknown)
        annotated = [
            self._cached_book(self._annotate_base(row, now), now, risk.stale_after_seconds)
            for row in markets
        ]
        assets_by_symbol = {}
        kinds_by_symbol = {}
        for row in annotated:
            if row.asset_id:
                assets_by_symbol.setdefault(row.symbol, set()).add(row.asset_id)
            if row.asset_kind:
                kinds_by_symbol.setdefault(row.symbol, set()).add(row.asset_kind)
        self.collision_symbols = {
            symbol for symbol, assets in assets_by_symbol.items() if len(assets) > 1
        }
        self.collision_symbols.update(
            symbol for symbol, kinds in kinds_by_symbol.items() if len(kinds) > 1
        )
        annotated = [
            row
            if row.identity_collision == (row.symbol in self.collision_symbols)
            else row.model_copy(update={"identity_collision": row.symbol in self.collision_symbols})
            for row in annotated
        ]
        if self.collision_symbols:
            from app.services.spread_engine import opportunity_id, pair_allowed

            invalid = set()
            for symbol in self.collision_symbols:
                collision_rows = [row for row in annotated if row.symbol == symbol and row.asset_id]
                for a, b in combinations(collision_rows, 2):
                    if a.asset_id == b.asset_id:
                        continue
                    for first, second in ((a, b), (b, a)):
                        for mode in ("SF", "FF", "SS"):
                            if pair_allowed(mode, first, second):
                                invalid.add(opportunity_id(mode, symbol, first, second))
            fresh_invalid = invalid - self.invalidated_ids
            if fresh_invalid:
                await self.repository.invalidate_opportunities(fresh_invalid)
                self.invalidated_ids.update(fresh_invalid)
        if (
            any(row.original_quote_currency == "USDC" for row in annotated)
            and now >= self.fx_retry_at
            and (not self.fx or (self.fx[3] - now).total_seconds() < 5)
            and (self.fx_task is None or self.fx_task.done())
        ):
            self.fx_task = asyncio.create_task(self._refresh_fx())
        groups = {}
        for row in annotated:
            if row.identity_status == "verified" and row.unit_status == "verified" and row.asset_id:
                groups.setdefault(row.asset_id, []).append(row)
            elif self.settings.mode == "shadow" and not row.identity_collision:
                groups.setdefault("unverified:" + row.symbol, []).append(row)
        if self.settings.mode == "shadow":
            for row in annotated:
                key = "unverified:" + row.symbol
                if (
                    row.identity_status == "verified"
                    and key in groups
                    and not row.identity_collision
                ):
                    groups[key].append(row)
        candidates = []
        for legs in groups.values():
            for a, b in combinations(legs, 2):
                if a.exchange == b.exchange and a.market_type == b.market_type:
                    continue
                spread = abs(2 * (b.bid - a.ask) / (b.bid + a.ask) * 100)
                if (
                    a.quote_kind == "reference"
                    or b.quote_kind == "reference"
                    or spread >= risk.huge_spread_pct
                ):
                    if (
                        a.market_type == b.market_type
                        and a.bid > b.ask
                        or a.market_type == "future"
                        and b.market_type == "spot"
                    ):
                        a, b = b, a
                    candidates.append((spread, a, b))
        # Limit scheduling, not just concurrent network requests.
        candidates.sort(
            key=lambda item: (
                self.review_attempted.get(self.pair_key(item[1], item[2]), 0),
                -item[0],
            )
        )
        for _, a, b in candidates:
            if len(self.tasks) >= 12:
                break
            key = self.pair_key(a, b)
            cached = self.reviews.get(key)
            if cached and cached.expires_at > now:
                continue
            if self.failures.get(key, now) > now or key in self.tasks:
                continue
            self._bounded_put(self.review_attempted, key, now.timestamp(), limit=5000)
            task = asyncio.create_task(self._background_review(a, b, risk, key))
            self.tasks[key] = task
        return annotated

    async def _background_review(self, a, b, risk, key):
        try:
            result, _, _ = await self.review(a, b, risk, twice=True)
            if self.repository is not None:
                incident_key = str(key)
                last = self._last_incident.get(incident_key)
                now = datetime.now(UTC)
                if last is None or (now - last).total_seconds() >= 60:
                    await self.repository.record_incident(
                        incident_key,
                        "SPREAD_REVIEW",
                        {"reasons": result.reason_codes},
                        "open" if result.blockers else "resolved",
                    )
                    self._last_incident[incident_key] = now
                    if len(self._last_incident) > 1000:
                        self._last_incident.pop(next(iter(self._last_incident)))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - isolate a failed background review; no trading side effects.
            self._bounded_put(
                self.failures,
                key,
                datetime.now(UTC)
                + timedelta(seconds=self.settings.review_failure_cooldown_seconds),
            )
        finally:
            self.tasks.pop(key, None)

    async def _book(self, row):
        adapter = self.adapters.get(row.exchange)
        if adapter is None:
            raise ValueError("missing book adapter")
        async with self.book_semaphore:
            book = await asyncio.wait_for(
                adapter.fetch_order_book(row.symbol, row.market_type, row.raw_symbol, 20),
                self.settings.review_timeout_seconds,
            )
        if book is None or not book.bids or not book.asks or book.raw_symbol != row.raw_symbol:
            raise ValueError("book unavailable or exact raw market mismatch")
        bids = sorted(book.bids, key=lambda x: x.price, reverse=True)
        asks = sorted(book.asks, key=lambda x: x.price)
        if bids[0].price >= asks[0].price:
            raise ValueError("crossed book")
        # Adapter snapshots are in their public price units; face-value size
        # conversion is performed by the exchange adapter, as before.
        multiplier = row.symbol_alias_price_multiplier
        fx_bid = row.quote_conversion_bid if row.quote_conversion_source else 1
        fx_ask = row.quote_conversion_ask if row.quote_conversion_source else 1
        normalized = book.model_copy(
            update={
                "bids": [
                    x.model_copy(
                        update={"price": x.price * multiplier * fx_bid, "size": x.size / multiplier}
                    )
                    for x in bids
                ],
                "asks": [
                    x.model_copy(
                        update={"price": x.price * multiplier * fx_ask, "size": x.size / multiplier}
                    )
                    for x in asks
                ],
            }
        )
        self._bounded_put(
            self.books,
            MarketKey.from_snapshot(row).key,
            (
                normalized,
                row.binding_revision,
                row.binding_fingerprint,
                row.quote_conversion_bid,
                row.quote_conversion_ask,
            ),
        )
        return normalized

    def validate(self, a, b, risk=None, *, comparison_kind="same_asset"):
        risk = risk or RiskSettings()
        now = datetime.now(UTC)
        a, b = self.annotate(a, now), self.annotate(b, now)
        cached = self.reviews.get(self.pair_key(a, b))
        # Cached depth reviews can qualify only that exact sampled quote.
        from app.services.pair_validation import quote_fingerprint

        cached_blockers = set(cached.reason_codes) if cached else set()
        if self.settings.mode == "shadow":
            cached_blockers -= {"ASSET_IDENTITY_UNVERIFIED", "UNIT_UNVERIFIED"}
        passed = bool(
            cached
            and cached.expires_at > now
            and cached.quote_fingerprint == quote_fingerprint(a, b)
            and not cached_blockers
        )
        result = validate_pair(
            a,
            b,
            now=now,
            comparison_kind=comparison_kind,
            huge_spread_pct=risk.huge_spread_pct,
            stale_after_seconds=risk.stale_after_seconds,
            max_leg_time_skew_seconds=self.settings.max_leg_time_skew_seconds,
            review_passed=passed,
        )
        result.registry_revision = self._mutation_epoch
        return result


class FrozenDateTime(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(FrozenDateTime, "current", NOW)
    monkeypatch.setattr(market_validation_review, "datetime", FrozenDateTime)
    monkeypatch.setattr(pair_validation, "datetime", FrozenDateTime)
    monkeypatch.setitem(globals(), "datetime", FrozenDateTime)
    monkeypatch.setattr(market_validation_review.asyncio, "sleep", AsyncMock())
    return FrozenDateTime


class TwentyLevelAdapter:
    def __init__(self, name, rows, sizes, timestamp_source):
        self.name = name
        self.rows = {row.raw_symbol: row for row in rows}
        self.sizes = sizes
        self.timestamp_source = timestamp_source
        self.calls = []
        self.snapshots = []

    async def fetch_order_book(self, symbol, market_type, raw_symbol, limit=20):
        size = self.sizes[min(len(self.calls), len(self.sizes) - 1)]
        self.calls.append((symbol, market_type, raw_symbol, limit))
        row = self.rows[raw_symbol]
        # Deliberately interleave prices; the first received level is not the best.
        order = list(range(7, 20)) + list(range(7))
        book = OrderBookSnapshot(
            exchange=self.name,
            symbol=symbol,
            market_type=market_type,
            raw_symbol=raw_symbol,
            bids=[
                OrderBookLevel(price=row.bid * (1 - i * 0.00001), size=size) for i in order
            ],
            asks=[
                OrderBookLevel(price=row.ask * (1 + i * 0.00001), size=size) for i in order
            ],
            timestamp=FrozenDateTime.current,
            timestamp_source=self.timestamp_source,
        )
        self.snapshots.append(book.model_dump())
        return book


def make_service(cls, rows, *, multiplier=1, fx=False, sizes=(5000,), source="upstream"):
    repository = SimpleNamespace(
        discover=AsyncMock(), invalidate_opportunities=AsyncMock(), record_incident=AsyncMock()
    )
    adapters = [
        TwentyLevelAdapter(exchange, [r for r in rows if r.exchange == exchange], sizes, source)
        for exchange in sorted({row.exchange for row in rows})
    ]
    service = cls(repository, None, adapters)
    bindings = [binding(row, price_multiplier=multiplier) for row in rows]
    if fx:
        bindings = [b.model_copy(update={"quote_currency": "USDC"}) for b in bindings]
        service.fx = (0.999, 1.001, NOW, NOW + timedelta(seconds=300))
    service.registry = MarketIdentityRegistry(bindings)
    return service


def make_pair(cls, **kwargs):
    rows = [market(), market("bybit", price=0.101)]
    return make_service(cls, rows, **kwargs), rows


@pytest.mark.parametrize("multiplier", [1, 0.001])
@pytest.mark.parametrize("fx", [False, True])
@pytest.mark.parametrize("source", ["upstream", "received"])
async def test_full_return_and_top_cache_produce_identical_rows(multiplier, fx, source):
    row = market("hyperliquid", "xyz:PRL", price=100, dex="xyz").model_copy(
        update={"quote_kind": "reference", "estimated_fields": ["bid", "ask", "volume"]}
    )
    old = make_service(LegacyCacheService, [row], multiplier=multiplier, fx=fx, source=source)
    new = make_service(MarketValidationService, [row], multiplier=multiplier, fx=fx, source=source)
    annotated = old.annotate(row)
    assert new.annotate(row) == annotated
    expected = await old._book(annotated)
    actual = await new._book(annotated)
    assert actual == expected
    assert len(actual.bids) == len(actual.asks) == 20
    assert actual.bids[0].price == pytest.approx(row.bid * multiplier * (0.999 if fx else 1))
    assert actual.asks[0].price == pytest.approx(row.ask * multiplier * (1.001 if fx else 1))
    assert actual.bids[0].size == pytest.approx(5000 / multiplier)
    assert actual.bids == sorted(actual.bids, key=lambda level: level.price, reverse=True)
    assert actual.asks == sorted(actual.asks, key=lambda level: level.price)
    key = MarketKey.from_snapshot(annotated).key
    cached = new.books[key][0]
    assert cached is not actual
    assert cached.bids is not actual.bids and cached.asks is not actual.asks
    assert cached.model_dump() == actual.model_copy(
        update={"bids": actual.bids[:1], "asks": actual.asks[:1]}
    ).model_dump()
    assert new.books[key][1:] == old.books[key][1:]
    assert new.adapters[row.exchange].snapshots == old.adapters[row.exchange].snapshots
    for service in (old, new):
        # The adapter input and returned full-depth lists must survive caching.
        assert len(service.adapters[row.exchange].snapshots[0]["bids"]) == 20
    expected_row = old._cached_book(annotated, NOW, 30)
    actual_row = new._cached_book(annotated, NOW, 30)
    assert actual_row == expected_row
    assert actual_row.data_source == "hyperliquid + exact l2Book"
    assert actual_row.quote_kind == "book" and actual_row.book_timestamp == NOW
    assert actual_row.quote_time_source == source
    assert actual_row.estimated_fields == ["volume"]
    assert await new.prepare([row], RiskSettings()) == await old.prepare([row], RiskSettings())
    assert new.repository.discover.call_args_list == old.repository.discover.call_args_list
    assert new.annotate(actual_row) == old.annotate(expected_row)
    assert len(actual.bids) == len(actual.asks) == 20


@pytest.mark.parametrize("case", [
    "hit", "revision", "fingerprint", "fx_bid", "fx_ask", "missing", "invalid_dex",
    "at_stale_boundary", "past_stale_boundary", "longer_risk_window",
])
async def test_cached_book_hit_and_miss_match_legacy(case):
    raw = market("hyperliquid", "xyz:PRL", dex="xyz")
    old = make_service(LegacyCacheService, [raw], fx=True)
    new = make_service(MarketValidationService, [raw], fx=True)
    row = new.annotate(raw)
    await old._book(row)
    await new._book(row)
    updates = {
        "revision": {"binding_revision": row.binding_revision + 1},
        "fingerprint": {"binding_fingerprint": "changed"},
        "fx_bid": {"quote_conversion_bid": 0.998},
        "fx_ask": {"quote_conversion_ask": 1.002},
        "missing": {"raw_symbol": "xyz:OTHER"},
        "invalid_dex": {"dex": "other"},
    }
    row = row.model_copy(update=updates.get(case, {}))
    age = {"at_stale_boundary": 30, "past_stale_boundary": 30.000001,
           "longer_risk_window": 45}.get(case, 0)
    stale_after = 60 if case == "longer_risk_window" else 30
    now = NOW + timedelta(seconds=age)
    expected = old._cached_book(row, now, stale_after)
    actual = new._cached_book(row, now, stale_after)
    assert actual == expected
    hit = case in {"hit", "at_stale_boundary", "longer_risk_window"}
    assert (actual is not row) == hit
    assert (expected is not row) == hit


@pytest.mark.parametrize("sizes,blocked,calls", [
    ((1,), True, 1), ((1100,), False, 2), ((1100, 1), True, 2),
])
async def test_twice_review_keeps_full_depth(sizes, blocked, calls):
    old, rows = make_pair(LegacyCacheService, sizes=sizes)
    new, _ = make_pair(MarketValidationService, sizes=sizes)
    risk = RiskSettings()
    expected = await old.review(*rows, risk, twice=True)
    actual = await new.review(*rows, risk, twice=True)
    assert actual == expected
    assert ("DEPTH_UNCONFIRMED" in actual[0].reason_codes) == blocked
    assert actual[0].capabilities.manual_card == (not blocked)
    assert all(len(adapter.calls) == calls for adapter in new.adapters.values())
    assert [a.calls for a in new.adapters.values()] == [a.calls for a in old.adapters.values()]
    if not blocked:
        # One level cannot fill 1000 USDT, but all 20 can satisfy the 2000 USDT gate.
        assert max(book[0].asks[0].price * book[0].asks[0].size for book in new.books.values()) < (
            risk.signal_validation_notional_usdt
        )


async def test_expired_reviews_pruned_without_changing_validate_or_prepare_hits(clock):
    pairs = [
        (market(raw=f"PRL{i}USDT"), market("bybit", raw=f"PRL{i}USDT", price=0.2))
        for i in range(4)
    ]
    rows = [row for pair in pairs for row in pair]
    old = make_service(LegacyCacheService, rows)
    new = make_service(MarketValidationService, rows)
    risk = RiskSettings()
    sampled = []
    for pair, ttl in zip(pairs, (2, 5, 10, 15), strict=True):
        for service in (old, new):
            service.settings = service.settings.model_copy(update={"review_ttl_seconds": ttl})
        expected = await old.review(*pair, risk)
        actual = await new.review(*pair, risk)
        assert actual == expected
        assert actual[0].expires_at == NOW + timedelta(seconds=ttl)
        sampled.append(actual[1:])
    clock.current = NOW + timedelta(seconds=5)
    assert await new.prepare([], risk) == await old.prepare([], risk) == []
    keys = [new.pair_key(*legs) for legs in sampled]
    assert list(new.reviews) == keys[2:]
    assert list(old.reviews) == keys
    for key in keys[2:]:
        assert new.reviews[key] == old.reviews[key]
    for index, legs in enumerate(sampled):
        expected = old.validate(*legs, risk)
        actual = new.validate(*legs, risk)
        assert actual == expected
        assert actual.anomaly_status == ("pending_review" if index < 2 else "passed")
        for service in (old, new):
            service._background_review = AsyncMock()
        assert await new.prepare(legs, risk) == await old.prepare(legs, risk)
        assert set(new.tasks) == set(old.tasks)
        assert (keys[index] in new.tasks) == (index < 2)
        await asyncio.gather(*old.tasks.values(), *new.tasks.values())
        assert new._background_review.call_args_list == old._background_review.call_args_list
        assert new.review_attempted == old.review_attempted


@pytest.mark.parametrize("blocked", [False, True])
async def test_incident_cleanup_preserves_deduplication_and_parameters(clock, blocked):
    old, rows = make_pair(LegacyCacheService)
    new, _ = make_pair(MarketValidationService)
    result = new.validate(*rows)
    if blocked:
        result.blockers.append(ValidationIssue(code="DEPTH_UNCONFIRMED", message="test depth"))
    for service in (old, new):
        service.review = AsyncMock(return_value=(result, *rows))
    sequence = [(0, "hot"), (59, "hot"), (59, "warm"), (60, "new"),
                (60, "hot"), (60.001, "hot"), (120.001, "other"), (120.001, "hot")]
    for age, name in sequence:
        clock.current = NOW + timedelta(seconds=age)
        for service in (old, new):
            service.tasks[(name,)] = object()
            await service._background_review(*rows, RiskSettings(), (name,))
            assert service.tasks == {}
        assert new.repository.record_incident.call_args_list == (
            old.repository.record_incident.call_args_list
        )
        assert new.review.call_args_list == old.review.call_args_list
        assert new.failures == old.failures == {}
        assert all((clock.current - observed).total_seconds() < 60
                   for observed in new._last_incident.values())
        if age == 60 and name == "new":
            assert str(("hot",)) not in new._last_incident
            assert str(("warm",)) in new._last_incident
    assert new.repository.record_incident.await_count == 6
    for call in new.repository.record_incident.call_args_list:
        assert call.args[1:] == (
            "SPREAD_REVIEW", {"reasons": result.reason_codes}, "open" if blocked else "resolved"
        )


async def test_incident_limit_remains_1000_for_live_entries():
    old, rows = make_pair(LegacyCacheService)
    new, _ = make_pair(MarketValidationService)
    for service in (old, new):
        service.review = AsyncMock(return_value=(service.validate(*rows), *rows))
        for index in range(1001):
            service.tasks[(index,)] = object()
            await service._background_review(*rows, RiskSettings(), (index,))
            assert service.tasks == {}
    assert new._last_incident == old._last_incident
    assert len(new._last_incident) == 1000
    assert str((0,)) not in new._last_incident
    assert new.repository.record_incident.call_args_list == old.repository.record_incident.call_args_list


async def test_expiry_cleanup_preserves_review_eviction_at_capacity(clock):
    rows = [market(), market("bybit", price=0.2)]
    old = make_service(LegacyCacheService, rows)
    new = make_service(MarketValidationService, rows)
    legs = [new.annotate(row) for row in rows]
    result = validate_pair(*legs, review_passed=True)
    key = new.pair_key(*legs)
    for service in (old, new):
        service._bounded_put(service.reviews, key, result)
        for index in range(999):
            a = legs[0].model_copy(update={"raw_symbol": f"PRL{index}USDT"})
            b = legs[1].model_copy(update={"raw_symbol": f"PRL{index}USDT"})
            review = validate_pair(a, b, review_passed=True)
            if index == 0:
                review.expires_at = NOW + timedelta(seconds=1)
            service._bounded_put(service.reviews, service.pair_key(a, b), review)
    clock.current = NOW + timedelta(seconds=2)
    for service in (old, new):
        await service.prepare([], RiskSettings())
        a = legs[0].model_copy(update={"raw_symbol": "NEWUSDT"})
        b = legs[1].model_copy(update={"raw_symbol": "NEWUSDT"})
        service._bounded_put(service.reviews, service.pair_key(a, b), validate_pair(a, b))
    assert new.validate(*legs) == old.validate(*legs)


async def test_expiry_cleanup_preserves_incident_eviction_at_capacity(clock):
    old, rows = make_pair(LegacyCacheService)
    new, _ = make_pair(MarketValidationService)
    for service in (old, new):
        service.review = AsyncMock(return_value=(service.validate(*rows), *rows))
        service._last_incident[str(("hot",))] = NOW + timedelta(seconds=59)
        service._last_incident[str(("expired",))] = NOW
        for index in range(998):
            service._last_incident[str((index,))] = NOW + timedelta(seconds=59)
    clock.current = NOW + timedelta(seconds=60)
    for service in (old, new):
        await service._background_review(*rows, RiskSettings(), ("new",))
        await service._background_review(*rows, RiskSettings(), ("hot",))
    assert new.repository.record_incident.call_args_list == old.repository.record_incident.call_args_list


@pytest.mark.parametrize("mutation", ["refresh", "invalidate"])
async def test_expiry_payload_is_released_and_clear_removes_order_metadata(mutation):
    service, rows = make_pair(MarketValidationService)
    result = service.validate(*rows)
    result.expires_at = NOW
    reference = weakref.ref(result)
    for index in range(1001):
        service._bounded_put(service.reviews, (index,), result)
    del result
    assert await service.prepare([], RiskSettings()) == []
    gc.collect()
    assert reference() is None
    assert not service.reviews and len(service.reviews._order) == 1000
    service.review_attempted["preserve"] = NOW.timestamp()
    service.failures["preserve"] = NOW + timedelta(seconds=30)
    service._last_incident["preserve"] = NOW
    before = service._mutation_epoch
    if mutation == "refresh":
        service.repository.settings = AsyncMock(return_value=service.settings)
        service.repository.list_bindings = AsyncMock(return_value=[binding(row) for row in rows])
        await service.refresh()
    else:
        service.invalidate()
    assert service._mutation_epoch == before + 1
    assert not service.reviews._order
    assert service.review_attempted == {"preserve": NOW.timestamp()}
    assert service.failures == {"preserve": NOW + timedelta(seconds=30)}
    assert service._last_incident == {"preserve": NOW}


async def test_1000_books_deep_size_drops_at_least_eighty_percent():
    rows = [market(raw=f"PRL{i}USDT", price=0.1 + i * 0.000001) for i in range(1001)]
    old = make_service(LegacyCacheService, rows)
    new = make_service(MarketValidationService, rows)
    for row in rows[:1000]:
        assert await new._book(row) == await old._book(row)
    assert len(old.books) == len(new.books) == 1000
    before, before_objects, before_truncated = deep_size(
        old.books, max_objects=1_000_000, deadline=time.monotonic() + 60
    )
    after, after_objects, after_truncated = deep_size(
        new.books, max_objects=1_000_000, deadline=time.monotonic() + 60
    )
    assert not before_truncated and not after_truncated
    print(f"M2_3B_BOOKS before_bytes={before} after_bytes={after} "
          f"reduction_pct={(1 - after / before) * 100:.4f} "
          f"before_objects={before_objects} after_objects={after_objects}")
    assert after <= before * 0.20, (before, after)
    # Preserve the existing bounded insertion/eviction policy, including refresh.
    for service in (old, new):
        await service._book(rows[0])
        await service._book(rows[1000])
    assert list(new.books) == list(old.books)
    assert len(new.books) == 1000
    assert MarketKey.from_snapshot(rows[0]).key in new.books
    assert MarketKey.from_snapshot(rows[1]).key not in new.books
