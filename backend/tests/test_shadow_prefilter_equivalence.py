"""Test-only full/selected reference paths for PM round-three dependency checks.

These are red-phase tests: retain strict equality if an unapproved dependency
is found. Never turn the production implementation into a full-path switch.
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
        full_annotations = full.score(quotes, ops, now)
        selected_annotations = selected.score(related_markets, related_ops, now)
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
    assert full_annotations == selected_annotations, "unrelated admissions changed median_outlier"
    assert full_rows == selected_rows
