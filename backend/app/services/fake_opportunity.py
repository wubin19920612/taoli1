"""Bounded, display-only diagnostics over exact markets and comparable assets."""

from array import array
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
import heapq
from statistics import fmean, median, pstdev

from app.services.spread_engine import _market_observed_at, _has_executable_book


class MinuteRing:
    """Fixed-size numeric storage, avoiding thousands of retained float tuples."""
    __slots__ = ("values", "capacity", "width", "start", "count")

    def __init__(self, capacity, width):
        self.values = array("d", [0]) * (capacity * width)
        self.capacity, self.width, self.start, self.count = capacity, width, 0, 0

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if index < 0:
            index += self.count
        if not 0 <= index < self.count:
            raise IndexError(index)
        offset = ((self.start + index) % self.capacity) * self.width
        return tuple(self.values[offset:offset + self.width])

    def __setitem__(self, index, value):
        if index < 0:
            index += self.count
        offset = ((self.start + index) % self.capacity) * self.width
        for i, number in enumerate(value):
            self.values[offset + i] = number

    def append(self, value):
        if self.count == self.capacity:
            self.start = (self.start + 1) % self.capacity
        else:
            self.count += 1
        self[self.count - 1] = value

    def popleft(self):
        self.start = (self.start + 1) % self.capacity
        self.count -= 1


@lru_cache(maxsize=20000)
def _identity(exchange, market_type, raw, dex, multiplier, size):
    dex = dex.lower() if dex else (raw.split(":", 1)[0].lower() if ":" in raw else "main") if exchange == "hyperliquid" and market_type == "future" else ""
    value = (exchange.lower(), str(market_type), raw, dex, f"{multiplier:.12g}")
    return (*value, f"{size:.12g}") if size is not None else value


def prepare_markets(markets, now):
    ts = now.timestamp()
    result = {}
    for m in markets:
        key = market_key(m)
        observed = _market_observed_at(m).timestamp()
        result[key] = (m, observed, 0 <= ts - observed <= 10 and _has_executable_book(m))
    return result


def market_key(m):
    return _identity(m.exchange, m.market_type, m.raw_symbol, m.dex,
                     m.symbol_alias_price_multiplier, m.contract_size_multiplier)


def opportunity_keys(op):
    return (_identity(op.buy_exchange, op.buy_market_type, op.buy_raw_symbol or "", op.buy_dex,
                      op.buy_price_multiplier, op.buy_contract_size_multiplier),
            _identity(op.sell_exchange, op.sell_market_type, op.sell_raw_symbol or "", op.sell_dex,
                      op.sell_price_multiplier, op.sell_contract_size_multiplier))


class MarketChanges:
    """Compare quote content, not object identity (callers may reuse models)."""
    def __init__(self):
        self.previous = {}

    def update(self, prepared):
        current = {k: (observed, fresh, m.bid, m.ask, m.funding_rate_pct,
                       m.funding_next_time, m.funding_interval_hours, m.asset_id,
                       m.symbol, m.identity_collision)
                   for k, (m, observed, fresh) in prepared.items()}
        changed = {k for k, value in current.items() if self.previous.get(k) != value}
        changed.update(self.previous.keys() - current.keys())
        self.previous = current
        return changed


@dataclass(slots=True)
class PriceState:
    mid: float
    changed: float
    seen: float
    asset: str = ""
    samples: MinuteRing = field(default_factory=lambda: MinuteRing(16, 3))


class FakeOpportunityScorer:
    def __init__(self, max_markets=20000, max_series=3000):
        self.prices = {}
        self.spreads = {}
        self.max_markets = max_markets
        self.max_series = max_series
        self.dropped = 0
        self.last_prune_minute = None
        self.changes = MarketChanges()
        self.groups = defaultdict(dict)
        self.assets = {}
        self.hard = set()
        self.frozen = set()

    def _remove_price(self, key):
        self.prices.pop(key, None)
        asset = self.assets.pop(key, None)
        if asset is not None:
            self.groups[asset].pop(key, None)
        self.hard.discard(key)
        self.frozen.discard(key)

    def score(self, markets, opportunities, now, prepared=None, changed=None,
              *, related_assets=None, history=(), clear_spreads=()):
        ts = now.timestamp()
        minute = int(ts // 60)
        # Expire absent keys as well as per-key rings: changing listings cannot
        # accumulate an unbounded dictionary over a long-running process.
        new_minute = minute != self.last_prune_minute
        if new_minute:
            for key in [k for k, v in self.prices.items() if ts - v.seen > 900]:
                self._remove_price(key)
            self.spreads = {k: v for k, v in self.spreads.items() if minute - v[-1][0] < 60}
            self.last_prune_minute = minute
        prepared = prepared if prepared is not None else prepare_markets(markets, now)
        changed = self.changes.update(prepared) if changed is None else changed
        for op_id in clear_spreads:
            self.spreads.pop(op_id, None)
        related_assets = (set(related_assets) if related_assets is not None else
                          {m.asset_id or m.symbol for m in markets})
        # One bounded heap per round, never a full scan per admission. Related
        # assets are protected even when the minute history arrives first.
        evictable = [(s.seen, k) for k, s in self.prices.items()
                     if s.asset not in related_assets] if len(self.prices) + len(prepared) + len(history) >= self.max_markets else []
        heapq.heapify(evictable)

        def observe(key, market):
            mid = (market.bid + market.ask) / 2
            state = self.prices.get(key)
            if state is None:
                if len(self.prices) >= self.max_markets:
                    while evictable:
                        seen, victim = heapq.heappop(evictable)
                        candidate = self.prices.get(victim)
                        if candidate is None or candidate.asset in related_assets:
                            continue
                        if candidate.seen != seen:
                            heapq.heappush(evictable, (candidate.seen, victim))
                            continue
                        self._remove_price(victim)
                        break
                    else:
                        self.dropped += 1
                        return None
                state = self.prices[key] = PriceState(mid, ts, ts, market.asset_id or market.symbol)
                if state.asset not in related_assets:
                    heapq.heappush(evictable, (ts, key))
            if mid != state.mid:
                state.changed = ts
            state.mid, state.seen = mid, ts
            state.asset = market.asset_id or market.symbol
            if state.samples and state.samples[-1][0] == minute:
                m, low, high = state.samples[-1]
                if mid < low or mid > high:
                    state.samples[-1] = (m, min(low, mid), max(high, mid))
            else:
                state.samples.append((minute, mid, mid))
            return state

        # Unrelated minute samples feed frozen-leg history only, never current
        # median votes or ledger books. changed can lag <=60s; intraminute
        # reversals/extrema can be missed (PM 10). Shards may differ by one
        # capture cycle (PM 14).
        for key, (market, _, fresh) in prepare_markets(history, now).items():
            if fresh and key not in prepared:
                observe(key, market)
        dirty = set(self.groups) if new_minute else set()
        if new_minute:
            changed = changed | {key for key, info in prepared.items() if info[2]}
        for key in changed:
            old_asset = self.assets.get(key)
            info = prepared.get(key)
            market = info[0] if info else None
            asset = ((market.asset_id or market.symbol) if info and info[2]
                     and not (market.identity_collision and not market.asset_id) else None)
            if old_asset is not None and old_asset != asset:
                self.groups[old_asset].pop(key, None)
                self.assets.pop(key, None)
                dirty.add(old_asset)
                self.hard.discard(key)
                self.frozen.discard(key)
            if info is None or not info[2]:
                continue
            state = observe(key, market)
            if state is None:
                continue
            asset = market.asset_id or market.symbol
            # Reviewed incompatible identities must not vote on each other's price.
            if market.identity_collision and not market.asset_id:
                continue
            if key not in self.groups[asset]:
                self.assets[key] = asset
                self.groups[asset][key] = (market.exchange.lower(), key, state)
            dirty.add(asset)
        hard, frozen = self.hard, self.frozen
        for asset in dirty:
            members = self.groups[asset]
            if not members:
                del self.groups[asset]
                continue
            hard.difference_update(members)
            frozen.difference_update(members)
            rows = members.values()
            can_have_outlier = len(rows) >= 3 and len({ex for ex, _, _ in rows}) >= 3
            if not can_have_outlier and not any(ts - state.changed >= 900 for _, _, state in rows):
                continue
            # Each exchange gets one vote even if several DEXes/market types exist.
            for exchange, key, state in rows:
                is_frozen = ts - state.changed >= 900
                if not can_have_outlier and not is_frozen:
                    continue
                other = defaultdict(list)
                moving_other = False
                for ex, _, peer in rows:
                    if ex == exchange:
                        continue
                    other[ex].append(peer.mid)
                    if is_frozen:
                        samples = [x for x in peer.samples if x[0] >= minute - 14]
                        if samples and max(x[2] for x in samples) / min(x[1] for x in samples) - 1 > 0.003:
                            moving_other = True
                votes = [median(v) for v in other.values()]
                if len(votes) >= 2 and max(votes) / min(votes) - 1 < 0.005:
                    if abs(state.mid / median(votes) - 1) > 0.03:
                        hard.add(key)
                if ts - state.changed >= 900 and moving_other:
                    frozen.add(key)
        result = {}
        for op in opportunities:
            reasons = []
            if hard or frozen:
                keys = opportunity_keys(op)
                if keys[0] in hard or keys[1] in hard:
                    reasons.append("median_outlier")
                if keys[0] in frozen or keys[1] in frozen:
                    reasons.append("frozen_leg")
            if op.open_spread_pct >= 1:
                ring = self.spreads.get(op.id)
                if ring is None and len(self.spreads) < self.max_series:
                    ring = self.spreads[op.id] = MinuteRing(60, 2)
                if ring is not None:
                    if not ring or ring[-1][0] != minute:
                        ring.append((minute, op.open_spread_pct))
                    while ring and minute - ring[0][0] >= 60:
                        ring.popleft()
                    if len(ring) == 60 and ring[-1][0] - ring[0][0] == 59:
                        values = [v for _, v in ring]
                        mean = fmean(values)
                        if mean > 1 and pstdev(values) < mean * 0.05:
                            reasons.append("spread_plateau")
                else:
                    self.dropped += 1
            else:
                self.spreads.pop(op.id, None)
            if op.buy_volume_24h_usdt is None or op.sell_volume_24h_usdt is None:
                reasons.append("volume_unknown")
            if op.open_spread_pct > 5:
                reasons.append("extreme_spread")
            score = len(reasons) + ("median_outlier" in reasons)
            result[op.id] = {"fake_score": score, "fake_reasons": reasons, "fake_flag": score >= 2}
        return result
