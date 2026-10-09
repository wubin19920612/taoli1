"""Frozen pre-optimization pair traversal (0819151), used as a differential oracle.

Keep the deliberately redundant traversal independent of the optimized enumeration.
Pricing/model construction helpers are unchanged by this refactor.
"""
from collections import defaultdict
from datetime import UTC, datetime

from reference_market_sessions import is_market_snapshot_tradable

from app.models.market import MarketSnapshot
from app.models.opportunity import Opportunity
from app.services.spread_engine import (
    Mode,
    _has_executable_book,
    _market_identity,
    _market_observed_at,
    build_directional_opportunity,
    midpoint_spread_pct,
    orient_pair,
)


def build_opportunities(
    snapshots: list[MarketSnapshot],
    mode: Mode,
    buy_fee_pct: float = 0.1,
    sell_fee_pct: float = 0.1,
    safety_slippage_pct: float = 0.05,
    now: datetime | None = None,
    stale_after_seconds: int | None = None,
) -> list[Opportunity]:
    current = now or datetime.now(UTC)
    by_symbol: dict[str, list[MarketSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        if not is_market_snapshot_tradable(snapshot, current):
            continue
        if not _has_executable_book(snapshot):
            continue
        observed_at = _market_observed_at(snapshot)
        if (
            stale_after_seconds is not None
            and (current - observed_at).total_seconds() > stale_after_seconds
        ):
            continue
        by_symbol[snapshot.symbol].append(snapshot)

    opportunities: list[Opportunity] = []
    seen: set[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = set()
    for symbol, legs in by_symbol.items():
        if len(legs) < 2:
            continue
        for first in legs:
            for second in legs:
                if first == second:
                    continue
                oriented = orient_pair(mode, first, second)
                if oriented is None:
                    continue
                buy_leg, sell_leg = oriented
                pair_key = sorted((_market_identity(buy_leg), _market_identity(sell_leg)))
                dedupe_key = (mode, symbol, pair_key[0], pair_key[1])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                open_spread_pct, close_spread_pct = midpoint_spread_pct(buy_leg, sell_leg)
                if mode in {"FF", "SS"} and open_spread_pct < 0 and close_spread_pct < 0:
                    buy_leg, sell_leg = sell_leg, buy_leg
                    open_spread_pct, close_spread_pct = midpoint_spread_pct(buy_leg, sell_leg)
                if open_spread_pct <= 0:
                    continue

                opportunities.append(
                    build_directional_opportunity(
                        buy_leg,
                        sell_leg,
                        mode=mode,
                        buy_fee_pct=buy_fee_pct,
                        sell_fee_pct=sell_fee_pct,
                        safety_slippage_pct=safety_slippage_pct,
                    )
                )
    return sorted(opportunities, key=lambda item: item.open_spread_pct, reverse=True)
