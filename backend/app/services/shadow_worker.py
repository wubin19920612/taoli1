"""Single-flight worker with detached immutable inputs and atomic status publication."""

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from time import monotonic, perf_counter, thread_time

from app.db.database import connect_database
from app.db.repositories import ShadowTradeRepository
from app.db.schema import initialize_shadow_schema
from app.services.execution_routes import ExecutionRoutes, ShadowErrorLog
from app.services.fake_opportunity import FakeOpportunityScorer, MarketChanges, prepare_markets, market_key, opportunity_keys
from app.services.shadow_ledger import ShadowLedger
from app.services.snapshot_consumers import PublishedSnapshot

class ShadowWorker:
    def __init__(self, settings):
        self.settings = settings
        self.ledger = ShadowLedger(settings)
        self.error_log = ShadowErrorLog(clock=lambda: monotonic())
        self.worker_catalog = ExecutionRoutes(settings.native_route_exchanges)
        self.scorer = FakeOpportunityScorer()
        self.changes = MarketChanges()
        self.repo = None
        self.db = None
        self.owns_db = False
        self.restored = False
        self.prune_at = 0
        self.stored_open_count = 0
        self._asset_index = {}
        self._phase_times = {}


    @property
    def errors(self):
        return sum(self.error_log.counts().values())


    def error(self, category="consumer"):
        self.error_log.record(category)


    async def _initialize(self, shared_db):
        try:
            if self.settings.sqlite_path == ":memory:":
                self.db = shared_db
            else:
                self.db = await connect_database(self.settings.sqlite_path)
                self.owns_db = True
                await self.db.execute("PRAGMA cache_size=-512")
            await initialize_shadow_schema(self.db)
            self.repo = ShadowTradeRepository(self.db)
            self.stored_open_count = await self.repo.count_open()
            if self.ledger.enabled:
                self.ledger.restore(*(await self.repo.restore(datetime.now(UTC))))
            self.restored = True
        except Exception:
            self.error("initialize")
        return self._worker_status()


    def _select_markets(self, markets, related, held_keys, new_minute):
        started, cpu = perf_counter(), thread_time()
        # Collector unpack_async creates new models each publication; registry
        # refresh replaces models. These published references are read-only in
        # production. Build exact lookup off-loop, then detach selected values.
        prepared = {market_key(m): m for m in markets}
        if new_minute:
            index = defaultdict(list)
            for key, m in prepared.items():
                index[m.asset_id or m.symbol].append(key)
            self._asset_index = {a: tuple(keys) for a, keys in index.items()}
        keys = set(held_keys)
        for op in related:
            keys.update(opportunity_keys(op))
        assets = {prepared[k].asset_id or prepared[k].symbol for k in keys if k in prepared}
        # New same-asset venues can enter median voting up to 60s later (PM 15).
        # Exact opportunity/held legs are always taken from this round's lookup.
        for asset in assets:
            keys.update(self._asset_index.get(asset, ()))
        selected = tuple(prepared[k] for k in keys if k in prepared)
        return selected, frozenset(assets), (thread_time() - cpu) * 1000, (perf_counter() - started) * 1000


    async def _process(self, snapshot, entries, history=(), assets=None, clear_spreads=(), below_threshold=()):
        if not self.ledger.enabled:
            return None
        annotations = None
        try:
            if self.repo is not None:
                if monotonic() >= self.prune_at:
                    await self.repo.prune(snapshot.published_at)
                    self.prune_at = monotonic() + 3600
                if self.ledger.enabled:
                    if not self.restored:
                        self.ledger.restore(*(await self.repo.restore(snapshot.published_at)))
                        self.restored = True
                    await self.ledger.flush(self.repo)
            started = perf_counter()
            prepared = prepare_markets(snapshot.markets, snapshot.published_at)
            changed = self.changes.update(prepared)
            self._phase_times = {"prepare_ms": (perf_counter() - started) * 1000}
            started = perf_counter()
            annotations = self.scorer.score(snapshot.markets, snapshot.opportunities, snapshot.published_at, prepared, changed,
                                            history=history, related_assets=assets, clear_spreads=clear_spreads)
            self._phase_times["score_ms"] = (perf_counter() - started) * 1000
            started = perf_counter()
            if self.worker_catalog.entries != entries:
                self.worker_catalog.entries = entries
            for op in snapshot.opportunities:
                fields = self.worker_catalog.fields(op)
                fields["buy_routes"] = list(fields["buy_routes"])
                fields["sell_routes"] = list(fields["sell_routes"])
                annotations[op.id].update(fields)
            self._phase_times["routes_ms"] = (perf_counter() - started) * 1000
            started = perf_counter()
            if self.repo is not None and self.ledger.enabled:
                self.ledger.process(snapshot.markets, snapshot.opportunities, snapshot.published_at, annotations, prepared, changed,
                                    below_threshold=below_threshold)
                await self.ledger.flush(self.repo)
            self._phase_times["ledger_flush_ms"] = (perf_counter() - started) * 1000
        except Exception:
            self.error()
        return annotations


    def _worker_status(self):
        return {"enabled": self.ledger.enabled, "ready": self.restored,
                "open_count": self.ledger.open_count if self.ledger.enabled else self.stored_open_count,
                "dropped": self.ledger.dropped, "errors": self.errors,
                "dropped_by_reason": dict(self.ledger.dropped_by_reason),
                "baseline_skipped": self.ledger.baseline_skipped,
                "pending_writes": len(self.ledger.pending), "scorer_dropped": self.scorer.dropped,
                "scorer_price_states": len(self.scorer.prices),
                "held": (frozenset(self.ledger.positions), frozenset(self.ledger.by_market)),
                "recomputed_positions": self.ledger.recomputed, "worker_phases_ms": dict(self._phase_times)}


    async def _close_worker(self):
        try:
            if self.repo is not None:
                for position in self.ledger.positions.values():
                    self.ledger._queue(position)
                await self.ledger.flush(self.repo)
        except Exception:
            self.error("shutdown_flush")
        finally:
            if self.owns_db and self.db is not None:
                try:
                    await self.db.close()
                except Exception:
                    self.error("shutdown_close")
        return self._worker_status()




def run_child(settings, incoming, outgoing, stop):
    """Single owner of ledger/scorer and the shadow SQLite writer."""
    import os
    import pickle
    import queue
    from types import SimpleNamespace
    from app.services.shadow_ipc import MarketInput as WireMarket, OpportunityInput as WireOpportunity, MarketDirectory, rss_mb
    from app.services.shadow_stats import buckets

    async def run():
        worker = ShadowWorker(SimpleNamespace(**settings))
        try:
            status = await worker._initialize(None)
            status.update(error_counts=worker.error_log.counts(), worker_pid=os.getpid(),
                          child_rss_mb=rss_mb(os.getpid()))
            outgoing.put(("ready", 0, None, status))
            if not worker.restored:
                return
            minute = None
            sequence = 0
            synchronized = False
            directory = []
            quotes = {}
            threshold_ids = ()
            entries = set()
            while True:
                try:
                    kind, token, payload = incoming.get(timeout=.1)
                except queue.Empty:
                    if stop.is_set():
                        break
                    continue
                started, cpu = perf_counter(), thread_time()
                result = None
                ipc_wait = 0.0
                try:
                    data = pickle.loads(payload)
                    if kind == "statistics":
                        result = buckets(*data)
                    else:
                        next_sequence, full, now, ids, spreads, catalog = data
                        if not full and (not synchronized or next_sequence != sequence + 1):
                            outgoing.put((kind, token, None, {**worker._worker_status(),
                                "error_counts": worker.error_log.counts(), "resync_required": True}))
                            continue
                        if full:
                            directory.clear()
                            quotes.clear()
                            minute = None
                        if ids is not None:
                            threshold_ids = ids
                        if len(threshold_ids) != len(spreads):
                            raise ValueError("invalid shadow threshold table")
                        thresholds = tuple(zip(threshold_ids, spreads))
                        if catalog is not None:
                            entries = catalog
                        held_ids, held_keys = worker._worker_status()["held"]
                        related_indices = tuple(i for i, (key, spread) in enumerate(thresholds)
                                                if spread >= worker.ledger.minimum or key in held_ids)
                        waiting = perf_counter()
                        outgoing.put(("capture", token, ("directory", related_indices), {}))
                        next_kind, next_token, payload = incoming.get(timeout=60)
                        ipc_wait += perf_counter() - waiting
                        if (next_kind, next_token) != ("directory", token):
                            raise ValueError("invalid shadow directory reply")
                        size, chunks, op_chunks = pickle.loads(payload)
                        for i in range(size, len(directory)):
                            quotes.pop(i, None)
                        del directory[size:]
                        directory.extend([None] * (size - len(directory)))
                        for chunk in chunks:
                            for i, row in pickle.loads(chunk):
                                directory[i] = MarketDirectory(*row)
                                quotes.pop(i, None)
                        related = tuple(WireOpportunity(*row) for chunk in op_chunks for row in pickle.loads(chunk))
                        current_minute = int(now.timestamp() // 60)
                        selected, assets, _, select_wall = worker._select_markets(directory, related, held_keys, current_minute != minute)
                        selected_ids = {id(m) for m in selected}
                        indices = tuple(i for i, m in enumerate(directory) if id(m) in selected_ids)
                        waiting = perf_counter()
                        outgoing.put(("capture", token, ("quotes", None if current_minute != minute else indices), {}))
                        next_kind, next_token, payload = incoming.get(timeout=60)
                        ipc_wait += perf_counter() - waiting
                        if (next_kind, next_token) != ("quotes", token):
                            raise ValueError("invalid shadow quote reply")
                        for chunk in pickle.loads(payload):
                            for i, row in pickle.loads(chunk):
                                quotes[i] = WireMarket(*row)
                        selected = tuple(quotes[i] for i in indices)
                        history = tuple(m for m in quotes.values() if (m.asset_id or m.symbol) not in assets) if current_minute != minute else ()
                        minute = current_minute
                        sequence, synchronized = next_sequence, True
                        calculate_start = perf_counter()
                        result = await worker._process(PublishedSnapshot(0, now, selected, related), entries,
                            history, assets, tuple(key for key, spread in thresholds if spread < 1),
                            tuple(key for key, spread in thresholds if spread < worker.ledger.minimum))
                        calculate_wall = (perf_counter() - calculate_start) * 1000
                    status = worker._worker_status()
                    status.update(worker_cpu_ms=(thread_time() - cpu) * 1000,
                                  worker_wall_ms=(perf_counter() - started - ipc_wait) * 1000,
                                  ipc_wait_ms=ipc_wait * 1000,
                                  error_counts=worker.error_log.counts(), worker_pid=os.getpid(),
                                  child_rss_mb=rss_mb(os.getpid()),
                                  asset_index_assets=len(worker._asset_index),
                                  display_cache_entries=len(result) if kind == "round" and result else 0)
                    if kind == "round":
                        status.update(select_wall_ms=select_wall, calculate_wall_ms=calculate_wall, mirror_sequence=sequence)
                except Exception:
                    synchronized = False
                    worker.error("worker")
                    status = {**worker._worker_status(), "error_counts": worker.error_log.counts(), "resync_required": True}
                outgoing.put((kind, token, result, status))
        finally:
            status = await worker._close_worker()
            outgoing.put(("closed", 0, None, {**status, "error_counts": worker.error_log.counts()}))

    incoming._writer.close()
    outgoing._reader.close()
    try:
        asyncio.run(run())
    finally:
        incoming.close()
        outgoing.close()
        outgoing.join_thread()
