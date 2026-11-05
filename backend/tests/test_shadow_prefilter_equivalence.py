"""Test-only full/selected reference paths for PM round-three dependency checks.

Unsaturated paths must agree; saturation uses PM 12-13 priority admission.
The full path exists only here, never as a production switch.
"""

import json
import random
from datetime import timedelta

import pytest

from app.core.config import Settings
from app.services.fake_opportunity import FakeOpportunityScorer, market_key, opportunity_keys
from app.services.shadow_ledger import ShadowLedger
from app.services.spread_engine import build_directional_opportunity
from shadow_helpers import NOW, market


def selected_inputs(markets, opportunities, ledger, scorer, now):
    """Literal test-only model of decisions 1, 8 and 9; no live-thread reads."""
    held_ids = frozenset(ledger.positions)
    held_keys = frozenset(key for p in ledger.positions.values() for key in p.keys)
    related = [op for op in opportunities
               if op.open_spread_pct >= ledger.minimum or op.id in held_ids]
    keys = held_keys | {key for op in related for key in opportunity_keys(op)}
    assets = {m.asset_id or m.symbol for m in markets if market_key(m) in keys}
    if int(now.timestamp() // 60) != scorer.last_prune_minute:
        return markets, related
    return [m for m in markets if market_key(m) in keys or (m.asset_id or m.symbol) in assets], related


@pytest.mark.parametrize("capacity,initial_count", [
    pytest.param(6000, 3, id="below-capacity-control"),
    pytest.param(6, 3, id="small-capacity-counterexample"),
    pytest.param(6000, 5997, id="production-capacity-counterexample"),
])
def test_unrelated_market_admission_preserves_median_and_opening_metadata(capacity, initial_count):
    """Unrelated mid-minute listings must not alter a later relevant opening.

    At the minute boundary both paths receive the same initial markets. Three
    unrelated new listings arrive inside that minute. Then a new asset becomes
    relevant. This uses no frozen-leg sampling exception: only median_outlier
    and persisted opening metadata are compared, exactly as decision 10 asks.
    """
    rng = random.Random(20261105)
    background = [market(symbol=f"F{i}USDT", raw_symbol=f"F{i}USDT",
                         price=100 + rng.random()) for i in range(initial_count + 3)]
    target = [market(ex, price, symbol="TARGETUSDT", raw_symbol="TARGETUSDT")
              for ex, price in (("binance", 100), ("bitget", 104), ("okx", 100))]
    op = build_directional_opportunity(target[0], target[1], mode="FF")
    full = FakeOpportunityScorer(max_markets=capacity)
    selected = FakeOpportunityScorer(max_markets=capacity)
    full_ledger = ShadowLedger(Settings(shadow_enabled=True))
    selected_ledger = ShadowLedger(Settings(shadow_enabled=True))
    for second, quotes, ops in (
        (0, background[:initial_count], []),
        (1, background, []),
        (2, background + target, [op]),
    ):
        now = NOW + timedelta(seconds=second)
        for quote in quotes:
            quote.timestamp = now
        related_markets, related_ops = selected_inputs(quotes, ops, selected_ledger, selected, now)
        full_annotations = full.score(quotes, ops, now, related_assets={m.asset_id or m.symbol for m in target} if ops else set())
        selected_annotations = selected.score(related_markets, related_ops, now, related_assets={m.asset_id or m.symbol for m in target} if ops else set())
        full_ledger.process(quotes, ops, now, full_annotations)
        selected_ledger.process(related_markets, related_ops, now, selected_annotations)
        assert full_ledger.open_count == selected_ledger.open_count == (2 if second == 2 else 0)

    full_rows = [dict(row) for row in full_ledger.pending.values()]
    selected_rows = [dict(row) for row in selected_ledger.pending.values()]
    for row in full_rows + selected_rows:
        row.pop("id")  # Independently generated UUIDs are not ledger semantics.
    print("SHADOW_EQUIVALENCE " + json.dumps({
        "capacity": capacity, "initial_markets": initial_count,
        "full": full_annotations[op.id], "selected": selected_annotations[op.id],
        "full_price_states": len(full.prices), "selected_price_states": len(selected.prices),
        "full_dropped": full.dropped, "selected_dropped": selected.dropped,
        "opening_metadata_equal": full_rows == selected_rows,
    }, sort_keys=True))
    assert all(market_key(m) in full.prices and market_key(m) in selected.prices for m in target)
    assert full_annotations[op.id]["fake_reasons"] == ["median_outlier"]
    assert len(full.prices) <= capacity and len(selected.prices) <= capacity
    assert full_annotations == selected_annotations, "related admitted peers must all vote"
    assert full_rows == selected_rows


@pytest.mark.asyncio
async def test_runtime_matches_full_path_combined_seeded_sequence(tmp_path):
    """Exercise the actual selected runtime against a full-only test oracle."""
    import copy
    from datetime import UTC, datetime
    from app.db.database import connect_database
    from app.db.schema import initialize_shadow_schema
    from app.db.repositories import ShadowTradeRepository
    from app.services.shadow_runtime import ShadowRuntime
    from app.services.snapshot_consumers import PublishedSnapshot
    from app.services.snapshot_store import SnapshotStore

    start = datetime.now(UTC).replace(second=0, microsecond=0)
    settings = Settings(shadow_enabled=True, shadow_max_open=4,
                        database_url="sqlite:///" + str(tmp_path / "selected.db"))
    db = await connect_database(settings.sqlite_path)
    oracle_db = await connect_database(str(tmp_path / "full.db"))
    await initialize_shadow_schema(oracle_db)
    repo = ShadowTradeRepository(oracle_db)
    store = SnapshotStore()
    runtime = ShadowRuntime(settings, store)
    full, scorer = ShadowLedger(settings), FakeOpportunityScorer()
    rng = random.Random(20261105)
    snapshots, observed_reasons = [], set()
    # Cross up/down, partial variant exit, missing/stale legs, disappeared ops,
    # max-open refusal, settlement at 30s, checkpoint and restart at 62s,
    # cooldown refusals and reopen after 600s are in one continuous sequence.
    steps = [0, 5, 10, 20, 31, 61, 62, 100, 200, 610, 611, 672, 1220, 1280]

    def normalized(rows):
        values = []
        for row in rows:
            value = copy.deepcopy(row)
            value.pop("id", None)
            values.append(json.dumps(value, sort_keys=True, default=str))
        return sorted(values)

    try:
        await runtime.initialize(db)
        for second in steps:
            now = start + timedelta(seconds=second)
            quotes, ops = [], []
            for index, name in enumerate(("A", "B", "C", "D")):
                price = 100 + rng.uniform(-.02, .02)
                spread = .5
                if name == "A" and 5 <= second < 611:
                    spread = .3 if second == 5 else .05 if second < 20 else .5
                if name == "C" and second >= 100:
                    spread = 3 if second == 100 else .05
                if name == "D" and second < 20:
                    spread = .05
                legs = [market(ex, price * (1 + (spread / 100 if leg else 0)),
                               symbol=name, raw_symbol=name, timestamp=now,
                               funding_rate_pct=.01 if leg == 0 else .03,
                               funding_interval_hours=1,
                               funding_next_time=start + timedelta(seconds=30))
                        for leg, ex in enumerate(("binance", "bitget"))]
                op = build_directional_opportunity(*legs, mode="FF", buy_fee_pct=.05, sell_fee_pct=.06)
                if name == "B" and 5 <= second <= 610:
                    if second >= 10:
                        legs[0].timestamp = start  # present but stale
                    legs.pop()  # missing sell, even after op disappears
                quotes.extend(legs)
                if not (name == "B" and second >= 5):
                    ops.append(op)
            # Never relevant assets still get minute history; their quote changes
            # must not influence any ledger accounting or diagnostic annotation.
            quotes.extend(market(symbol=f"BG{i}", raw_symbol=f"BG{i}", timestamp=now)
                          for i in range(20))
            annotations = scorer.score(quotes, ops, now)
            for op in ops:
                annotations[op.id].update(runtime.worker_catalog.fields(op))
            full.process(quotes, ops, now, annotations)
            await full.flush(repo)
            store.mark_published(now)
            await runtime.consume(PublishedSnapshot(store.generation, now, tuple(quotes), tuple(ops),
                opportunities_version=runtime.store.opportunities_version))
            assert runtime.errors == 0
            assert runtime.ledger.open_count == full.open_count
            assert runtime.ledger.dropped == full.dropped
            assert runtime.ledger.cooldowns == full.cooldowns
            assert set(runtime.ledger.positions) == set(full.positions)
            for key, position in full.positions.items():
                assert runtime.ledger.positions[key].state() == position.state()
                assert normalized([r.payload() if hasattr(r, "payload") else dict(r) for r in position.rows]) == normalized([
                    r.payload() if hasattr(r, "payload") else dict(r) for r in runtime.ledger.positions[key].rows])
            selected_rows = await runtime.repo.list_trades(limit=1000)
            full_rows = await repo.list_trades(limit=1000)
            assert normalized(selected_rows) == normalized(full_rows)
            for row in selected_rows:
                if row["exit_reason"]:
                    observed_reasons.add(row["exit_reason"])
            shown = store.get_opportunities()
            assert len(shown) == len(ops)
            for source, target in zip(ops, shown):
                assert source.id == target.id
                if target is not source:
                    for field in ("fake_score", "fake_flag", "fake_reasons", "executable"):
                        assert getattr(target, field) == annotations[source.id][field]
            snapshots.append((second, runtime.ledger.open_count, full.dropped))
            if second == 62:
                for position in full.positions.values():
                    full._queue(position)
                await full.flush(repo)
                await runtime.aclose()
                runtime = ShadowRuntime(settings, store)
                await runtime.initialize(db)
                full = ShadowLedger(settings)
                full.restore(*(await repo.restore(now)))
                # A real process restart resets diagnostics as well as caches.
                scorer = FakeOpportunityScorer()
        assert {"converged", "stop", "stale"} <= observed_reasons
        assert any(dropped > 0 for _, _, dropped in snapshots)
        assert any(row["funding_bps"] != 0 for row in await repo.list_trades(limit=1000))
        assert len({row["opened_at"] for row in await repo.list_trades(limit=1000)}) >= 3
    finally:
        await runtime.aclose()
        await db.close()
        await oracle_db.close()


@pytest.mark.asyncio
async def test_bmt_first_threshold_crossing_already_has_frozen_leg(tmp_path):
    import math
    from app.db.database import connect_database
    from app.services.shadow_runtime import ShadowRuntime
    from app.services.snapshot_consumers import PublishedSnapshot
    from app.services.snapshot_store import SnapshotStore

    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True,
        database_url="sqlite:///" + str(tmp_path / "bmt.db")), store)
    db = await connect_database(runtime.settings.sqlite_path)
    try:
        await runtime.initialize(db)
        for minute in range(17):
            now = NOW + timedelta(minutes=minute)
            # Bitget stays frozen. Other venues move >.3% while wide asks keep
            # the executable opening below .2% until the final first crossing.
            price = 100 + .2 * math.sin(minute * math.pi / 3)
            quotes = [market(ex, price, ask=102.4, timestamp=now,
                             symbol="BMTUSDT", raw_symbol="BMTUSDT")
                      for ex in ("binance", "okx", "bybit")]
            quotes.append(market("bitget", 102.5, timestamp=now,
                                 symbol="BMTUSDT", raw_symbol="BMTUSDT"))
            if minute == 16:
                quotes[0].ask = quotes[0].bid
            op = build_directional_opportunity(quotes[0], quotes[3], mode="FF")
            assert (op.open_spread_pct >= .2) == (minute == 16)
            store.mark_published(now)
            await runtime.consume(PublishedSnapshot(store.generation, now, tuple(quotes), (op,),
                opportunities_version=runtime.store.opportunities_version))
            if minute < 16:
                assert runtime.ledger.open_count == 0
                assert store.get_opportunities()[0] is op
        assert runtime.errors == 0 and runtime.ledger.open_count == 2
        assert "frozen_leg" in store.get_opportunities()[0].fake_reasons
        assert all("frozen_leg" in row["fake_reasons"] for row in await runtime.repo.list_trades())
    finally:
        await runtime.aclose()
        await db.close()


@pytest.mark.asyncio
async def test_unrelated_changed_time_and_new_peer_delay_are_at_most_sixty_seconds():
    from app.services.shadow_runtime import ShadowRuntime
    from app.services.snapshot_consumers import PublishedSnapshot
    from app.services.snapshot_store import SnapshotStore

    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True), store)
    full = FakeOpportunityScorer()
    for second in (0, 1, 59, 60):
        now = NOW + timedelta(seconds=second)
        quotes = [market(price=100 if second == 0 else 101, timestamp=now)]
        full.score(quotes, [], now)
        store.mark_published(now)
        await runtime.consume(PublishedSnapshot(store.generation, now, tuple(quotes), (),
            opportunities_version=runtime.store.opportunities_version))
        key = market_key(quotes[0])
        if second < 60:
            assert runtime.scorer.prices[key].changed == NOW.timestamp()
    delta = runtime.scorer.prices[key].changed - full.prices[key].changed
    assert 0 <= delta <= 60 and delta == 59
    # Relevant exact legs appear immediately; a newly listed third venue may
    # wait for the next minute's asset index, but cannot wait longer.
    for second in (61, 119, 120):
        now = NOW + timedelta(seconds=second)
        quotes = [market(ex, price, timestamp=now) for ex, price in
                  (("binance", 100), ("bitget", 104), ("okx", 100))]
        op = build_directional_opportunity(quotes[0], quotes[1], mode="FF")
        store.mark_published(now)
        await runtime.consume(PublishedSnapshot(store.generation, now, tuple(quotes), (op,),
            opportunities_version=runtime.store.opportunities_version))
        assert ("median_outlier" in store.get_opportunities()[0].fake_reasons) == (second == 120)
    assert runtime.errors == 0
    await runtime.aclose()


@pytest.mark.asyncio
async def test_filtered_below_threshold_clears_plateau_but_absence_preserves_it():
    from app.services.shadow_runtime import ShadowRuntime
    from app.services.snapshot_consumers import PublishedSnapshot
    from app.services.snapshot_store import SnapshotStore
    from shadow_helpers import pair

    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True), store)
    full = FakeOpportunityScorer()
    _, op = pair(open_spread_pct=2.5)
    for minute in range(60):
        now = NOW + timedelta(minutes=minute)
        full.score([], [op], now)
        store.mark_published(now)
        await runtime.consume(PublishedSnapshot(store.generation, now, (), (op,),
            opportunities_version=runtime.store.opportunities_version))
    assert "spread_plateau" in store.get_opportunities()[0].fake_reasons
    now += timedelta(seconds=1)
    full.score([], [], now)
    store.mark_published(now)
    await runtime.consume(PublishedSnapshot(store.generation, now, (), (),
        opportunities_version=runtime.store.opportunities_version))
    assert op.id in full.spreads and op.id in runtime.scorer.spreads
    low = op.model_copy(update={"open_spread_pct": .1})
    full.score([], [low], now)
    store.mark_published(now)
    await runtime.consume(PublishedSnapshot(store.generation, now, (), (low,),
        opportunities_version=runtime.store.opportunities_version))
    assert not full.spreads and not runtime.scorer.spreads
    assert store.get_opportunities()[0] is low
    now += timedelta(seconds=1)
    expected = full.score([], [op], now)
    store.mark_published(now)
    await runtime.consume(PublishedSnapshot(store.generation, now, (), (op,),
        opportunities_version=runtime.store.opportunities_version))
    assert store.get_opportunities()[0].fake_reasons == expected[op.id]["fake_reasons"] == []
    await runtime.aclose()
