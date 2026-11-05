"""Paper observations only. No account, order, or opportunity-history dependencies."""

import json
import heapq
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from app.services.fake_opportunity import MarketChanges, opportunity_keys, prepare_markets
from app.services.spread_engine import _market_observed_at

VARIANTS = ("conv-0.1", "conv-half")
STRATEGY_VERSION = "shadow-v2"


def liquidity_tier(buy, sell):
    if buy is None or sell is None:
        return "unknown"
    volume = min(buy, sell)
    if volume > 50_000_000:
        return "gt50k"
    if volume >= 5_000_000:
        return "5k-50k"
    return "500-5k" if volume >= 500_000 else "lt500"


def spread_tier(value):
    for upper, label in ((0.2, "<0.2%"), (0.3, "0.2–0.3%"), (0.5, "0.3–0.5%"),
                         (1, "0.5–1%"), (2, "1–2%"), (5, "2–5%")):
        if value < upper:
            return label
    return "2–5%" if value == 5 else ">5%"


def market_key_json(key):
    return {"exchange": key[0], "market_type": key[1], "raw_symbol": key[2], "dex": key[3] or None,
            "price_multiplier": float(key[4]), "contract_size_multiplier": float(key[5]) if len(key) > 5 else None}


def utc_iso(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat()


def funding_state(market):
    return {
        "next": market.funding_next_time.timestamp() if market.funding_next_time else None,
        "rate": market.funding_rate_pct,
        "interval": (market.funding_interval_hours or 0) * 3600,
        "last": None,
        "spot": market.market_type == "spot",
    }


class TradeRow(dict):
    """Variants share immutable entry metadata; only accounting fields diverge."""
    __slots__ = ("common",)

    def __init__(self, common, **values):
        super().__init__(values)
        self.common = common

    def __missing__(self, key):
        return self.common[key]

    def payload(self):
        return {**self.common, **self}


class PendingRows(Mapping):
    """Retain owned rows, materializing at most one write chunk at a time."""
    def __init__(self):
        self.rows = {}

    def queue(self, row, position):
        self.rows[row["id"]] = (row, position)

    def __getitem__(self, key):
        row, position = self.rows[key]
        payload = row.payload() if isinstance(row, TradeRow) else dict(row)
        payload["tracking"] = {**position.state(), "funding": payload.pop("_funding")}
        return payload

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)

    def __delitem__(self, key):
        del self.rows[key]


@dataclass(slots=True)
class Position:
    keys: tuple
    opened: float
    open_spread: float
    last_prices: list
    last_seen: list
    rows: list = field(default_factory=list)
    funding_due: float | None = None

    def refresh_funding_due(self):
        self.funding_due = min((s["next"] for r in self.rows for s in r["_funding"]
                                if s["next"] is not None), default=None)

    def state(self):
        return {"keys": self.keys, "opened": self.opened, "open_spread": self.open_spread,
                "last_prices": self.last_prices, "last_seen": self.last_seen}


class ShadowLedger:
    def __init__(self, settings):
        self.enabled = settings.shadow_enabled
        self.minimum = settings.shadow_min_open_pct
        self.maximum = settings.shadow_max_open
        self.slippage = json.loads(settings.shadow_slippage_bps)
        self.positions = {}
        self.cooldowns = {}
        self.open_count = 0
        self.dropped = 0
        self.dropped_by_reason = dict.fromkeys(("capacity", "liquidity", "market_type", "symbol"), 0)
        self.baseline_skipped = 0
        self.baseline_pending = True
        self.last_above = {}
        self.retry_events = set()
        # Quotas count variant rows, including partially closed/restored pairs.
        self.liquidity_limits = {"lt500": self.maximum * 30 // 100, "unknown": self.maximum // 10}
        self.spot_limit = self.maximum // 5
        self.liquidity_counts = defaultdict(int)
        self.symbol_counts = defaultdict(int)
        self.spot_count = 0
        self.pending = PendingRows()
        self.checkpoint_at = 0.0
        self.by_market = defaultdict(set)
        self.changes = MarketChanges()
        self.deadlines = {}
        self.timers = []
        self.recomputed = 0

    def restore(self, rows, cooldowns):
        positions = {}
        for row in rows:
            state = row.pop("tracking")
            row["_funding"] = state.pop("funding")
            key = row["opportunity_id"]
            if key not in positions:
                state["keys"] = tuple(tuple(k) for k in state["keys"])
                positions[key] = Position(**state)
            positions[key].rows.append(row)
        self.positions = positions
        self.open_count = len(rows)
        self.cooldowns = cooldowns
        # Crossing state belongs to this process, never persisted in trade rows.
        self.baseline_pending = True
        self.last_above.clear()
        self.retry_events.clear()
        self.liquidity_counts.clear()
        self.symbol_counts.clear()
        self.spot_count = 0
        for row in rows:
            self._count_row(row, 1)
        self.by_market.clear()
        self.deadlines.clear()
        self.timers.clear()
        for key, position in positions.items():
            position.refresh_funding_due()
            for market in position.keys:
                self.by_market[market].add(key)
            self._schedule(key, position, position.opened)

    def _count_row(self, row, delta):
        for counts, key in ((self.liquidity_counts, row["liquidity"]),
                            (self.symbol_counts, row["symbol"])):
            counts[key] += delta
            if not counts[key]:
                del counts[key]
        if row["buy_market_key"]["market_type"] == row["sell_market_key"]["market_type"] == "spot":
            self.spot_count += delta

    def _quota_reason(self, op, tier):
        # One event has one primary refusal reason, in total/category order.
        size = len(VARIANTS)
        if self.open_count + size > self.maximum:
            return "capacity"
        if tier in self.liquidity_limits and self.liquidity_counts.get(tier, 0) + size > self.liquidity_limits[tier]:
            return "liquidity"
        if op.buy_market_type == op.sell_market_type == "spot" and self.spot_count + size > self.spot_limit:
            return "market_type"
        if self.symbol_counts.get(op.symbol, 0) + size > 6:
            return "symbol"
        return None

    def _schedule(self, key, position, ts):
        due = min(min(position.last_seen) + 600, position.opened + 172800)
        if ts < position.opened + 86400:
            due = min(due, position.opened + 86400)
        if position.funding_due is not None and position.funding_due > ts:
            due = min(due, position.funding_due)
        self.deadlines[key] = due
        heapq.heappush(self.timers, (due, key))

    def _queue(self, position, rows=None):
        for row in position.rows if rows is None else rows:
            self.pending.queue(row, position)

    @staticmethod
    def _funding(opened, schedules, legs, now):
        total, unknown = 0.0, 0
        if all(s["next"] is None and (m is None or m.funding_next_time is None)
               for s, m in zip(schedules, legs)):
            return total, unknown
        for index, (schedule, market) in enumerate(zip(schedules, legs)):
            if schedule["spot"]:
                continue
            due, rate, interval = schedule["next"], schedule["rate"], schedule["interval"]
            if (market is not None and due is not None and market.funding_next_time is not None
                    and market.funding_next_time.timestamp() == due
                    and 0 <= due - _market_observed_at(market).timestamp() <= 10):
                rate = market.funding_rate_pct
            # Use the rate observed for this settlement, never the next cycle's
            # rate published after it. Last-known rates across gaps are estimates.
            if due is not None and opened < due <= now:
                count = 1 + int((now - due) // interval) if interval > 0 else 1
                if rate is None:
                    unknown += count
                else:
                    total += (-1 if index == 0 else 1) * rate * 100 * count
                schedule["last"] = due + (count - 1) * interval
                schedule["next"] = due + count * interval if interval > 0 else None
            elif due is not None and due <= opened:
                # Opening exactly on a settlement does not earn it, but the
                # schedule must still advance rather than becoming stuck.
                schedule["next"] = due + interval if interval > 0 else None
            if market is not None and 0 <= now - _market_observed_at(market).timestamp() <= 10:
                upcoming = market.funding_next_time.timestamp() if market.funding_next_time else None
                if upcoming is not None and upcoming > now and (schedule["last"] is None or upcoming > schedule["last"]):
                    schedule["next"] = upcoming
                    schedule["rate"] = market.funding_rate_pct
                    schedule["interval"] = (market.funding_interval_hours or 0) * 3600
        return total, unknown

    def process(self, markets, opportunities, now, annotations=None, prepared=None, changed=None,
                below_threshold=()):
        if not self.enabled:
            return
        ts = now.timestamp()
        prepared = prepared if prepared is not None else prepare_markets(markets, now)
        self.cooldowns = {k: v for k, v in self.cooldowns.items() if ts - v < 600}
        # Keep only the last 60 seconds of reached-threshold ids, not lifetime
        # history. A missing observation does not assert a below-threshold price;
        # pending quote retries survive short gaps, but explicit low quotes cancel.
        self.last_above = {k: v for k, v in self.last_above.items() if ts - v < 60}
        self.retry_events.intersection_update(self.last_above)
        self.retry_events.difference_update(below_threshold)
        checkpoint = ts >= self.checkpoint_at
        changed = self.changes.update(prepared) if changed is None else changed
        affected = set()
        for key in changed:
            affected.update(self.by_market.get(key, ()))
        while self.timers and self.timers[0][0] <= ts:
            due, key = heapq.heappop(self.timers)
            if self.deadlines.get(key) == due:
                affected.add(key)
        self.recomputed = len(affected)
        for op_id in affected:
            position = self.positions.get(op_id)
            if position is None:
                continue
            buy_info, sell_info = prepared.get(position.keys[0]), prepared.get(position.keys[1])
            buy_fresh, sell_fresh = buy_info is not None and buy_info[2], sell_info is not None and sell_info[2]
            if buy_fresh:
                position.last_prices[0] = buy_info[0].bid
                position.last_seen[0] = buy_info[1]
            if sell_fresh:
                position.last_prices[1] = sell_info[0].ask
                position.last_seen[1] = sell_info[1]
            stale = ts - min(position.last_seen) >= 600
            buy_bid, sell_ask = position.last_prices
            close = 200 * (sell_ask - buy_bid) / (buy_bid + sell_ask)
            holding = ts - position.opened
            legs = (buy_info[0] if buy_info else None, sell_info[0] if sell_info else None)
            funding = unknown = 0
            if (position.funding_due is not None or (legs[0] is not None and legs[0].funding_next_time)
                    or (legs[1] is not None and legs[1].funding_next_time)):
                funding_now = min(ts, min(position.last_seen) + 600)
                for row in position.rows:
                    delta, missing = self._funding(position.opened, row["_funding"], legs, funding_now)
                    row["funding_bps"] += delta
                    row["funding_unknown_settlements"] += missing
                    funding += abs(delta)
                    unknown += missing
                position.refresh_funding_due()
            # The common steady-state path needs no row allocations or writes.
            # Both variants remain open above their maximum convergence threshold.
            if (not stale and buy_fresh and sell_fresh and not funding and not unknown
                    and holding < 86400 and max(0.1, position.open_spread / 2) < close
                    < position.open_spread + max(position.open_spread, 1)):
                self._schedule(op_id, position, ts)
                continue
            remaining = []
            closed = []
            for row in position.rows:
                reason = None
                if stale:
                    reason = "stale"
                elif buy_fresh and sell_fresh:
                    if close >= position.open_spread + max(position.open_spread, 1):
                        reason = "stop"
                    elif close <= (0.1 if row["variant"] == "conv-0.1" else position.open_spread / 2):
                        reason = "converged"
                    elif holding >= 48 * 3600:
                        reason = "timeout_hard"
                    elif holding >= 24 * 3600 and close <= position.open_spread / 2:
                        reason = "timeout"
                # Hard timeout also applies while a leg is briefly missing.
                elif holding >= 48 * 3600:
                    reason = "timeout_hard"
                if reason:
                    row.update(closed_at=utc_iso(ts), close_spread_pct=close, exit_reason=reason,
                               holding_seconds=holding, spread_bps=(position.open_spread - close) * 100)
                    row["net_bps"] = row["spread_bps"] - row["slippage_bps"] - row["fees_bps"] + row["funding_bps"]
                    closed.append(row)
                    self.open_count -= 1
                    self._count_row(row, -1)
                    self.cooldowns[op_id] = ts
                else:
                    remaining.append(row)
            if closed:
                self._queue(position, closed)
            position.rows = remaining
            if not remaining:
                del self.positions[op_id]
                self.deadlines.pop(op_id, None)
                for key in position.keys:
                    self.by_market[key].discard(op_id)
                    if not self.by_market[key]:
                        del self.by_market[key]
            else:
                self._schedule(op_id, position, ts)
                if funding or unknown:
                    self._queue(position)
        if checkpoint:
            for position in self.positions.values():
                self._queue(position)
            self.checkpoint_at = ts + 60
        for op in opportunities:
            if op.open_spread_pct < self.minimum:
                self.retry_events.discard(op.id)
                continue
            crossing = op.id not in self.last_above
            self.last_above[op.id] = ts
            if self.baseline_pending:
                self.baseline_skipped += int(crossing)
                continue
            if crossing:
                self.retry_events.add(op.id)
            if op.id in self.positions or op.id in self.cooldowns:
                self.retry_events.discard(op.id)
                continue
            if op.id not in self.retry_events:
                continue
            tier = liquidity_tier(op.buy_volume_24h_usdt, op.sell_volume_24h_usdt)
            reason = self._quota_reason(op, tier)
            if reason:
                self.retry_events.discard(op.id)
                self.dropped += 1
                self.dropped_by_reason[reason] += 1
                continue
            keys = opportunity_keys(op)
            info = [prepared.get(k) for k in keys]
            if any(i is None or not i[2] for i in info):
                continue
            legs = [i[0] for i in info]
            ages = [ts - i[1] for i in info]
            if any(age < 0 or age > 10 for age in ages):
                continue
            fields = annotations.get(op.id, {}) if annotations else {}
            routes = [fields.get("buy_routes", op.buy_routes), fields.get("sell_routes", op.sell_routes)]
            common = dict(
                opportunity_id=op.id, symbol=op.symbol, buy_market_key=market_key_json(keys[0]),
                sell_market_key=market_key_json(keys[1]), opened_at=utc_iso(ts), closed_at=None,
                open_spread_pct=op.open_spread_pct, close_spread_pct=None, exit_reason=None,
                holding_seconds=None, spread_bps=None, slippage_bps=4 * self.slippage[tier],
                fees_bps=2 * (op.buy_fee_pct + op.sell_fee_pct) * 100,
                funding_bps=0.0, net_bps=None, funding_unknown_settlements=0, funding_estimated=True,
                pair=f"{keys[0][0]}{('/' + keys[0][3]) if keys[0][3] else ''} → {keys[1][0]}{('/' + keys[1][3]) if keys[1][3] else ''}",
                spread_tier=spread_tier(op.open_spread_pct), liquidity=tier,
                routes="/".join("+".join(r) if r else "none" for r in routes),
                strategy_version=STRATEGY_VERSION, buy_quote_age_seconds=ages[0], sell_quote_age_seconds=ages[1],
                fake_score=fields.get("fake_score", op.fake_score), fake_reasons=list(fields.get("fake_reasons", op.fake_reasons)),
                fake_flag=fields.get("fake_flag", op.fake_flag), executable=fields.get("executable", op.executable),
            )
            position = Position(keys, ts, op.open_spread_pct,
                                [legs[0].bid, legs[1].ask], [ts - a for a in ages],
                                [TradeRow(common, id=uuid4().hex, variant=v,
                                          _funding=[funding_state(m) for m in legs]) for v in VARIANTS])
            for row in position.rows:
                self._funding(ts, row["_funding"], legs, ts)
            position.refresh_funding_due()
            self.positions[op.id] = position
            for key in keys:
                self.by_market[key].add(op.id)
            self._schedule(op.id, position, ts)
            self.open_count += len(VARIANTS)
            for row in position.rows:
                self._count_row(row, 1)
            self.retry_events.discard(op.id)
            self._queue(position)

        self.baseline_pending = False

        # Replaced deadlines must not accumulate across millions of quote ticks.
        if len(self.timers) > max(64, 2 * len(self.positions)):
            self.timers = [(due, key) for key, due in self.deadlines.items()]
            heapq.heapify(self.timers)

    async def flush(self, repository):
        # Serialization and this coroutine run on the runtime's worker loop.
        # Only successful chunks leave pending, so retries remain bounded/idempotent.
        keys = list(self.pending)
        for offset in range(0, len(keys), 200):
            batch = keys[offset:offset + 200]
            await repository.save_batch([self.pending[key] for key in batch])
            for key in batch:
                del self.pending[key]
