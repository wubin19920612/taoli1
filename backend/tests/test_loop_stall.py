import asyncio
import inspect
from collections import Counter
from datetime import timedelta
from unittest.mock import Mock

import pytest
import loop_stall_reference as legacy
from loop_stall_fixture import Clock, NOW, dump, prepared, service, workload

from app.models.settings import RiskSettings
from app.services import compact_markets, market_validation_review as review, pair_validation
from app.services import multi_exchange_premium as premium
from app.services.collector import MarketCollector
from app.services.market_validation_review import MarketValidationService


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(Clock, "current", NOW)
    for module in (legacy, review, pair_validation):
        monkeypatch.setattr(module, "datetime", Clock)


@pytest.fixture(scope="module")
def large():
    return workload()


class CountingBudget:
    def __init__(self, callback=None):
        self.calls = Counter()
        self.callback = callback

    async def maybe_yield(self):
        frame = inspect.currentframe().f_back
        self.calls[frame.f_code.co_name] += 1
        if self.callback:
            self.callback(frame)
        await asyncio.sleep(0)


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
async def test_large_validation_and_filter_match_frozen_00dfa9e(large, mode):
    old, old_rows, opportunities = await prepared(legacy.LegacyValidation, large, mode)
    new, rows, _ = await prepared(MarketValidationService, large, mode)
    assert len(rows) == 12000 and len(opportunities) == 4000
    assert dump(rows) == dump(old_rows)
    assert any(r.identity_collision for r in rows)
    assert any(r.identity_status == "expired" for r in rows)
    assert {"received_batch", "ws"} <= {r.quote_time_source for r in rows}
    assert all(r.quote_kind == "reference" and {"bid", "ask"} <= set(r.estimated_fields)
               for r in rows if r.quote_time_source == "received_batch")
    assert any(r.quote_conversion_source for r in rows)
    risk = RiskSettings()
    for i, item in enumerate(opportunities):
        a, b = rows[3*i:3*i+2]
        expected = old.validate(*old_rows[3*i:3*i+2], risk)
        # Exercise both cache hits and misses, including cached blockers.
        if i % 4 == 0:
            old.reviews[old.pair_key(a, b)] = expected
            new.reviews[new.pair_key(a, b)] = expected
            expected = old.validate(*old_rows[3*i:3*i+2], risk)
        assert dump([new.validate(a, b, risk, annotated=True)]) == dump([expected])
    expected = old.filter_opportunities(opportunities, risk)
    assert dump(new.filter_opportunities(opportunities, risk)) == dump(expected)
    budget = CountingBudget()
    assert dump(await new.filter_opportunities_async(opportunities, risk, budget)) == dump(expected)
    assert sum(budget.calls.values()) >= 4000
    assert new.last_filter_recomputed == 4000


async def test_annotated_guard_epoch_row_provenance_fx_and_expiry(monkeypatch):
    new, rows, _ = await prepared(MarketValidationService, workload(2))
    a, b = rows[3:5]
    annotate = Mock(wraps=new.annotate)
    monkeypatch.setattr(new, "annotate", annotate)
    new.validate(a, b, annotated=True)
    assert annotate.call_count == 0
    new.validate(a.model_copy(), b, annotated=True)
    assert annotate.call_count == 2
    annotate.reset_mock()
    new.invalidate()
    assert dump([new.validate(a, b, annotated=True)]) == dump([new.validate(a, b)])
    assert annotate.call_count == 4
    new._prepared_epoch = new._mutation_epoch
    new.fx = (0.998, 1.002, NOW, NOW + timedelta(seconds=60))
    annotate.reset_mock()
    new.validate(a, b, annotated=True)
    assert annotate.call_count == 2


async def test_binding_expiring_after_prepare_matches_legacy(monkeypatch):
    old, old_rows, _ = await prepared(legacy.LegacyValidation, workload(2))
    new, rows, _ = await prepared(MarketValidationService, workload(2))
    monkeypatch.setattr(Clock, "current", NOW + timedelta(hours=2))
    assert dump([new.validate(*rows[3:5], annotated=True)]) == dump([old.validate(*old_rows[3:5])])


async def test_market_index_reuses_version_and_invalidates_setters_and_publication():
    new, rows, _ = await prepared(MarketValidationService, workload(2))
    first = new._market_index()
    assert new._market_index() is first
    generation = new.store.generation
    new.store.mark_published(NOW)
    assert new.store.generation == generation + 1
    second = new._market_index()
    assert second == first and second is not first
    new.store.set_all_markets(rows[:1])
    assert len(new._market_index()) == 1


async def test_exact_leg_cache_keeps_dex_identity_and_expires_with_index():
    new, rows, opportunities = await prepared(MarketValidationService, workload(2))
    item = opportunities[1].model_copy(update={
        "buy_exchange": "hyperliquid", "buy_raw_symbol": "xyz:TOKEN1", "buy_dex": "xyz",
    })
    assert new._opportunity_legs(item)[0] is rows[5]
    assert new._opportunity_legs(item)[0] is rows[5]
    with pytest.raises(ValueError, match="DEX"):
        new._opportunity_legs(item.model_copy(update={"buy_dex": "io"}))
    replacement = rows[5].model_copy(update={"bid": 99})
    new.store.set_all_markets(rows[:5] + [replacement])
    assert new._opportunity_legs(item)[0] is replacement
    # An explicitly supplied index must never borrow rows from the cached snapshot.
    with pytest.raises(ValueError, match="not available"):
        new._opportunity_legs(item, {})


async def test_prepared_invalid_dex_keeps_legacy_validation_error():
    rows, bindings, _ = workload(2)
    rows = [rows[5].model_copy(update={"dex": "io"}), rows[3]]
    new = service(MarketValidationService, bindings)
    annotated = await new.prepare(rows, RiskSettings())
    assert annotated[0].identity_status == "conflict"
    with pytest.raises(ValueError, match="DEX"):
        new.validate(*annotated, annotated=True)
    with pytest.raises(ValueError, match="DEX"):
        new.validate(*annotated)


def test_pair_fingerprint_optional_reuses_supplied_value(monkeypatch):
    rows, _, _ = workload(1)
    expected = pair_validation.quote_fingerprint(*rows[:2])
    assert pair_validation.validate_pair(*rows[:2], now=NOW, quote_fingerprint=expected).quote_fingerprint == expected
    assert pair_validation.validate_pair(*rows[:2], now=NOW).quote_fingerprint == expected
    recompute = Mock(side_effect=AssertionError("fingerprint was recomputed"))
    monkeypatch.setattr(pair_validation, "_quote_fingerprint", recompute)
    assert pair_validation.validate_pair(*rows[:2], now=NOW, quote_fingerprint=expected).quote_fingerprint == expected
    recompute.assert_not_called()


async def test_validate_fingerprints_once_with_cached_review(monkeypatch):
    new, rows, _ = await prepared(MarketValidationService, workload(2))
    a, b = rows[3:5]
    new.reviews[new.pair_key(a, b)] = new.validate(a, b)
    fingerprint = Mock(wraps=pair_validation.quote_fingerprint)
    monkeypatch.setattr(pair_validation, "quote_fingerprint", fingerprint)
    monkeypatch.setattr(pair_validation, "_quote_fingerprint", fingerprint)
    new.validate(a, b, annotated=True)
    assert fingerprint.call_count == 1


async def test_old_published_rows_after_next_prepare_fall_back(monkeypatch):
    new, rows, opportunities = await prepared(MarketValidationService, workload(2))
    # A new prepare can finish before the collector publishes its rows.
    await new.prepare(workload(2)[0], RiskSettings())
    annotate = Mock(wraps=new.annotate)
    monkeypatch.setattr(new, "annotate", annotate)
    new.validate(*rows[3:5], annotated=True)
    assert annotate.call_count == 2


async def test_filter_snapshot_change_during_yield_aborts():
    new, _, opportunities = await prepared(MarketValidationService, workload(2))
    budget = CountingBudget(lambda frame: new.store.mark_published(NOW))
    with pytest.raises(ValueError, match="snapshot changed") as error:
        await new.filter_opportunities_async(opportunities, RiskSettings(), budget)
    assert type(error.value) is review.AlertFilterChanged


async def test_premium_scan_publication_change_does_not_cache_old_rows(monkeypatch):
    from types import SimpleNamespace
    new, rows, _ = await prepared(MarketValidationService, workload(2))
    new.store.set_markets(rows)
    app = SimpleNamespace(state=SimpleNamespace(snapshot_store=new.store))
    monitor = premium.MultiExchangePremiumMonitor(app)
    expected = premium._eligible_at(premium._scan_eligibility(rows, RiskSettings()), NOW)
    budget = CountingBudget(lambda frame: new.store.mark_published(NOW))
    monkeypatch.setattr(premium, "YieldBudget", lambda: budget)
    assert await monitor._current_eligibility_async(RiskSettings(), NOW) == expected
    assert monitor._scan_cache is None
    assert monitor._scan_inflight == {}


async def test_alert_loop_awaits_async_filter_and_records_recomputed(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app import main
    new, _, opportunities = await prepared(MarketValidationService, workload(2))
    new.store.set_opportunities(opportunities)
    stop = asyncio.Event()
    engine = Mock()
    engine.evaluate.side_effect = lambda *args, **kwargs: (stop.set() or [])
    new.filter_opportunities_async = AsyncMock(wraps=new.filter_opportunities_async)
    new.filter_opportunities = Mock(side_effect=AssertionError("sync alert path"))
    app = SimpleNamespace(state=SimpleNamespace(
        snapshot_store=new.store, market_validation=new,
        alert_rule_repo=SimpleNamespace(list=AsyncMock(return_value=[])),
        alert_event_repo=Mock(), alert_delivery_store=SimpleNamespace(prune=AsyncMock()),
        alert_engine=engine,
    ))
    monkeypatch.setattr(main, "datetime", Clock)
    monkeypatch.setattr(main, "_refresh_astro_runtime_settings", AsyncMock())
    monkeypatch.setattr(main, "filter_opportunities", lambda items, risk: items)
    timing = Mock()
    monkeypatch.setattr(main, "_AlertFilterTiming", lambda: timing)
    await asyncio.wait_for(main._run_alert_loop(app, 0.01, stop), timeout=2)
    new.filter_opportunities_async.assert_awaited_once()
    new.filter_opportunities.assert_not_called()
    assert timing.record.call_args.args[1] == 2


async def test_pack_async_exact_values_fields_and_mutable_isolation(large):
    rows = large[0]
    rows = [rows[0].model_copy(update={"estimated_fields": [{"nested": ["bid"]}]})] + rows[1:]
    expected = compact_markets.pack(rows)
    budget = CountingBudget()
    actual = await compact_markets.pack_async(rows, budget)
    assert actual == expected
    assert sum(budget.calls.values()) >= len(rows) // 200
    restored = actual.unpack()
    with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
        assert dump(restored) == dump(rows)
    assert [r.model_fields_set for r in restored] == [r.model_fields_set for r in rows]
    rows[0].estimated_fields[0]["nested"].append("mutated")
    assert actual.unpack()[0].estimated_fields == [{"nested": ["bid"]}]


async def test_scan_async_exact_groups_and_order_with_checkpoints(large):
    new, rows, _ = await prepared(MarketValidationService, large)
    rows = rows + [rows[0], rows[1].model_copy(update={"binding_valid_until": NOW})]
    risk = RiskSettings(excluded_symbols=["TOKEN2USDT"])
    budget = CountingBudget()
    actual = await premium._scan_eligibility_async(rows, risk, budget)
    assert actual == premium._scan_eligibility(rows, risk)
    assert sum(budget.calls.values()) >= len(rows) - 3


async def test_filter_fresh_validation_reused_without_recompute():
    new, rows, opportunities = await prepared(MarketValidationService, workload(2))
    item = opportunities[1].model_copy(update={"validation": new.validate(*rows[3:5])})
    assert await new.filter_opportunities_async([item], RiskSettings()) == [item]
    assert new.last_filter_recomputed == 0


async def test_filter_mapping_change_during_yield_never_returns_stale_capabilities():
    new, rows, opportunities = await prepared(MarketValidationService, workload(2))
    budget = CountingBudget(lambda frame: new.invalidate())
    with pytest.raises(ValueError, match="changed") as error:
        await new.filter_opportunities_async(opportunities, RiskSettings(), budget)
    assert type(error.value) is review.AlertFilterChanged


def test_alert_filter_timing_300_second_window_json_and_reset(monkeypatch):
    import json
    from app import main
    now = [1000.0]
    monkeypatch.setattr(main, "monotonic", lambda: now[0])
    log = Mock()
    monkeypatch.setattr(main.alert_timing_logger, "info", log)
    timing = main._AlertFilterTiming()
    timing.record(10, 3)
    now[0] += 299
    timing.record(30, 7)
    log.assert_not_called()
    now[0] += 1
    timing.record(20, 5)
    assert json.loads(log.call_args.args[0]) == {"alert_filter_timing": {
        "count": 3, "filter_ms": {"p50": 20, "max": 30}, "recomputed": 15,
        "retries": 0, "skipped": 0,
    }}
    now[0] += 300
    timing.record(2, 0)
    assert json.loads(log.call_args.args[0]) == {"alert_filter_timing": {
        "count": 1, "filter_ms": {"p50": 2, "max": 2}, "recomputed": 0,
        "retries": 0, "skipped": 0,
    }}


@pytest.fixture
async def alert_retry_loop(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app import main

    validator, rows, opportunities = await prepared(MarketValidationService, workload(3))
    opportunities = opportunities[1:]
    validator.store.set_opportunities(opportunities)
    validator.filter_opportunities_async = AsyncMock(wraps=validator.filter_opportunities_async)
    validator.store.get_opportunities = Mock(wraps=validator.store.get_opportunities)
    stop = asyncio.Event()
    app = SimpleNamespace(state=SimpleNamespace(
        snapshot_store=validator.store, market_validation=validator,
        alert_rule_repo=SimpleNamespace(list=AsyncMock(return_value=[])),
        alert_event_repo=Mock(), alert_delivery_store=SimpleNamespace(prune=AsyncMock()),
        alert_engine=SimpleNamespace(evaluate=Mock(return_value=[])),
    ))
    monkeypatch.setattr(main, "datetime", Clock)
    monkeypatch.setattr(main, "_refresh_astro_runtime_settings", AsyncMock())
    general_filter = Mock(side_effect=lambda items, risk: items)
    rule_filter = Mock(side_effect=lambda items, *args, **kwargs: items)
    monkeypatch.setattr(main, "filter_opportunities", general_filter)
    monkeypatch.setattr(main, "filter_opportunities_by_alert_rules", rule_filter)
    timing = Mock()
    monkeypatch.setattr(main, "_AlertFilterTiming", lambda: timing)
    heartbeat = Mock(side_effect=lambda *args, **kwargs: stop.set())
    monkeypatch.setattr(main, "beat", heartbeat)
    exception_log = Mock(wraps=main.logger.exception)
    monkeypatch.setattr(main.logger, "exception", exception_log)
    return SimpleNamespace(
        app=app, validator=validator, opportunities=opportunities, rows=rows,
        stop=stop, timing=timing, heartbeat=heartbeat, exception_log=exception_log,
        general_filter=general_filter, rule_filter=rule_filter,
    )


@pytest.mark.parametrize("change", ["snapshot", "mapping", "markets"])
@pytest.mark.parametrize("changes", [1, 2, 3])
async def test_alert_loop_retries_changed_filter_then_succeeds_or_skips(
    monkeypatch, caplog, alert_retry_loop, change, changes,
):
    import logging
    from app import main

    loop = alert_retry_loop
    budgets = []
    latest = loop.opportunities
    inputs = []

    def make_budget():
        changed = False
        inputs.append([item.id for item in latest])

        def mutate(frame):
            nonlocal changed, latest
            if changed or len(budgets) > changes:
                return
            changed = True
            latest = [item.model_copy(update={"id": item.id + "-new"}) for item in latest]
            loop.validator.store.set_opportunities(latest)
            if change == "snapshot":
                loop.validator.store.mark_published(NOW)
            elif change == "markets":
                loop.validator.store.set_all_markets(loop.rows)
            else:
                loop.validator.invalidate()

        budget = CountingBudget(mutate)
        budgets.append(budget)
        return budget

    monkeypatch.setattr(review, "YieldBudget", make_budget)
    with caplog.at_level(logging.WARNING, logger=main.logger.name):
        await asyncio.wait_for(main._run_alert_loop(loop.app, 60, loop.stop), timeout=2)

    attempts = min(changes + 1, 3)
    skipped = changes == 3
    assert len(budgets) == attempts
    assert loop.validator.filter_opportunities_async.await_count == attempts
    assert loop.validator.store.get_opportunities.call_count == attempts
    assert loop.general_filter.call_count == attempts
    assert [
        [item.id for item in call.args[0]]
        for call in loop.validator.filter_opportunities_async.await_args_list
    ] == inputs
    assert all(before != after for before, after in zip(inputs, inputs[1:]))
    loop.timing.record.assert_called_once()
    assert loop.timing.record.call_args.args[0] >= 0
    assert loop.timing.record.call_args.args[1] == (3 if skipped else 2 + changes)
    assert loop.timing.record.call_args.kwargs == {"retries": min(changes, 2), "skipped": skipped}
    loop.heartbeat.assert_called_once_with("alert-loop", ok=not skipped)
    loop.exception_log.assert_not_called()
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    if skipped:
        loop.rule_filter.assert_not_called()
        loop.app.state.alert_engine.evaluate.assert_not_called()
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING
        assert warnings[0].exc_info is None
        assert "skipped" in warnings[0].getMessage() and "2 retries" in warnings[0].getMessage()
        assert "alert loop failed" not in caplog.text
    else:
        loop.rule_filter.assert_called_once()
        loop.app.state.alert_engine.evaluate.assert_called_once()
        assert [item.id for item in loop.app.state.alert_engine.evaluate.call_args.args[0]] == [
            item.id for item in latest
        ]
        assert not warnings


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
async def test_alert_loop_does_not_retry_unrelated_filter_errors(alert_retry_loop, error_type):
    from app import main

    loop = alert_retry_loop
    # Even an identical message must not make a plain ValueError retryable.
    loop.validator.filter_opportunities_async.side_effect = error_type(
        "mapping or snapshot changed during alert filtering"
    )
    await asyncio.wait_for(main._run_alert_loop(loop.app, 60, loop.stop), timeout=2)
    loop.validator.filter_opportunities_async.assert_awaited_once()
    loop.validator.store.get_opportunities.assert_called_once()
    loop.exception_log.assert_called_once_with("alert loop failed")
    loop.timing.record.assert_not_called()
    loop.app.state.alert_engine.evaluate.assert_not_called()


async def test_alert_loop_filter_cancellation_propagates_without_retry(alert_retry_loop):
    from app import main

    loop = alert_retry_loop
    loop.validator.filter_opportunities_async.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await main._run_alert_loop(loop.app, 60, loop.stop)
    loop.validator.filter_opportunities_async.assert_awaited_once()
    loop.exception_log.assert_not_called()
    loop.timing.record.assert_not_called()
    loop.heartbeat.assert_not_called()


def test_alert_filter_timing_counts_retries_and_skips_and_resets(monkeypatch):
    import json
    from app import main

    now = [1000.0]
    monkeypatch.setattr(main, "monotonic", lambda: now[0])
    log = Mock()
    monkeypatch.setattr(main.alert_timing_logger, "info", log)
    timing = main._AlertFilterTiming()
    timing.record(10, 1, retries=1, skipped=False)
    now[0] += 300
    timing.record(20, 2, retries=2, skipped=True)
    assert json.loads(log.call_args.args[0]) == {"alert_filter_timing": {
        "count": 2, "filter_ms": {"p50": 15, "max": 20}, "recomputed": 3,
        "retries": 3, "skipped": 1,
    }}
    now[0] += 300
    timing.record(5, 0)
    assert json.loads(log.call_args.args[0]) == {"alert_filter_timing": {
        "count": 1, "filter_ms": {"p50": 5, "max": 5}, "recomputed": 0,
        "retries": 0, "skipped": 0,
    }}


@pytest.mark.parametrize("fallback", [False, True])
async def test_prepare_collision_and_candidate_combinations_yield_except_third_attempt(monkeypatch, fallback):
    from itertools import combinations
    rows, bindings, _ = workload(4)
    rows = [r.model_copy(update={"symbol": "COLLISIONUSDT"}) for r in rows]
    validator = service(MarketValidationService, bindings)
    attempts = set()
    budget = CountingBudget()

    def mutate(frame):
        attempt = frame.f_locals["attempt"]
        if fallback and attempt < 2 and attempt not in attempts:
            attempts.add(attempt)
            validator.invalidate()

    budget.callback = mutate
    monkeypatch.setattr(review, "YieldBudget", lambda: budget)
    loops = Counter()

    def checked_combinations(values, count):
        caller = inspect.currentframe().f_back
        kind = "collision" if "groups" not in caller.f_locals else "candidate"
        for pair in combinations(values, count):
            before = sum(budget.calls.values())
            yield pair
            after = sum(budget.calls.values())
            loops[kind] += 1
            assert (after == before) if fallback else (after > before)

    monkeypatch.setattr(review, "combinations", checked_combinations)
    annotated = await validator.prepare(rows, RiskSettings())
    assert len(annotated) == len(rows)
    assert loops["collision"] > 0 and loops["candidate"] > 0
    assert attempts == ({0, 1} if fallback else set())
    assert validator._prepared_epoch == validator._mutation_epoch


async def test_prepare_mapping_change_in_combination_aborts_before_invalidation(monkeypatch):
    rows, bindings, _ = workload(4)
    rows = [r.model_copy(update={"symbol": "COLLISIONUSDT"}) for r in rows]
    validator = service(MarketValidationService, bindings)

    def mutate(frame):
        if "collision_rows" in frame.f_locals:
            validator.invalidate()

    monkeypatch.setattr(review, "YieldBudget", lambda: CountingBudget(mutate))
    with pytest.raises(ValueError, match="changed"):
        await validator.prepare(rows, RiskSettings())
    validator.repository.invalidate_opportunities.assert_not_awaited()


def test_prepare_business_tail_matches_baseline_after_exact_additions():
    """Also audit against the frozen 00dfa9e reference."""
    from textwrap import dedent
    marker = "        if self.collision_symbols:\n"
    actual = inspect.getsource(MarketValidationService.prepare).split(marker, 1)[1]
    expected = inspect.getsource(legacy.LegacyValidation.prepare).split(marker, 1)[1]
    for width, count in ((16, 2), (20, 1)):
        indent = " " * width
        addition = (indent + "if cooperative:\n"
                    + indent + "    await budget.maybe_yield()\n"
                    + indent + "    if epoch != self._mutation_epoch:\n"
                    + indent + '        raise ValueError("mapping changed during preparation")\n')
        assert actual.count(addition) == count
        actual = actual.replace(addition, "")
    additions = [
        '        if epoch != self._mutation_epoch:\n'
        '            raise ValueError("mapping changed during preparation")\n',
        '        self._prepared_epoch = epoch\n'
        '        self._prepared_rows = {\n'
        '            id(row): (row, key) for row, key in zip(annotated, market_keys, strict=True)\n'
        '        }\n'
        '        self._prepared_fx = prepared_fx\n'
        '        self._prepared_collision = collision_symbols\n'
        '        self._discard_stale_market_index()\n',
    ]
    for addition in additions:
        assert actual.count(addition) == 1
        actual = actual.replace(addition, "")
    assert dedent(actual) == dedent(expected)


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
async def test_collector_publication_is_prepare_output_and_matches_baseline(large, mode):
    from app.exchanges.base import ExchangeAdapter
    class Adapter(ExchangeAdapter):
        def __init__(self, name):
            self.name = name
        async def fetch_spot_tickers(self):
            return []
        async def fetch_future_tickers(self):
            return [r for r in large[0] if r.exchange == self.name]

    outputs = []
    for collector_cls, validation_cls in ((legacy.LegacyCollector, legacy.LegacyValidation),
                                           (MarketCollector, MarketValidationService)):
        validator = service(validation_cls, large[1], mode)
        captured = []
        original = validator.prepare
        async def capture(markets, risk):
            result = await original(markets, risk)
            captured.extend(result)
            return result
        validator.prepare = capture
        collector = collector_cls([Adapter(e) for e in ("bitget", "gate", "hyperliquid")],
                                  validator.store, market_validation=validator, now_fn=lambda: NOW)
        await collector.collect_once()
        published = validator.store.get_all_markets()
        assert len(published) == 12000
        assert all(a is b for a, b in zip(published, captured, strict=True))
        outputs.append((dump(published), dump(validator.store.get_markets()),
                        dump(validator.store.get_opportunities())))
        await validator.aclose()
    assert outputs[0] == outputs[1]
