"""Bounded asynchronous scheduler; one SQLite writer, positions first."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
import time
from .core import (AMOUNTS, EXCHANGES, Market, Spec, Book, timestamp, opening,
                   closing, track_interval, walk, dec)
from .exchanges import endpoint, parse_book, parse_spec
from .network import RateLimited


def pair_key(payload):
    # Symmetric venue pair prevents immediate reverse re-entry. Retain exact markets separately.
    return payload["symbol"] + "|" + "|".join(sorted(payload[k]["exchange"] for k in ("buy_market_key", "sell_market_key")))


def trigger_reason(payload, now):
    try:
        opened = timestamp(payload["opened_at"])
        if now - opened > 120:
            return "skipped_stale_trigger"
        if opened > now + 5:
            return "skipped_future_trigger"
        if (payload.get("strategy_version") != "shadow-v2" or payload.get("variant") != "conv-half"
                or payload.get("spread_tier") not in ("0.5–1%", "1–2%", "2–5%", ">5%")
                or payload.get("liquidity") not in ("gt50k", "5k-50k")
                or payload.get("fake_flag") is not False):
            return "skipped_filter"
        if not isinstance(payload["symbol"], str) or not payload["symbol"]:
            return "invalid_trigger"
        markets = [Market.from_payload(payload[k]) for k in ("buy_market_key", "sell_market_key")]
        if markets[0].key == markets[1].key:
            return "same_market"
    except (KeyError, ValueError, TypeError, AttributeError):
        return "invalid_trigger"
    return None


def funding_snapshot(payload, markets):
    # Public shadow payload normally omits internal tracking funding. Optional
    # explicit snapshots use ratio/seconds; never reuse mutable closing estimates.
    supplied = payload.get("fill_sim_funding")
    snapshots = []
    for i, m in enumerate(markets):
        if m.market_type == "spot":
            snapshots.append({"spot": True})
        elif isinstance(supplied, list) and len(supplied) == 2:
            s = supplied[i]
            try:
                valid = isinstance(s, dict) and dec(s["interval"]) > 0
                if valid:
                    s = dict(rate=str(dec(s["rate"])), next=float(dec(s["next"])), interval=float(dec(s["interval"])))
                snapshots.append(s if valid else None)
            except (KeyError, ValueError, TypeError):
                snapshots.append(None)
        else:
            snapshots.append(None)
    return snapshots


class Engine:
    def __init__(self, store, client, fees, clock=time.time, executor=None):
        self.store, self.client, self.fees, self.clock = store, client, fees, clock
        self.executor = executor or ThreadPoolExecutor(max_workers=4, thread_name_prefix="public-depth")
        store.recover()
        self.positions = {p["id"]: p for p in store.open_positions()}
        for p in self.positions.values():
            p.setdefault("failed_since", [None, None])
            for i, last in enumerate(p["last_good"]):
                if clock() - last > 60 and p["failed_since"][i] is None:
                    p["failed_since"][i] = last
        self.pending, self.jobs, self.spec_jobs = {}, {}, {}
        self.cache = store.meta("spec_cache", {})
        self.want_specs = {}
        self.spec_retry = {}
        # Refresh all persisted instruments on startup; bounded incremental work.
        for value in self.cache.values():
            m = Market(**value["market"])
            self.want_specs[m.key] = m
        for ex in EXCHANGES:
            raw = "BTC_USDT" if ex == "gate" else ("BTC-USDT-SWAP" if ex == "okx" else "BTCUSDT")
            m = Market(ex, "future", raw)
            self.want_specs.setdefault(m.key, m)
        self.last_tick = clock()

    def spec(self, market):
        value = self.cache.get(market.key)
        if not value or self.clock() - value["at"] >= 21600:
            self.want_specs[market.key] = market
            return None
        spec = Spec(**value["spec"])
        spec.validate(market)
        return spec

    def active_events(self):
        return {p["event_id"] for p in self.positions.values()} | set(self.pending)

    def _inflight(self):
        return sum(len(job["futures"]) for job in self.jobs.values()) + len(self.spec_jobs)

    def _fetch_book(self, market, spec):
        raw = self.client.fetch(market.exchange, endpoint(market, "depth"), reserved=True)
        return parse_book(market, spec, raw, self.clock())

    def _submit(self, key, kind, event, markets, specs):
        if self._inflight() + 2 > 4 or not self.client.limiter.acquire([m.exchange for m in markets]):
            return False
        futures = [self.executor.submit(self._fetch_book, m, s) for m, s in zip(markets, specs)]
        self.jobs[key] = dict(kind=kind, event=event, futures=futures)
        return True

    def ingest(self, payload):
        now = self.clock()
        identity = payload.get("id")
        if not isinstance(identity, str) or not identity:
            identity = "invalid:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if self.store.seen(identity):
            # Update matched shadow outcomes when the API first exposes a close.
            if payload.get("closed_at") is not None and payload.get("net_bps") is not None:
                self._update_shadow(identity, payload["net_bps"])
            return
        reason = trigger_reason(payload, now)
        event = dict(id=identity, payload=payload, t0=None, t1=now, status=reason or "pending", filter_passed=reason is None)
        try:
            event["t0"] = timestamp(payload["opened_at"])
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
        if reason is None:
            markets = [Market.from_payload(payload[k]) for k in ("buy_market_key", "sell_market_key")]
            key = pair_key(payload)
            active_keys = {p["pair_key"] for p in self.positions.values()} | {p["pair_key"] for p in self.pending.values()}
            event.update(pair_key=key, markets=[asdict(m) for m in markets])
            if key in active_keys:
                reason = "skipped_duplicate_position"
            elif len(self.active_events()) >= 10:
                reason = "skipped_capacity"
            else:
                try:
                    specs = []
                    for leg, m in zip(("buy", "sell"), markets):
                        try:
                            specs.append(self.spec(m))
                        except ValueError:
                            event["spec_error_leg"] = leg
                            raise
                    if any(s is None for s in specs):
                        reason = "awaiting_specs"
                        event["spec_deadline"] = now + 6
                        self.pending[identity] = event
                    elif specs[0].base != specs[1].base:
                        reason = "spec_mismatch"
                    elif any(self.fees.get(m.exchange, {}).get(m.market_type) is None for m in markets):
                        reason = "fee_unconfigured"
                except ValueError as exc:
                    reason = str(exc)
            if reason is None:
                event["specs"] = [asdict(s) for s in specs]
                if self.tracking_due() or not self._submit(identity, "first", event, markets, specs):
                    reason = "skipped_rate_limit"
                else:
                    self.pending[identity] = event
        event["status"] = reason or "pending"
        with self.store.transaction():
            self.store.event(event)

    def _update_shadow(self, identity, net_bps):
        try:
            from .core import dec
            value = float(dec(net_bps))
        except (ValueError, ArithmeticError):
            return
        if self.store.meta("shadow:" + identity) == value:
            return
        # Closed events may already be archived. No implicit archive mutation:
        # persist a small outcome map and join it during reporting.
        with self.store.transaction():
            self.store.set_meta("shadow:" + identity, value)
            for p in self.positions.values():
                if p["event_id"] == identity:
                    p["shadow_net_bps"] = value
                    self.store.position(p)

    def _variants(self, event, variant, books):
        payload, now = event["payload"], self.clock()
        markets = [Market(**m) for m in event["markets"]]
        specs = [Spec(**s) for s in event["specs"]]
        for amount in AMOUNTS:
            f = opening(*books, *specs, amount, *(self.fees[m.exchange][m.market_type] for m in markets))
            f.update(id="%s:%s:%d" % (event["id"], variant, amount), event_id=event["id"], variant=variant,
                     symbol=payload["symbol"], pair="|".join(sorted(m.exchange for m in markets)),
                     spread_tier=payload["spread_tier"], t0=event["t0"], t1=event["t1"],
                     tA=books[0].received_at, tB=books[1].received_at,
                     exchange_tA=books[0].exchange_at, exchange_tB=books[1].exchange_at,
                     markets=event["markets"], specs=event["specs"])
            self.store.fill(f)
            if f["status"] == "opened":
                p = dict(id=f["id"], event_id=event["id"], variant=variant, amount=amount,
                         symbol=f["symbol"], pair=f["pair"], spread_tier=f["spread_tier"],
                         pair_key=event["pair_key"], fill=f, markets=event["markets"], specs=event["specs"],
                         opened_at=now, next_track=now+3, closed_at=None,
                         last_books=[b.to_dict() for b in books],
                         last_good=[b.received_at for b in books],
                         failed_since=[None, None],
                         funding=funding_snapshot(payload, markets),
                         shadow_net_bps=payload.get("net_bps") if payload.get("closed_at") else None)
                self.positions[p["id"]] = p
                self.store.position(p)

    def _failed_variants(self, event, variants, reason):
        for variant in variants:
            for amount in AMOUNTS:
                self.store.fill(dict(id="%s:%s:%d" % (event["id"], variant, amount),
                                     event_id=event["id"], variant=variant, amount=amount,
                                     status=reason, t1=event["t1"]))

    def _finish_jobs(self):
        for key, job in list(self.jobs.items()):
            if not all(f.done() for f in job["futures"]):
                continue
            del self.jobs[key]
            books, errors, rate_limited = [], [], False
            for f in job["futures"]:
                try:
                    books.append(f.result())
                except RateLimited:
                    books.append(None)
                    errors.append("RateLimited")
                    rate_limited = True
                except (OSError, ValueError, KeyError, IndexError, TypeError, ArithmeticError) as exc:
                    books.append(None)
                    errors.append(type(exc).__name__)
                except Exception as exc:
                    # Worker failure is accounted for, never logged with URLs/headers.
                    books.append(None)
                    errors.append(type(exc).__name__)
            event = job["event"]
            with self.store.transaction():
                if job["kind"] == "track":
                    for p in list(self.positions.values()):
                        if p["event_id"] == key:
                            self._track_position(p, books)
                elif errors:
                    variants = ("measured", "plus_1s", "leg_delay") if job["kind"] == "first" else ("plus_1s", "leg_delay")
                    reason = "skipped_rate_limit" if rate_limited else "book_unavailable"
                    self._failed_variants(event, variants, reason)
                    event.update(status=reason, error_types=errors)
                    self.store.event(event)
                    self.pending.pop(key, None)
                elif job["kind"] == "first":
                    self._variants(event, "measured", books)
                    event.update(status="measured", first_books=[b.to_dict() for b in books],
                                 tA=books[0].received_at, tB=books[1].received_at,
                                 delayed_due=max(b.received_at for b in books)+1)
                    self.store.event(event)
                else:
                    self._variants(event, "plus_1s", books)
                    self._variants(event, "leg_delay", [Book(**event["first_books"][0]), books[1]])
                    event.update(status="evaluated", delayed_tA=books[0].received_at, delayed_tB=books[1].received_at)
                    self.store.event(event)
                    self.pending.pop(key, None)

    def _track_position(self, p, books):
        now = self.clock()
        already_stale = any(t is not None and now-t >= 60 for t in p.get("failed_since", [None,None]))
        if already_stale:
            books = [None, None]
        failed_since = p.setdefault("failed_since", [None, None])
        for i, book in enumerate(books):
            if book is not None:
                p["last_books"][i] = book.to_dict()
                p["last_good"][i] = book.received_at
                failed_since[i] = None
            elif failed_since[i] is None:
                failed_since[i] = now
        stale = any(t is not None and now - t >= 60 for t in failed_since)
        p["next_track"] = now + track_interval(now-p["opened_at"])
        result = None
        if stale or all(books):
            effective = [Book(**b) for b in p["last_books"]]
            try:
                # Save an executable exit snapshot for forced closure when depth disappears.
                for book, side in zip(effective, ("bids", "asks")):
                    walk(getattr(book, side), quantity=p["fill"]["quantity"])
                p["last_executable_exit"] = [b.to_dict() for b in effective]
                result = closing(p, *effective, now, stale)
            except ValueError:
                p["last_exit_error"] = "too_thin"
        if result is None and (stale or now-p["opened_at"] >= 172800):
            if p.get("last_executable_exit"):
                result = closing(p, *(Book(**b) for b in p["last_executable_exit"]), now, True)
                result["forced_exit_reason"] = "stale_exit" if stale else "timeout_48h"
            else:
                result = dict(closed_at=now, holding_seconds=now-p["opened_at"], exit_reason="unpriced_exit",
                              main_eligible=False, funding_known=False, net_bps=None, net_usd=None)
        if result:
            p.update(result)
            self.positions.pop(p["id"], None)
        self.store.position(p)

    def tracking_due(self):
        now = self.clock()
        return any(p["next_track"] <= now and p["event_id"] not in self.jobs for p in self.positions.values())

    def tick(self):
        now = self.clock()
        self._finish_jobs()
        # Stale checks do not depend on request admission or an exchange unpausing.
        with self.store.transaction():
            for p in list(self.positions.values()):
                if any(t is not None and now - t >= 60 for t in p.get("failed_since", [None, None])):
                    self._track_position(p, [None, None])
        groups = {}
        for p in sorted(self.positions.values(), key=lambda p: p["next_track"]):
            if p["next_track"] <= now:
                groups.setdefault(p["event_id"], p)
        for key, p in groups.items():
            if key not in self.jobs:
                admitted = self._submit(key, "track", None, [Market(**m) for m in p["markets"]], [Spec(**s) for s in p["specs"]])
                if not admitted:
                    with self.store.transaction():
                        for row in self.positions.values():
                            if row["event_id"] == key:
                                row["failed_since"] = [t if t is not None else now for t in row.get("failed_since", [None, None])]
                                self.store.position(row)
        for key, event in list(self.pending.items()):
            if event["status"] == "awaiting_specs":
                markets = [Market(**m) for m in event["markets"]]
                reason = None
                try:
                    specs = []
                    for leg, m in zip(("buy", "sell"), markets):
                        try:
                            specs.append(self.spec(m))
                        except ValueError:
                            event["spec_error_leg"] = leg
                            raise
                    if any(s is None for s in specs):
                        if now < event["spec_deadline"]:
                            continue
                        reason = "spec_unavailable"
                    elif specs[0].base != specs[1].base:
                        reason = "spec_mismatch"
                    elif any(self.fees.get(m.exchange, {}).get(m.market_type) is None for m in markets):
                        reason = "fee_unconfigured"
                    elif now-event["t0"] > 120:
                        reason = "skipped_stale_trigger"
                    else:
                        event["specs"] = [asdict(s) for s in specs]
                        if self.tracking_due() or not self._submit(key, "first", event, markets, specs):
                            reason = "skipped_rate_limit"
                except ValueError as exc:
                    reason = str(exc)
                with self.store.transaction():
                    event["status"] = reason or "pending"
                    self.store.event(event)
                    if reason:
                        self._failed_variants(event, ("measured", "plus_1s", "leg_delay"), reason)
                        self.pending.pop(key, None)
                continue
            if key in self.jobs or event.get("delayed_due", float("inf")) > now:
                continue
            if self.tracking_due() or not self._submit(key, "delayed", event, [Market(**m) for m in event["markets"]], [Spec(**s) for s in event["specs"]]):
                with self.store.transaction():
                    self._failed_variants(event, ("plus_1s", "leg_delay"), "skipped_rate_limit")
                    event["status"] = "delayed_rate_limit"
                    self.store.event(event)
                self.pending.pop(key, None)
        self._refresh_specs()

    def _refresh_specs(self):
        for key, (m, future) in list(self.spec_jobs.items()):
            if not future.done():
                continue
            del self.spec_jobs[key]
            try:
                spec = parse_spec(m, future.result())
                self.cache[key] = dict(at=self.clock(), spec=asdict(spec), market=asdict(m))
                self.want_specs.pop(key, None)
            except Exception:
                # Retry no faster than 60s; cache remains stale and unusable for opens.
                self.spec_retry[key] = self.clock() + 60
                self.want_specs[key] = m
            with self.store.transaction():
                self.store.set_meta("spec_cache", self.cache)
        if not hasattr(self, "spec_retry"):
            self.spec_retry = {}
        for key, value in list(self.cache.items()):
            if self.clock() - value["at"] >= 21600:
                self.want_specs[key] = Market(**value["market"])
        if self.tracking_due():
            return
        pending_keys = {Market(**m).key for e in self.pending.values() for m in e.get("markets", [])}
        for key, m in sorted(self.want_specs.items(), key=lambda item: item[0] not in pending_keys):
            if key in self.spec_jobs or self.spec_retry.get(key, 0) > self.clock():
                continue
            if self._inflight() >= 4 or not self.client.limiter.acquire([m.exchange]):
                continue
            future = self.executor.submit(self.client.fetch, m.exchange, endpoint(m, "spec"), True)
            self.spec_jobs[key] = (m, future)
            del self.want_specs[key]

    def close(self):
        self.executor.shutdown(wait=True)
        self._finish_jobs()
