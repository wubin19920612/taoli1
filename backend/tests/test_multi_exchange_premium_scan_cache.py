"""Offline equivalence oracle frozen from 3b697ab, plus cache acceptance tests."""

import asyncio
import logging
import random
import weakref
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from time import monotonic, perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.models.alert as alert_model
import app.services.multi_exchange_premium as premium
from app.models.alert import AlertEvent
from app.models.market import MarketSnapshot, MarketType
from app.models.market_identity import MarketKey, utc
from app.models.premium_index import SUPPORTED_PREMIUM_INDEX_EXCHANGES, PremiumIndexPoint
from app.models.settings import RiskSettings
from app.services.data_filters import filter_markets
from app.services.multi_exchange_premium import (
    BATCH_SIZE,
    MultiExchangePremiumMonitor,
    build_premium_alert_message,
    matching_points,
)

logger = logging.getLogger(__name__)


def legacy_eligible_groups(markets: list[MarketSnapshot], settings: RiskSettings, now: datetime):
    groups = defaultdict(dict)
    for market in filter_markets(markets, settings):
        if (
            market.market_type != MarketType.FUTURE
            or market.exchange not in SUPPORTED_PREMIUM_INDEX_EXCHANGES
            or market.identity_status != "verified"
            or market.unit_status != "verified"
            or not market.asset_id
            or not market.binding_valid_until
            or utc(market.binding_valid_until) <= now
        ):
            continue
        try:
            key = MarketKey.from_snapshot(market).key
        except ValueError:
            continue
        # Valuation contracts with different tracking definitions are distinct instruments.
        group = (
            market.asset_id,
            market.asset_kind or "",
            market.contract_kind,
            market.tracking_definition if market.contract_kind == "valuation" else "",
        )
        groups[group][key] = market
    return {
        key: list(rows.values())
        for key, rows in groups.items()
        if len({row.exchange for row in rows.values()}) >= 2
    }


def legacy_by_market(current):
    return {
        MarketKey.from_snapshot(market).key: (key, market)
        for key, markets in current.items()
        for market in markets
    }


class LegacyMonitor(MultiExchangePremiumMonitor):
    async def run_once(self):
        repo = self.app.state.settings_repo
        settings = await repo.get_risk_settings()
        if not settings.multi_exchange_premium_alert_enabled:
            return
        now = datetime.now(UTC)
        groups = legacy_eligible_groups(self.app.state.snapshot_store.get_markets(), settings, now)
        keys = sorted(groups)
        self.status["eligible_symbols"] = len(keys)
        remaining = [key for key in keys if self._cursor is None or key > self._cursor]
        if not remaining:
            self.status["last_sweep_seconds"] = round(monotonic() - self._sweep_started, 1)
            self._sweep_started = monotonic()
            self._cursor = None
            remaining = keys
        selected = remaining[:BATCH_SIZE]
        self.status["last_error_count"] = 0
        self.status["last_official_points"] = 0
        self.status["last_fallback_points"] = 0

        async def sample_group(key):
            results = await asyncio.gather(*(self._point(market) for market in groups[key]))
            await self._evaluate_results(results)

        # Evaluate each asset as soon as its legs finish; a slow unrelated market
        # must not age otherwise usable observations out of the freshness window.
        async with asyncio.TaskGroup() as tasks:
            for key in selected:
                tasks.create_task(sample_group(key))
        if selected:
            self._cursor = selected[-1]
        now = datetime.now(UTC)
        self._next_allowed = {
            key: until for key, until in self._next_allowed.items() if until > now
        }
        self.status.update(last_batch_symbols=len(selected), last_batch_at=now.isoformat())

    async def _evaluate_results(self, results):
        # A setting, blacklist or identity may have changed during network I/O.
        settings = await self.app.state.settings_repo.get_risk_settings()
        if not settings.multi_exchange_premium_alert_enabled:
            return
        now = datetime.now(UTC)
        current = legacy_eligible_groups(self.app.state.snapshot_store.get_markets(), settings, now)
        by_market = legacy_by_market(current)
        observations = defaultdict(list)
        for result in results:
            if result is None:
                continue
            market, point = result
            live = by_market.get(MarketKey.from_snapshot(market).key)
            if live is None:
                continue
            key, latest = live
            if (latest.asset_id, latest.binding_revision, latest.binding_fingerprint) != (
                market.asset_id,
                market.binding_revision,
                market.binding_fingerprint,
            ):
                continue
            observations[key].append((latest, point))
        for key, rows in observations.items():
            hits = matching_points(rows, settings, now)
            if not hits or now < self._next_allowed.get(key, now):
                continue
            collector = getattr(self.app.state, "market_collector", None)
            message = build_premium_alert_message(
                hits,
                settings,
                now=now,
                fee_settings=getattr(collector, "fee_settings", None),
            )
            status = "sent"
            try:
                await self.app.state.feishu_notifier.send_text(message)
            except Exception:
                status = "failed"
                logger.exception("multi-exchange premium notification failed")
            self._next_allowed[key] = now + timedelta(
                seconds=settings.multi_exchange_premium_cooldown_seconds
                if status == "sent"
                else 60,
            )
            await self.app.state.alert_event_repo.create(
                AlertEvent(
                    rule_id="multi-exchange-premium",
                    opportunity_id="premium:" + ":".join(key),
                    symbol=hits[0][0].symbol,
                    status=status,
                    message=message,
                    created_at=now,
                )
            )


START = datetime(2026, 10, 29, 5, tzinfo=UTC)
ORDER = [15, 0, 14, 1, 13, 2, 12, 3, 11, 4, 10, 5, 9, 6, 8, 7]


class Clock(datetime):
    value = START

    @classmethod
    def now(cls, tz=None):
        return cls.value if tz else cls.value.replace(tzinfo=None)


@pytest.fixture
def clock(monkeypatch):
    Clock.value = START
    monkeypatch.setattr(premium, "datetime", Clock)
    monkeypatch.setattr(__name__ + ".datetime", Clock)
    monkeypatch.setattr(premium, "monotonic", lambda: 100.0)
    monkeypatch.setattr(__name__ + ".monotonic", lambda: 100.0)
    # Compare every AlertEvent field, with UUID entropy removed from both sides.
    monkeypatch.setattr(alert_model, "uuid4", lambda: SimpleNamespace(hex="test-event"))
    return Clock


def make_rows(count=16):
    return [
        MarketSnapshot(
            symbol=f"COIN{group:04}USDT", base=f"COIN{group:04}",
            raw_symbol=f"COIN{group:04}USDT", asset_id=f"asset{group:04}",
            exchange=exchange, market_type=MarketType.FUTURE,
            bid=100 + leg, ask=102 + leg, timestamp=START,
            identity_status="verified", unit_status="verified",
            binding_valid_until=START + timedelta(hours=1), binding_revision=1,
            binding_fingerprint="original",
        )
        for group in range(count)
        for leg, exchange in enumerate(("binance", "gate", "bybit"))
    ]


class FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.generation = 1

    def get_markets(self):
        return list(self.rows)

    def publish(self, rows):
        self.rows = rows
        self.generation += 1


def make_monitor(kind=MultiExchangePremiumMonitor, rows=None):
    settings = RiskSettings()
    state = SimpleNamespace(
        snapshot_store=FakeStore(make_rows() if rows is None else rows),
        settings_repo=SimpleNamespace(get_risk_settings=AsyncMock(return_value=settings)),
        feishu_notifier=SimpleNamespace(send_text=AsyncMock()),
        alert_event_repo=SimpleNamespace(create=AsyncMock()),
    )
    monitor = kind(SimpleNamespace(state=state), service=SimpleNamespace())
    return monitor, state, settings


def observed(monitor):
    state = monitor.app.state
    return (
        state.feishu_notifier.send_text.await_args_list,
        [call.args[0].model_dump() for call in state.alert_event_repo.create.await_args_list],
        dict(monitor._next_allowed), dict(monitor.status), monitor._cursor,
        state.settings_repo.get_risk_settings.await_count,
    )


async def start_batch(monitor):
    gates = {f"asset{i:04}": asyncio.Event() for i in range(16)}
    done = {key: asyncio.Event() for key in gates}
    ready = asyncio.Event()
    started = 0
    evaluate = monitor._evaluate_results

    async def fake_point(market):
        nonlocal started
        started += 1
        if started == 48:
            ready.set()
        await gates[market.asset_id].wait()
        return market, PremiumIndexPoint(
            premium_pct=3, bucket_at=Clock.value, source="binance_premium_index",
        )

    async def evaluate_and_signal(results):
        await evaluate(results)
        done[results[0][0].asset_id].set()

    monitor._point = fake_point
    monitor._evaluate_results = evaluate_and_signal
    task = asyncio.create_task(monitor.run_once())
    await asyncio.wait_for(ready.wait(), timeout=5)
    return task, gates, done


def change_during_wait(step, state, settings):
    asset = f"asset{ORDER[step]:04}"
    store = state.snapshot_store

    def publish_target(**updates):
        store.publish([
            row.model_copy(update=updates) if row.asset_id == asset else row
            for row in store.rows
        ])

    if step == 1:
        store.publish([row.model_copy(update={"bid": 105, "ask": 107}) for row in store.rows])
    elif step == 2:
        settings.excluded_symbols.append(f"COIN{ORDER[step]:04}USDT")
    elif step == 3:
        settings.excluded_symbols.clear()
        settings.ignored_exchanges.extend(["binance", "gate"])
    elif step == 4:
        settings.ignored_exchanges.clear()
        publish_target(binding_revision=2)
    elif step == 5:
        publish_target(binding_fingerprint="changed")
    elif step == 6:
        publish_target(asset_id="rebound-asset")
    elif step == 7:
        settings.multi_exchange_premium_alert_enabled = False
    elif step == 8:
        settings.multi_exchange_premium_alert_enabled = True
        settings.multi_exchange_premium_threshold_pct = 4
    elif step == 9:
        settings.multi_exchange_premium_threshold_pct = 2
    elif step == 11:
        state.feishu_notifier.send_text.side_effect = RuntimeError("offline fake failure")
    elif step == 12:
        state.feishu_notifier.send_text.side_effect = None
    elif step == 13:
        settings.multi_exchange_premium_cooldown_seconds = 17
    elif step == 14:
        publish_target(identity_status="pending")


async def test_staggered_equivalence_with_live_changes(clock):
    rows = make_rows()
    # Step 9 sets a threshold; step 10 crosses expiry with identical cache identity.
    rows = [
        row.model_copy(update={"binding_valid_until": START + timedelta(seconds=10)})
        if row.asset_id == f"asset{ORDER[10]:04}" and row.exchange != "binance" else row
        for row in rows
    ]
    new, new_state, new_settings = make_monitor(rows=rows)
    old, old_state, old_settings = make_monitor(LegacyMonitor, rows=rows)
    for monitor in (new, old):
        monitor._next_allowed[(f"asset{ORDER[12]:04}", "", "linear", "")] = (
            START + timedelta(seconds=20)
        )
    batches = await asyncio.gather(start_batch(new), start_batch(old))
    try:
        for step, group in enumerate(ORDER):
            clock.value = START + timedelta(seconds=step)
            change_during_wait(step, new_state, new_settings)
            change_during_wait(step, old_state, old_settings)
            for _, gates, _ in batches:
                gates[f"asset{group:04}"].set()
            await asyncio.wait_for(asyncio.gather(*(
                done[f"asset{group:04}"].wait() for _, _, done in batches
            )), timeout=5)
            assert observed(new) == observed(old), f"completion step {step}"
        await asyncio.gather(*(task for task, _, _ in batches))
        assert observed(new) == observed(old)
        events = [call.args[0] for call in new_state.alert_event_repo.create.await_args_list]
        assert [event.symbol for event in events] == [
            f"COIN{ORDER[i]:04}USDT" for i in (0, 1, 9, 11, 13, 15)
        ]
        assert [event.status for event in events] == ["sent"] * 3 + ["failed", "sent", "sent"]
        assert new_state.settings_repo.get_risk_settings.await_count == 17
    finally:
        for task, _, _ in batches:
            task.cancel()
        await asyncio.gather(*(task for task, _, _ in batches), return_exceptions=True)


async def finish_batch(monitor):
    original_point = monitor._point
    original_evaluate = monitor._evaluate_results
    task, gates, done = await start_batch(monitor)
    try:
        for group in ORDER:
            key = f"asset{group:04}"
            gates[key].set()
            await asyncio.wait_for(done[key].wait(), timeout=5)
        await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        monitor._point = original_point
        monitor._evaluate_results = original_evaluate


async def test_one_scan_per_generation_and_settings_value(clock, monkeypatch):
    monitor, state, settings = make_monitor()
    calls = []

    def counted(markets, risk):
        calls.append(state.snapshot_store.generation)
        return filter_markets(markets, risk)

    monkeypatch.setattr(premium, "filter_markets", counted)
    await finish_batch(monitor)
    assert calls == [1]
    # Equal values in a new settings object must hit the same cache.
    state.settings_repo.get_risk_settings.return_value = settings.model_copy(deep=True)
    await finish_batch(monitor)
    assert calls == [1]
    state.snapshot_store.publish(list(state.snapshot_store.rows))
    await finish_batch(monitor)
    assert calls == [1, 2]
    settings = state.settings_repo.get_risk_settings.return_value
    settings.multi_exchange_premium_threshold_pct = 4
    await finish_batch(monitor)
    assert calls == [1, 2, 2]
    settings.multi_exchange_premium_threshold_pct = 2
    await finish_batch(monitor)
    assert calls == [1, 2, 2, 2]  # Only the latest value is retained.
    print("SCAN_COUNTS: first_batch=1 same_values=0 generation_change=1 settings_change=1 return_to_old_settings=1")


async def test_expiry_boundary_rechecks_without_scanning(clock, monkeypatch):
    rows = make_rows(1)[:2]
    expiry = START + timedelta(seconds=1)
    rows[1] = rows[1].model_copy(update={"binding_valid_until": expiry})
    monitor, state, _ = make_monitor(rows=rows)
    monitor._point = AsyncMock(side_effect=lambda row: (
        row, PremiumIndexPoint(premium_pct=3, bucket_at=clock.value, source="official")
    ))
    calls = []

    def counted(markets, settings):
        calls.append(1)
        return filter_markets(markets, settings)

    monkeypatch.setattr(premium, "filter_markets", counted)
    for now, eligible in ((START, 1), (expiry, 0), (expiry + timedelta(seconds=1), 0)):
        clock.value = now
        await monitor.run_once()
        assert monitor.status["eligible_symbols"] == eligible
        assert premium.eligible_groups(rows, RiskSettings(), now) == legacy_eligible_groups(
            rows, RiskSettings(), now
        )
        calls.pop()  # Public eligible_groups is deliberately uncached.
    assert len(calls) == 1
    assert state.feishu_notifier.send_text.await_count == 1


def test_group_order_duplicates_expiry_and_identity(clock):
    rows = make_rows(3)
    rows += [rows[0].model_copy(update={"bid": 95, "binding_valid_until": START})]
    rows.insert(0, rows[3].model_copy(update={"binding_valid_until": START}))
    rows += [rows[1].model_copy(update={"asset_id": "asset0001"})]
    rows += [rows[2].model_copy(update={"exchange": "hyperliquid", "dex": "io",
                                         "raw_symbol": "xyz:COIN0000"})]
    for seconds in (-1, 0, 3600):
        now = START + timedelta(seconds=seconds)
        expected = legacy_eligible_groups(rows, RiskSettings(), now)
        actual = premium.eligible_groups(rows, RiskSettings(), now)
        assert list(actual.items()) == list(expected.items())


@pytest.mark.parametrize("seed", range(8))
def test_cached_projection_matches_legacy_with_duplicates_and_time_reversal(seed, clock):
    rng = random.Random(seed)
    rows = make_rows(5)
    for _ in range(30):
        row = rng.choice(rows)
        rows.insert(rng.randrange(len(rows)), row.model_copy(update={
            "asset_id": f"asset{rng.randrange(5):04}",
            "binding_valid_until": START + timedelta(seconds=rng.randrange(-2, 3)),
            "bid": rng.randrange(90, 100),
        }))
    monitor, _, settings = make_monitor(rows=rows)
    for seconds in (0, 1, -2, 2, 3600, -1):
        now = START + timedelta(seconds=seconds)
        expected = legacy_eligible_groups(rows, settings, now)
        current, index = monitor._current_eligibility(settings, now)
        assert list(current.items()) == list(expected.items())
        active_index = {
            key: (group, row) for key, (group, row) in index.items()
            if group in current and utc(row.binding_valid_until) > now
        }
        assert active_index == legacy_by_market(expected)


async def test_cache_hit_does_not_rebuild_market_keys_and_releases_old_scan(clock, monkeypatch):
    monitor, state, settings = make_monitor()
    monitor._current_eligibility(settings, START)
    previous = weakref.ref(monitor._scan_cache[1])
    original = MarketKey.from_snapshot
    calls = []

    def counted(row):
        calls.append(row)
        return original(row)

    monkeypatch.setattr(MarketKey, "from_snapshot", counted)
    await monitor._evaluate_results([])
    assert not calls
    state.snapshot_store.publish(make_rows())
    await monitor._evaluate_results([])
    assert len(calls) == 48
    assert previous() is None
    previous = weakref.ref(monitor._scan_cache[1])
    settings.ignored_exchanges.append("gate")
    await monitor._evaluate_results([])
    assert len(calls) == 48 + 32
    assert previous() is None
    assert state.settings_repo.get_risk_settings.await_count == 3


@pytest.mark.parametrize("updates", [
    {"binding_revision": 2}, {"binding_fingerprint": "revoked"},
    {"asset_id": "rebound-asset"}, {"identity_status": "pending"},
])
async def test_binding_replacement_without_generation_matches_legacy(updates, clock):
    rows = make_rows(1)
    new, new_state, settings = make_monitor(rows=rows)
    old, old_state, _ = make_monitor(LegacyMonitor, rows=rows)
    new._current_eligibility(settings, START)
    replacement = [row.model_copy(update=updates) for row in rows]
    # routes_market_identities._refresh_store replaces rows without mark_published.
    new_state.snapshot_store.rows = replacement
    old_state.snapshot_store.rows = replacement
    results = [(row, PremiumIndexPoint(
        premium_pct=3, bucket_at=START, source="official",
    )) for row in rows]
    await new._evaluate_results(results)
    await old._evaluate_results(results)
    assert observed(new) == observed(old)
    new_state.feishu_notifier.send_text.assert_not_awaited()


def test_input_copy_identity_order_length_and_release(clock, monkeypatch):
    monitor, state, settings = make_monitor(rows=make_rows(2))
    copies = []

    def get_markets():
        result = list(state.snapshot_store.rows)
        copies.append(result)
        return result

    monkeypatch.setattr(state.snapshot_store, "get_markets", get_markets)
    monitor._current_eligibility(settings, START)
    assert monitor._scan_cache[2] is copies[-1]
    first_scan = weakref.ref(monitor._scan_cache[1])
    monitor._current_eligibility(settings, START)
    assert monitor._scan_cache[1] is first_scan()
    assert monitor._scan_cache[2] is copies[0]
    assert copies[0] is not copies[1]
    # Equal-valued replacement, order, removal and append each invalidate.
    replacements = [
        [row.model_copy() for row in state.snapshot_store.rows],
        list(reversed(state.snapshot_store.rows)),
        state.snapshot_store.rows[:-1],
        state.snapshot_store.rows + make_rows(3)[6:],
    ]
    for rows in replacements:
        previous = weakref.ref(monitor._scan_cache[1])
        state.snapshot_store.rows = rows
        actual, _ = monitor._current_eligibility(settings, START)
        assert previous() is None
        assert monitor._scan_cache[2] is copies[-1]
        assert list(actual.items()) == list(legacy_eligible_groups(rows, settings, START).items())
        assert state.snapshot_store.generation == 1


async def test_expired_leg_is_excluded_while_group_remains_eligible(clock):
    rows = make_rows(1)
    rows[0] = rows[0].model_copy(update={"binding_valid_until": START + timedelta(seconds=1)})
    new, new_state, settings = make_monitor(rows=rows)
    old, _, _ = make_monitor(LegacyMonitor, rows=rows)
    new._current_eligibility(settings, START)
    clock.value = START + timedelta(seconds=1)
    results = [(row, PremiumIndexPoint(
        premium_pct=3, bucket_at=clock.value, source="official",
    )) for row in rows]
    await new._evaluate_results(results)
    await old._evaluate_results(results)
    assert observed(new) == observed(old)
    message = new_state.feishu_notifier.send_text.await_args.args[0]
    assert "binance:" not in message
    assert "gate:" in message and "bybit:" in message


@pytest.mark.benchmark
async def test_microbenchmark_12400_rows_16_completions(clock, monkeypatch):
    rows = make_rows(4134)[:12400]
    measurements = {}
    identity_checks = 0
    identity_elapsed = 0.0
    original_identity = premium._same_market_rows

    def timed_identity(old, new):
        nonlocal identity_checks, identity_elapsed
        start = perf_counter()
        try:
            return original_identity(old, new)
        finally:
            identity_elapsed += perf_counter() - start
            identity_checks += 1

    for label, kind in (("legacy", LegacyMonitor), ("cached", MultiExchangePremiumMonitor)):
        monitor, _, _ = make_monitor(kind, rows=rows)
        counts = 0
        elapsed = 0.0
        original_filter = filter_markets

        def counted(markets, settings, original_filter=original_filter):
            nonlocal counts
            counts += 1
            return original_filter(markets, settings)

        def timer(function):
            def timed(*args, **kwargs):
                nonlocal elapsed
                start = perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    elapsed += perf_counter() - start
            return timed

        with monkeypatch.context() as patch:
            if label == "legacy":
                patch.setitem(globals(), "legacy_eligible_groups", timer(legacy_eligible_groups))
                patch.setitem(globals(), "legacy_by_market", timer(legacy_by_market))
            else:
                patch.setattr(monitor, "_current_eligibility", timer(monitor._current_eligibility))
                patch.setattr(premium, "_same_market_rows", timed_identity)
            patch.setattr(premium, "filter_markets", counted)
            patch.setitem(globals(), "filter_markets", counted)
            await finish_batch(monitor)
        measurements[label] = counts, elapsed
    assert measurements["legacy"][0] == 17
    assert measurements["cached"][0] == 1
    assert identity_checks == 16
    print(
        f"MICROBENCH rows=12400 groups=16: legacy_scans=17 cached_scans=1 "
        f"legacy_scan_seconds={measurements['legacy'][1]:.6f} "
        f"cached_scan_seconds={measurements['cached'][1]:.6f}"
    )
    print(
        f"IDENTITY_CHECK rows=12400 checks={identity_checks}: "
        f"total_seconds={identity_elapsed:.6f} "
        f"per_check_seconds={identity_elapsed / identity_checks:.6f}"
    )
