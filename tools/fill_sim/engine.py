"""Bounded asynchronous scheduler; one SQLite writer, positions first."""
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
import time
from .core import (AMOUNTS, EXCHANGES, Market, Spec, Book, timestamp, opening,
                   closing, track_interval, walk, dec)
from .exchanges import endpoint, parse_book, parse_spec
from .network import RateLimited
from .funding import TTL, queue_funding
from .scheduler import DaemonPool, RequestQueue


def pair_key(payload):
    # Symmetric venue pair prevents immediate reverse re-entry. Retain exact markets separately.
    return payload["symbol"] + "|" + "|".join(sorted(payload[k]["exchange"] for k in ("buy_market_key", "sell_market_key")))


SPEC_CAPACITY = 256
SPEC_IDLE_TTL = 21600


def business_reason(payload):
    """Eligibility is independent of when/whether execution can run."""
    try:
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


def trigger_reason(payload, now):
    reason = business_reason(payload)
    if reason:
        return reason
    try:
        opened = timestamp(payload["opened_at"])
    except (KeyError, ValueError, TypeError, AttributeError):
        return "invalid_trigger"
    if now-opened > 120:
        return "skipped_stale_trigger"
    if opened > now+5:
        return "skipped_future_trigger"
    return None


class Engine:
    def __init__(self, store, client, fees, clock=time.time, executor=None):
        self.store, self.client, self.fees, self.clock = store, client, fees, clock
        self.executor = executor or DaemonPool(max_workers=4, thread_name_prefix="public-depth")
        self.requests = RequestQueue(client, clock)
        store.recover()
        self.positions = {p["id"]: p for p in store.open_positions()}
        for p in self.positions.values():
            p.setdefault("failed_since", [None, None])
            for i, last in enumerate(p["last_good"]):
                if clock() - last > 60 and p["failed_since"][i] is None and p.get("tracking_status") != "rate_limited_delay":
                    p["failed_since"][i] = last
        self.pending, self.jobs, self.spec_jobs = {}, {}, {}
        self.funding_cache, self.funding_jobs = {}, {}
        self.cache = store.meta("spec_cache", {})
        self.want_specs = {}
        self.spec_retry = {}
        self.spec_used = {}
        self._prune_specs()
        # Refresh retained instruments on startup; bounded incremental work.
        for value in self.cache.values():
            m = Market(**value["market"])
            self.want_specs[m.key] = m
        for ex in EXCHANGES:
            raw = "BTC_USDT" if ex == "gate" else ("BTC-USDT-SWAP" if ex == "okx" else "BTCUSDT")
            m = Market(ex, "future", raw)
            self.want_specs.setdefault(m.key, m)
        self.spec_used.update({k: clock() for k in self.want_specs})
        self._prune_specs()
        self.last_tick = clock()

    def spec(self, market):
        self.spec_used[market.key] = self.clock()
        value = self.cache.get(market.key)
        if value:
            value["used_at"] = self.clock()
        if not value or self.clock() - value["at"] >= 21600:
            self.want_specs[market.key] = market
            self._prune_specs()
            return None
        spec = Spec(**value["spec"])
        spec.validate(market)
        return spec

    def active_events(self):
        return {p["event_id"] for p in self.positions.values()} | set(self.pending)

    def _inflight(self):
        return sum(len(job["futures"]) for job in self.jobs.values()) + len(self.spec_jobs) + len(self.funding_jobs)

    def _fetch_book(self, market, spec, priority=1):
        return self.requests.submit(market.exchange, endpoint(market, "depth"), priority,
                                    lambda raw: parse_book(market, spec, raw, self.clock()))

    def _submit(self, key, kind, event, markets, specs):
        futures = [self._fetch_book(m, s, 0 if kind == "track" else 1)
                   for m, s in zip(markets, specs)]
        self.jobs[key] = dict(kind=kind, event=event, futures=futures, accounted=[0., 0.], observed=[False, False])
        return True

    def _cached_funding(self, m):
        if m.market_type == "spot":
            return dict(at=self.clock(), snapshot={"spot": True}, error=None)
        value = self.funding_cache.get(m.key)
        if value and 0 <= self.clock() - value["at"] < TTL:
            snapshot = value["snapshot"]
            if snapshot is None or snapshot["next"] > self.clock():
                return value
        return None

    def _start_open(self, event, markets, specs):
        values = [self._cached_funding(m) for m in markets]
        if all(v is not None for v in values):
            event["funding"] = [v["snapshot"] for v in values]
            event["funding_errors"] = [v.get("error") for v in values]
            admitted = self._submit(event["id"], "first", event, markets, specs)
            if admitted:
                event["status"] = "pending"
            return admitted
        event.update(status="awaiting_funding", funding_deadline=self.clock()+7)
        return True

    def _finish_funding(self):
        now = self.clock()
        needed = {Market(**m).key for e in self.pending.values()
                  if e.get("status") == "awaiting_funding" for m in e["markets"]}
        for key, future in list(self.funding_jobs.items()):
            if not future.done():
                if key not in needed:
                    future.cancel()
                    del self.funding_jobs[key]
                continue
            del self.funding_jobs[key]
            try:
                snapshot, error = future.result(), None
            except Exception as exc:
                snapshot, error = None, type(exc).__name__
            self.funding_cache[key] = dict(at=snapshot["captured_at"] if snapshot else now,
                                          snapshot=snapshot, error=error)
        self.funding_cache = {k:v for k,v in self.funding_cache.items() if 0 <= now-v["at"] < TTL}
        # Bound memory even during a very large unique-market trigger burst.
        if len(self.funding_cache) > 2048:
            self.funding_cache = dict(sorted(self.funding_cache.items(), key=lambda kv: kv[1]["at"])[-2048:])

    def _advance_funding(self, event):
        markets = [Market(**m) for m in event["markets"]]
        now = self.clock()
        values = [self._cached_funding(m) for m in markets]
        ready = all(v is not None for v in values)
        expired = now >= event["funding_deadline"]
        if not ready and not expired:
            for m, value in zip(markets, values):
                if value is None and m.key not in self.funding_jobs:
                    self.funding_jobs[m.key] = queue_funding(self.requests, m, self.clock)
        if not (ready or expired):
            return
        event["funding"] = [v["snapshot"] if v else None for v in values]
        event["funding_errors"] = [v.get("error") if v else "funding_timeout_or_budget" for v in values]
        specs = [Spec(**s) for s in event["specs"]]
        # Funding and opening depth use the same fair queue of actual sends.
        if now-event["t0"] > 120:
            reason = "skipped_stale_trigger"
        elif self._submit(event["id"], "first", event, markets, specs):
            reason = None
        elif now < event["funding_deadline"]+1:
            return
        else:
            reason = "skipped_rate_limit"
        with self.store.transaction():
            event["status"] = reason or "pending"
            self.store.event(event)
            if reason:
                self._failed_variants(event, ("measured", "plus_1s", "leg_delay"), reason)
                self.pending.pop(event["id"], None)

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
        event = dict(id=identity, payload=payload, t0=None, t1=now, status=reason or "pending", filter_passed=business_reason(payload) is None)
        try:
            event["t0"] = timestamp(payload["opened_at"])
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
        if reason is None:
            markets = [Market.from_payload(payload[k]) for k in ("buy_market_key", "sell_market_key")]
            key = pair_key(payload)
            active_keys = {p["pair_key"] for p in self.positions.values()} | {p["pair_key"] for p in self.pending.values()}
            event.update(pair_key=key, markets=[asdict(m) for m in markets])
            if markets[1].market_type == "spot":
                reason = "unsupported_spot_short"
            elif key in active_keys:
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
                if not self._start_open(event, markets, specs):
                    reason = "skipped_rate_limit"
                else:
                    self.pending[identity] = event
        event["status"] = reason or event["status"]
        with self.store.transaction():
            self.store.event(event)
        self.requests.pump(self.executor)

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
        # Never roll an expired pre-settlement quote into a new funding period.
        funding = [s if s and (s.get("spot") or
                   (0 <= now-s["captured_at"] < TTL and s["next"] > now)) else None
                   for s in event["funding"]]
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
                         funding=funding,
                         funding_known=all(s is not None for s in funding),
                         funding_errors=event.get("funding_errors", []),
                         shadow_net_bps=payload.get("net_bps") if payload.get("closed_at") else None)
                self.positions[p["id"]] = p
                self.store.position(p)

    def _failed_variants(self, event, variants, reason):
        for variant in variants:
            for amount in AMOUNTS:
                self.store.fill(dict(id="%s:%s:%d" % (event["id"], variant, amount),
                                     event_id=event["id"], variant=variant, amount=amount,
                                     status=reason, t1=event["t1"]))

    def _account_local_wait(self):
        now = self.clock()
        changed = {}
        for key, job in self.jobs.items():
            if job["kind"] != "track":
                continue
            for i, future in enumerate(job["futures"]):
                delta = future.local_wait - job["accounted"][i]
                job["accounted"][i] = future.local_wait
                completed = future.done() and not job["observed"][i]
                book = None
                if completed:
                    job["observed"][i] = True
                    try:
                        book = future.result()
                    except Exception:
                        pass
                for p in self.positions.values():
                    if p["event_id"] != key:
                        continue
                    failed = p.setdefault("failed_since", [None, None])
                    if failed[i] is not None:
                        failed[i] += delta
                    # A good leg is healthy immediately, even if its partner is queued.
                    if completed:
                        if book is not None:
                            failed[i] = None
                            p["last_good"][i] = book.received_at
                            p["last_books"][i] = book.to_dict()
                        elif failed[i] is None:
                            failed[i] = now
                        changed[p["id"]] = p
                    if future.sent_at is None and now - p["last_good"][i] >= 60 and future.local_wait > 0:
                        if p.get("tracking_status") != "rate_limited_delay":
                            p["tracking_status"] = "rate_limited_delay"
                            changed[p["id"]] = p
                        p["rate_limited_delay_seconds"] = future.local_wait
                    # Persist adjusted timers at most once per second while queued.
                    if delta and now - job.get("wait_saved_at", 0) >= 1:
                        changed[p["id"]] = p
            if now - job.get("wait_saved_at", 0) >= 1:
                job["wait_saved_at"] = now
        if changed:
            with self.store.transaction():
                for p in changed.values():
                    self.store.position(p)

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
        if all(books):
            p["tracking_status"] = "healthy"
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
                              main_eligible=False, exit_pricing="unpriced", net_bps=None, net_usd=None)
        if result:
            p.update(result)
            self.positions.pop(p["id"], None)
        self.store.position(p)

    def tracking_due(self):
        now = self.clock()
        return any(p["next_track"] <= now and p["event_id"] not in self.jobs for p in self.positions.values())

    def _expire_positions(self, now):
        """The holding deadline is independent of network jobs and stale timers."""
        with self.store.transaction():
            for p in list(self.positions.values()):
                if now-p["opened_at"] < 172800:
                    continue
                stale = any(t is not None and now-t >= 60 for t in p.get("failed_since", [None, None]))
                result = None
                for field in ("last_books", "last_executable_exit"):
                    saved = p.get(field)
                    if not saved or len(saved) != 2 or not all(saved):
                        continue
                    try:
                        result = closing(p, *(Book(**b) for b in saved), now, stale)
                    except ValueError:
                        continue
                    if field == "last_executable_exit":
                        # Keep the existing conservative exclusion for depth fallback.
                        result.update(main_eligible=False, exit_price_fallback=True)
                    break
                if result is None:
                    result = dict(closed_at=now, holding_seconds=now-p["opened_at"],
                                  exit_reason="unpriced_exit", forced_exit_reason="stale_exit" if stale else "timeout_48h",
                                  main_eligible=False, exit_pricing="unpriced", net_bps=None, net_usd=None)
                p.update(result)
                self.positions.pop(p["id"])
                self.store.position(p)
        # Cancel queued legs; running legs may finish but cannot update closed rows.
        live_events = {p["event_id"] for p in self.positions.values()}
        for key, job in list(self.jobs.items()):
            if job["kind"] == "track" and key not in live_events:
                for future in job["futures"]:
                    future.cancel()
                del self.jobs[key]

    def _expire_open_jobs(self):
        for key, job in list(self.jobs.items()):
            if job["kind"] == "track" or self.clock() - job["event"]["t0"] <= 120:
                continue
            for future in job["futures"]:
                future.cancel()
            event = job["event"]
            variants = ("measured", "plus_1s", "leg_delay") if job["kind"] == "first" else ("plus_1s", "leg_delay")
            with self.store.transaction():
                self._failed_variants(event, variants, "skipped_stale_trigger")
                event["status"] = "skipped_stale_trigger"
                self.store.event(event)
            del self.jobs[key]
            self.pending.pop(key, None)

    def tick(self):
        now = self.clock()
        self._expire_open_jobs()
        self.requests.pump(self.executor)
        self._account_local_wait()
        self._expire_positions(now)
        self._finish_jobs()
        self._finish_funding()
        # True outages still expire while locally queued time has been subtracted.
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
                self._submit(key, "track", None, [Market(**m) for m in p["markets"]], [Spec(**s) for s in p["specs"]])
        for key, event in list(self.pending.items()):
            if event["status"] == "awaiting_funding":
                self._advance_funding(event)
                continue
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
                        if not self._start_open(event, markets, specs):
                            reason = "skipped_rate_limit"
                except ValueError as exc:
                    reason = str(exc)
                with self.store.transaction():
                    event["status"] = reason or event["status"]
                    self.store.event(event)
                    if reason:
                        self._failed_variants(event, ("measured", "plus_1s", "leg_delay"), reason)
                        self.pending.pop(key, None)
                continue
            if key in self.jobs or event.get("delayed_due", float("inf")) > now:
                continue
            if not self._submit(key, "delayed", event, [Market(**m) for m in event["markets"]], [Spec(**s) for s in event["specs"]]):
                with self.store.transaction():
                    self._failed_variants(event, ("plus_1s", "leg_delay"), "skipped_rate_limit")
                    event["status"] = "delayed_rate_limit"
                    self.store.event(event)
                self.pending.pop(key, None)
        self._refresh_specs()
        self.requests.pump(self.executor)

    def _prune_specs(self):
        now = self.clock()
        dependencies = {Market(**m).key for e in (*self.pending.values(), *self.positions.values())
                        for m in e.get("markets", [])}
        for key in dependencies:
            self.spec_used[key] = now
            if key in self.cache:
                self.cache[key]["used_at"] = now
        removed = False
        for key, value in list(self.cache.items()):
            if key not in dependencies and now-value.get("used_at", value["at"]) >= SPEC_IDLE_TTL:
                del self.cache[key]
                removed = True
        for key in sorted(self.cache, key=lambda k: (k in dependencies,
                          self.cache[k].get("used_at", self.cache[k]["at"]))):
            if len(self.cache) <= SPEC_CAPACITY:
                break
            del self.cache[key]
            removed = True
        for key in set(self.want_specs) | set(self.spec_retry):
            if key not in dependencies and (key in self.spec_retry or
                    now-self.spec_used.get(key, 0) >= SPEC_IDLE_TTL):
                # A failed stale cache must not recreate the abandoned retry on
                # the next tick's periodic refresh scan.
                if key in self.spec_retry and key in self.cache:
                    del self.cache[key]
                    removed = True
                self.want_specs.pop(key, None)
                self.spec_retry.pop(key, None)
        for key in sorted(self.want_specs, key=lambda k: (k in dependencies, self.spec_used.get(k, 0))):
            if len(self.want_specs) <= SPEC_CAPACITY:
                break
            self.want_specs.pop(key, None)
            self.spec_retry.pop(key, None)
        for key in sorted(self.spec_jobs, key=lambda k: (k in dependencies, self.spec_used.get(k, 0))):
            future = self.spec_jobs[key][1]
            if key not in dependencies and (future.priority == 1 or
                    len(self.spec_jobs) > SPEC_CAPACITY or now-self.spec_used.get(key, 0) >= SPEC_IDLE_TTL):
                future.cancel()
                del self.spec_jobs[key]
        retained = set(self.cache) | set(self.want_specs) | set(self.spec_jobs) | dependencies
        self.spec_used = {k: v for k, v in self.spec_used.items() if k in retained}
        if removed:
            with self.store.transaction():
                self.store.set_meta("spec_cache", self.cache)

    def _refresh_specs(self):
        cache_changed = False
        pending_keys = {Market(**m).key for e in self.pending.values() for m in e.get("markets", [])}
        for key, (_, future) in self.spec_jobs.items():
            if key in pending_keys:
                self.requests.promote(future, 1)
        for key, (m, future) in list(self.spec_jobs.items()):
            if not future.done():
                continue
            del self.spec_jobs[key]
            try:
                spec = parse_spec(m, future.result())
                self.cache[key] = dict(at=self.clock(), spec=asdict(spec), market=asdict(m))
                self.want_specs.pop(key, None)
                self.spec_retry.pop(key, None)
                cache_changed = True
            except Exception:
                # Retry no faster than 60s; cache remains stale and unusable for opens.
                self.spec_retry[key] = self.clock() + 60
                self.want_specs[key] = m
        for key, value in list(self.cache.items()):
            if self.clock() - value["at"] >= 21600:
                self.want_specs[key] = Market(**value["market"])
        self._prune_specs()
        if cache_changed:
            with self.store.transaction():
                self.store.set_meta("spec_cache", self.cache)
        pending_keys = {Market(**m).key for e in self.pending.values() for m in e.get("markets", [])}
        for key, m in sorted(self.want_specs.items(), key=lambda item: item[0] not in pending_keys):
            if key in self.spec_jobs or self.spec_retry.get(key, 0) > self.clock():
                continue
            future = self.requests.submit(m.exchange, endpoint(m, "spec"), 1 if key in pending_keys else 2)
            self.spec_jobs[key] = (m, future)
            del self.want_specs[key]

    def close(self, discard=False):
        self.requests.close()
        self.executor.shutdown(wait=not discard)
        if not discard:
            self._finish_jobs()
