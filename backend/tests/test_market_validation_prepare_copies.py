"""M1-C: frozen f794324 reference, offline equivalence and allocation gates."""

import asyncio
import gc
import inspect
import tracemalloc
from datetime import UTC, datetime, timedelta
from itertools import combinations
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_market_identity_validation import NOW, binding, market

from app.core import config
from app.db.database import connect_database
from app.db.market_identity_repository import (
    MarketIdentityRepository,
    initialize_market_identity_schema,
)
from app.models.market import MarketSnapshot
from app.models.market_identity import MarketKey, utc
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.models.settings import RiskSettings
from app.services import market_identity_catalog, market_validation_review
from app.services.market_identity import MarketIdentityRegistry
from app.services.market_identity_catalog import OfficialMarketIdentityCatalog
from app.services.market_validation_review import MarketValidationService


class LegacyValidationService(MarketValidationService):
    # Frozen verbatim from f794324; do not update with the production implementation.
    def annotate(self, row, now=None):
        now = utc(now or datetime.now(UTC))
        result = self._apply_fx(self.registry.annotate(row, now), now)
        return result.model_copy(
            update={"identity_collision": row.symbol in self.collision_symbols}
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
            self._cached_book(self.annotate(row, now), now, risk.stale_after_seconds)
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
            row.model_copy(update={"identity_collision": row.symbol in self.collision_symbols})
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


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(market_validation_review, "datetime", FrozenDateTime)
    # The frozen reference uses this module's global datetime.
    monkeypatch.setitem(globals(), "datetime", FrozenDateTime)


def make_service(cls, scenario="plain", mode="shadow"):
    rows = [market(), market("hyperliquid", "io:PRL", dex="io")]
    if scenario == "cleared_collision":
        rows = [r.model_copy(update={"identity_collision": True}) for r in rows]
    if scenario == "reference":
        rows[1] = rows[1].model_copy(update={"quote_kind": "reference"})
    if scenario == "invalid_key":
        rows[1] = rows[1].model_copy(update={"dex": "xyz"})
    bindings = [binding(rows[0])]
    if scenario not in {"invalid_key", "unbound"}:
        bindings.append(binding(rows[1], "pearl" if scenario == "new_collision" else "perle"))
    if scenario == "kind_collision":
        bindings[1] = bindings[1].model_copy(update={"asset_kind": "equity"})
    if scenario.startswith("fx"):
        bindings[1] = bindings[1].model_copy(update={"quote_currency": "USDC"})
    repo = SimpleNamespace(discover=AsyncMock(), invalidate_opportunities=AsyncMock())
    service = cls(repo, None)
    service.settings = service.settings.model_copy(update={"mode": mode})
    service.registry = MarketIdentityRegistry(bindings, mode=mode)
    service._refresh_fx = AsyncMock()
    service._background_review = AsyncMock()
    if scenario == "cleared_collision":
        service.collision_symbols = {"PRLUSDT"}
    if scenario in {"fx", "fx_cached"}:
        service.fx = (0.999, 1.001, NOW, NOW + timedelta(seconds=60))
    if scenario in {"cached", "stale_cached", "fx_cached"}:
        row = service.annotate(rows[1], NOW)
        book = OrderBookSnapshot(
            exchange=row.exchange, symbol=row.symbol, market_type=row.market_type,
            raw_symbol=row.raw_symbol,
            bids=[OrderBookLevel(price=0.101, size=50)],
            asks=[OrderBookLevel(price=0.102, size=60)],
            timestamp=NOW - timedelta(seconds=1000 if scenario == "stale_cached" else 1),
        )
        service.books[MarketKey.from_snapshot(row).key] = (
            book, row.binding_revision, row.binding_fingerprint,
            row.quote_conversion_bid, row.quote_conversion_ask,
        )
    return service, rows


def side_effects(service):
    return {
        "collisions": service.collision_symbols,
        "discovered": service.discovered,
        "discover_calls": [
            [row.model_dump() for row in call.args[0]]
            for call in service.repository.discover.call_args_list
        ],
        "invalidated": service.invalidated_ids,
        "invalidate_calls": service.repository.invalidate_opportunities.call_args_list,
        "review_attempted": service.review_attempted,
        "task_keys": set(service.tasks),
        "review_calls": service._background_review.call_args_list,
        "fx_calls": service._refresh_fx.call_count,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["shadow", "enforce"])
@pytest.mark.parametrize("scenario", [
    "plain", "new_collision", "cleared_collision", "kind_collision", "cached",
    "stale_cached", "fx", "fx_cached", "fx_refresh", "invalid_key", "unbound", "reference",
])
async def test_prepare_matches_frozen_reference(scenario, mode):
    old, rows = make_service(LegacyValidationService, scenario, mode)
    new, _ = make_service(MarketValidationService, scenario, mode)
    original = [row.model_dump() for row in rows]
    for _ in range(2):  # Also check discovery/invalidation deduplication across rounds.
        expected = await old.prepare(rows, RiskSettings())
        actual = await new.prepare(rows, RiskSettings())
        await asyncio.sleep(0)  # Only the fake FX/review workers can run.
        assert [row.model_dump() for row in actual] == [row.model_dump() for row in expected]
        assert side_effects(new) == side_effects(old)
        assert [row.model_dump() for row in rows] == original
    if scenario == "new_collision":
        assert new.collision_symbols == {"PRLUSDT"}
        assert new.invalidated_ids
        assert new.repository.invalidate_opportunities.call_count == 1
    if scenario == "cleared_collision":
        assert not new.collision_symbols
        assert not any(row.identity_collision for row in actual)
    if scenario in {"cached", "fx_cached"}:
        assert actual[1].bid_size == 50 and "exact l2Book" in actual[1].data_source
    if scenario in {"fx", "fx_cached"}:
        assert actual[1].quote_conversion_bid == 0.999
    if scenario == "reference":
        assert new._background_review.call_count > 0
    if scenario == "fx_refresh":
        assert new._refresh_fx.call_count > 0


@pytest.mark.parametrize("collision", [False, True])
@pytest.mark.parametrize("now", [None, NOW, NOW.replace(tzinfo=None)])
def test_public_annotate_keeps_original_behavior(collision, now):
    old, rows = make_service(LegacyValidationService, "fx")
    new, _ = make_service(MarketValidationService, "fx")
    old.collision_symbols = new.collision_symbols = {"PRLUSDT"} if collision else set()
    for row in rows:
        assert new.annotate(row, now).model_dump() == old.annotate(row, now).model_dump()


def test_prepare_pairing_and_scheduling_source_is_unchanged():
    marker = "        if self.collision_symbols:\n"
    assert inspect.getsource(MarketValidationService.prepare).split(marker, 1)[1] == (
        inspect.getsource(LegacyValidationService.prepare).split(marker, 1)[1]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["plain", "cached", "fx", "fx_cached"])
async def test_noncollision_prepare_saves_two_copies_per_row(monkeypatch, scenario):
    old, rows = make_service(LegacyValidationService, scenario)
    new, _ = make_service(MarketValidationService, scenario)
    copy = MarketSnapshot.model_copy
    counts = []

    def counted(row, *args, **kwargs):
        counts.append(MarketKey.from_snapshot(row).key)
        return copy(row, *args, **kwargs)

    monkeypatch.setattr(MarketSnapshot, "model_copy", counted)
    await old.prepare(rows, RiskSettings())
    before = counts[:]
    counts.clear()
    await new.prepare(rows, RiskSettings())
    for row in rows:
        key = MarketKey.from_snapshot(row).key
        assert before.count(key) - counts.count(key) == 2


@pytest.mark.asyncio
async def test_prepare_10000_markets_peak_below_seventy_percent():
    rows = [market(raw=f"TOKEN{i}USDT").model_copy(
        update={"symbol": f"TOKEN{i}USDT", "base": f"TOKEN{i}"}
    ) for i in range(10_000)]
    keys = {MarketKey.from_snapshot(row).key for row in rows}
    risk = RiskSettings()

    async def peak(cls):
        service = cls(None, None)
        service.discovered = keys.copy()  # Steady-state prepare; no DB/mock allocation noise.
        gc.collect()
        tracemalloc.start()
        try:
            result = await service.prepare(rows, risk)
            _, allocated = tracemalloc.get_traced_memory()
            assert len(result) == 10_000
            return allocated
        finally:
            tracemalloc.stop()

    before = await peak(LegacyValidationService)
    after = await peak(MarketValidationService)
    print(f"M1C_PEAK before_bytes={before} after_bytes={after} ratio={after / before:.6f}")
    assert after < before * 0.70, (before, after)


@pytest.mark.asyncio
async def test_catalog_default_delay_can_stop_without_refresh(monkeypatch):
    catalog = OfficialMarketIdentityCatalog(None)
    refresh = AsyncMock()
    monkeypatch.setattr(catalog, "refresh", refresh)
    assert inspect.signature(catalog.run).parameters["startup_delay_seconds"].default == 300
    stop = asyncio.Event()
    task = asyncio.create_task(catalog.run(stop))
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        refresh.assert_not_called()
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        refresh.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_catalog_zero_delay_refreshes_immediately(monkeypatch):
    stop = asyncio.Event()
    catalog = OfficialMarketIdentityCatalog(None)
    refresh = AsyncMock(side_effect=stop.set)
    monkeypatch.setattr(catalog, "refresh", refresh)
    await asyncio.wait_for(catalog.run(stop, startup_delay_seconds=0), timeout=1)
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_catalog_delay_then_daily_interval(monkeypatch):
    stop = asyncio.Event()
    catalog = OfficialMarketIdentityCatalog(None)
    refresh = AsyncMock()
    monkeypatch.setattr(catalog, "refresh", refresh)
    timeouts = []

    async def wait_for(awaitable, timeout):
        awaitable.close()
        timeouts.append(timeout)
        if len(timeouts) == 1:
            refresh.assert_not_called()
            raise TimeoutError
        if len(timeouts) == 2:
            refresh.assert_awaited_once()
            raise TimeoutError
        assert refresh.await_count == 2
        stop.set()

    monkeypatch.setattr(market_identity_catalog, "asyncio", SimpleNamespace(wait_for=wait_for))
    await catalog.run(stop, startup_delay_seconds=300)
    assert timeouts == [300, 86400, 86400]


@pytest.mark.asyncio
async def test_database_bindings_available_throughout_catalog_delay(tmp_path, monkeypatch):
    path = str(tmp_path / "persisted-identities.db")
    row = market("binance", "LOCALTESTUSDT")
    db = await connect_database(path)
    try:
        await initialize_market_identity_schema(db)
        await MarketIdentityRepository(db).import_catalog([binding(row, "offline.persisted")])
    finally:
        await db.close()
    db = await connect_database(path)
    try:
        service = MarketValidationService(MarketIdentityRepository(db), None)
        await service.initialize(seed_reviewed=True)
        before = service.annotate(row, NOW).model_dump()
        assert before["asset_id"] == "offline.persisted" and before["identity_status"] == "verified"
        catalog = OfficialMarketIdentityCatalog(service)
        stop = asyncio.Event()
        refresh = AsyncMock(side_effect=stop.set)
        monkeypatch.setattr(catalog, "refresh", refresh)
        waits = []

        async def wait_for(awaitable, timeout):
            awaitable.close()
            waits.append(timeout)
            assert service.annotate(row, NOW).model_dump() == before
            if timeout == 300:
                refresh.assert_not_called()
                raise TimeoutError

        monkeypatch.setattr(market_identity_catalog, "asyncio", SimpleNamespace(wait_for=wait_for))
        await catalog.run(stop)
        assert waits == [300, 86400]
        refresh.assert_awaited_once()
    finally:
        await db.close()


@pytest.mark.parametrize("value, expected", [(None, 300), ("0", 0), ("12.5", 12.5)])
def test_catalog_startup_delay_configuration(monkeypatch, value, expected):
    monkeypatch.setattr(config, "find_dotenv", lambda **kwargs: "")
    name = "MARKET_IDENTITY_CATALOG_STARTUP_DELAY_SECONDS"
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    assert config.Settings().market_identity_catalog_startup_delay_seconds == 300
    assert config.get_settings.__wrapped__().market_identity_catalog_startup_delay_seconds == expected


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_catalog_startup_delay_rejects_invalid_configuration(value):
    with pytest.raises(ValueError, match="market_identity_catalog_startup_delay_seconds"):
        config.Settings(market_identity_catalog_startup_delay_seconds=value)
