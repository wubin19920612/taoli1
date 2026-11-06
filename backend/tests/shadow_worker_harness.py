"""In-process harness for fault injection into the child computation core.

Existing business assertions run unchanged; real IPC is tested separately.
"""
"""Single-flight worker with detached immutable inputs and atomic status publication."""

import asyncio
from collections import namedtuple
from operator import itemgetter
from datetime import UTC, datetime
from time import monotonic, perf_counter, thread_time

from app.models.opportunity import Opportunity
from app.db.database import connect_database
from app.db.repositories import ShadowTradeRepository
from app.db.schema import initialize_shadow_schema
from app.services.execution_routes import ExecutionRoutes, ShadowErrorLog
from app.services.snapshot_consumers import PublishedSnapshot

MARKET_FIELDS = "exchange market_type raw_symbol dex symbol_alias_price_multiplier contract_size_multiplier timestamp upstream_timestamp bid ask quote_kind funding_next_time funding_rate_pct funding_interval_hours asset_id symbol identity_collision estimated_fields".split()
MarketInput = namedtuple("MarketInput", MARKET_FIELDS)
_market_values = itemgetter(*MARKET_FIELDS[:-1])
OP_LISTS = ("buy_estimated_fields", "sell_estimated_fields", "risk_labels", "buy_routes", "sell_routes", "fake_reasons")


OP_FIELDS = "id symbol open_spread_pct buy_exchange buy_market_type buy_raw_symbol buy_dex buy_price_multiplier buy_contract_size_multiplier sell_exchange sell_market_type sell_raw_symbol sell_dex sell_price_multiplier sell_contract_size_multiplier buy_volume_24h_usdt sell_volume_24h_usdt buy_fee_pct sell_fee_pct fake_score fake_flag executable buy_routes sell_routes fake_reasons".split()
OpportunityInput = namedtuple("OpportunityInput", OP_FIELDS)
DISPLAY_FIELDS = tuple(k for k in Opportunity.model_fields if k not in OP_LISTS and k != "validation")
_op_values = itemgetter(*DISPLAY_FIELDS)
_op_projection = itemgetter(*(DISPLAY_FIELDS.index(k) for k in OP_FIELDS[:-3]))
_DISPLAY_TEMPLATE = Opportunity.model_construct()


FrozenSnapshot = namedtuple("FrozenSnapshot", "generation published_at markets opportunities market_lists op_lists history related_assets clear_spreads below_threshold", defaults=((), frozenset(), (), ()))


def freeze_snapshot(snapshot, previous=None):
    market_values, estimated = [], []
    for market in snapshot.markets:
        values = market.__dict__
        market_values.append(_market_values(values))
        estimated.append(tuple(values["estimated_fields"]))
    markets, estimates = tuple(market_values), tuple(estimated)
    payloads, lists, cache = [], [], {}
    previous = previous or {}
    for op in snapshot.opportunities:
        values = op.__dict__
        old = previous.get(op.id)
        if old is not None and old[0] == tuple(values.values()):
            entry = old
        else:
            copied = values.copy()
            for key in OP_LISTS:
                copied[key] = list(values[key])
            entry = (tuple(copied.values()), _op_values(values), tuple(tuple(values[k]) for k in OP_LISTS))
        cache[op.id] = entry
        payloads.append(entry[1])
        lists.append(entry[2])
    return FrozenSnapshot(snapshot.generation, snapshot.published_at, markets, tuple(payloads), estimates, tuple(lists)), cache

from app.services.shadow_worker import ShadowWorker

class ShadowRuntime(ShadowWorker):
    def __init__(self, settings, store):
        super().__init__(settings)
        self.store = store
        self.catalog = ExecutionRoutes(settings.native_route_exchanges, settings.crossex_catalog_refresh_seconds,
                                       on_error=self.error)
        self.last_duration_ms = None
        self.skipped_rounds = self.skipped_publications = 0
        self._active = None
        self._display_cache = {}
        self._input_cache = {}
        self._validation_cache = ()
        self._held = (frozenset(), frozenset())
        self._sample_minute = None
        self._closing = False
        self._close_task = None
        self._published = self._worker_status()
        self._published.pop("held", None)
        self.error_log = ShadowErrorLog(clock=lambda: monotonic())

    async def initialize(self, shared_db):
        if not self.ledger.enabled:
            return
        self._published = await asyncio.to_thread(lambda: asyncio.run(self._initialize(shared_db)))
        self._held = self._published.pop("held", self._held)


    async def submit(self, snapshot):
        """Mailbox handler: never wait for previous work or enqueue a backlog."""
        if not self.ledger.enabled:
            return None
        started = perf_counter()
        if self._closing or (self._active is not None and not self._active.done()):
            self.skipped_rounds += 1
            return None
        try:
            # The queue may predate an identity refresh. Never pair old contents
            # with the store's current version when consuming a queued snapshot.
            opportunities_version = snapshot.opportunities_version
            held_ids, held_keys = self._held
            related = tuple(op for op in snapshot.opportunities
                            if op.open_spread_pct >= self.ledger.minimum or op.id in held_ids)
            clear_spreads = tuple(op.id for op in snapshot.opportunities if op.open_spread_pct < 1)
            # Freeze presentation input before yielding, including mutable lists.
            frozen, self._input_cache = freeze_snapshot(
                PublishedSnapshot(snapshot.generation, snapshot.published_at, (), related), self._input_cache)
            frozen = frozen._replace(clear_spreads=clear_spreads, below_threshold=tuple(
                op.id for op in snapshot.opportunities if op.open_spread_pct < self.ledger.minimum))
            entries = frozenset(self.catalog.entries)
            catalog_status = (self.catalog.updated_at, len(entries), self.catalog.errors)
            validations = tuple(op.validation for op in related)
            timing = []
            task = asyncio.create_task(self._capture_round(snapshot, related, held_keys, validations, frozen, entries, catalog_status, timing, opportunities_version))
            self._active = task
            timing.append((perf_counter() - started) * 1000)
            return task
        except Exception:
            self.error("capture")
            return None


    async def _capture_round(self, snapshot, related, held_keys, validations, frozen, entries, catalog_status, timing, opportunities_version):
        try:
            minute = int(snapshot.published_at.timestamp() // 60)
            new_minute = minute != self._sample_minute
            selected, assets, select_cpu, select_wall = await asyncio.to_thread(
                self._select_markets, snapshot.markets, related, held_keys, new_minute)
            started = perf_counter()
            captured, _ = freeze_snapshot(PublishedSnapshot(snapshot.generation, snapshot.published_at, selected, ()))
            frozen = frozen._replace(markets=captured.markets, market_lists=captured.market_lists, related_assets=assets)
            segments = [timing[0] + (perf_counter() - started) * 1000]
            history = []
            if new_minute:
                for offset in range(0, len(snapshot.markets), 1000):
                    await asyncio.sleep(0)
                    started = perf_counter()
                    for m in snapshot.markets[offset:offset + 1000]:
                        # Current relevant books were captured together above;
                        # these later slices only supply unrelated price history.
                        if (m.asset_id or m.symbol) not in assets:
                            history.append(MarketInput(*_market_values(m.__dict__), tuple(m.estimated_fields)))
                    segments.append((perf_counter() - started) * 1000)
                self._sample_minute = minute
            started = perf_counter()
            frozen = frozen._replace(history=tuple(history))
            work = self._finish(snapshot.generation, validations, frozen, entries, catalog_status,
                                segments, snapshot.opportunities, select_cpu, select_wall, opportunities_version)
            del frozen, captured, history, selected, snapshot, related
            segments[-1] += (perf_counter() - started) * 1000
            await work
        except Exception:
            self.error("capture")


    async def consume(self, snapshot):
        task = await self.submit(snapshot)
        if task is not None:
            # Cancelling a consumer must not release the guard while to_thread
            # continues running. _finish owns publication and stays alive.
            await asyncio.shield(task)


    async def _finish(self, *args):
        work = self._finish_round(*args)
        # Do not retain another reference to the detached input: _finish_round
        # includes its release in the existing event-loop occupancy measurement.
        del args
        try:
            await work
        except Exception:
            self.error("publish")


    async def _finish_round(self, generation, validations, frozen, entries, catalog_status, timing, originals, select_cpu, select_wall, opportunities_version):
        dispatch_ms = sum(timing)
        annotations, status, cpu_ms, wall_ms = await asyncio.to_thread(self._work, frozen, entries)
        resumed = perf_counter()
        display_changed = status.pop("display_changed", False)
        # Ledger progress must survive a failed presentation publication: the
        # next round needs these keys even if the opportunity has disappeared.
        self._held = status.pop("held", self._held)
        try:
            if self.store.opportunities_version != opportunities_version:
                self.skipped_publications += 1
            elif self.store.generation == generation and annotations is not None:
                if display_changed or validations != self._validation_cache:
                    for validation, target in zip(validations, annotations):
                        target.__dict__["validation"] = validation
                self._validation_cache = validations
                by_id = {op.id: op for op in annotations}
                if not self.store.set_opportunities_if_version(
                    [by_id.get(op.id, op) for op in originals], opportunities_version,
                ):
                    self.skipped_publications += 1
        except Exception:
            self.error("publish")
        status.update(catalog_updated_at=catalog_status[0], catalog_entries=catalog_status[1],
                      catalog_errors=catalog_status[2], worker_cpu_ms=cpu_ms + select_cpu, worker_wall_ms=wall_ms + select_wall,
                      select_wall_ms=select_wall, calculate_wall_ms=wall_ms)
        del frozen  # Include detached-input release in event-loop occupancy.
        self.last_duration_ms = dispatch_ms + (perf_counter() - resumed) * 1000
        publish_ms = self.last_duration_ms - dispatch_ms
        status["event_loop_max_segment_ms"] = max(*timing, publish_ms)
        status["capture_segments_ms"] = tuple(timing)
        status["last_duration_ms"] = self.last_duration_ms
        status["capture_ms"] = dispatch_ms
        status["publish_ms"] = self.last_duration_ms - dispatch_ms
        self._published = status


    def _work(self, snapshot, entries):
        started, cpu = perf_counter(), thread_time()
        try:
            display, status = self._calculate(snapshot, entries)
        except Exception:
            self.error("worker")
            display, status = None, self._worker_status()
        return display, status, (thread_time() - cpu) * 1000, (perf_counter() - started) * 1000


    def _calculate(self, snapshot, entries):
        history, assets, clear_spreads = snapshot.history, snapshot.related_assets, snapshot.clear_spreads
        below_threshold = snapshot.below_threshold
        payloads = snapshot.opportunities
        lists = snapshot.op_lists
        # These fixed-width tuples come exclusively from freeze_snapshot's
        # itemgetters; construct records without _make's per-row Python checks.
        snapshot = PublishedSnapshot(snapshot.generation, snapshot.published_at,
            tuple(tuple.__new__(MarketInput, values + (fields,))
                  for values, fields in zip(snapshot.markets, snapshot.market_lists)),
            tuple(tuple.__new__(OpportunityInput, _op_projection(values) + fields[-3:])
                  for values, fields in zip(payloads, lists)))
        annotations = asyncio.run(self._process(snapshot, entries, history, assets, clear_spreads, below_threshold))
        display, cache, display_changed = [], {}, False
        if annotations is not None:
            for op, values, fields in zip(snapshot.opportunities, payloads, lists):
                annotations_for_op = annotations[op.id]
                previous = self._display_cache.get(op.id)
                if previous is not None and previous[:3] == (values, fields, annotations_for_op):
                    entry = previous
                else:
                    display_changed = True
                    data = dict(zip(DISPLAY_FIELDS, values))
                    data.update((key, list(value)) for key, value in zip(OP_LISTS, fields))
                    data.update(annotations_for_op)
                    model = _DISPLAY_TEMPLATE.model_copy()
                    model.__dict__.update(data)
                    # set(dict) pre-sizes storage; Pydantic update(dict_keys)
                    # overallocates ~4 KiB per 79-field presentation model.
                    object.__setattr__(model, "__pydantic_fields_set__", set(data))
                    entry = (values, fields, annotations_for_op, model)
                cache[op.id] = entry
                display.append(entry[3])
            self._display_cache = cache
        return (display if annotations is not None else None), {**self._worker_status(), "display_changed": display_changed}


    async def run_inline_observer(self, stop):
        if not self.ledger.enabled:
            return
        generation = 0
        while not stop.is_set():
            if self.store.generation > generation:
                generation = self.store.generation
                await self.submit(PublishedSnapshot(generation, self.store.published_at,
                    tuple(self.store.get_markets()), tuple(self.store.get_opportunities()),
                    opportunities_version=self.store.opportunities_version))
            try:
                await asyncio.wait_for(stop.wait(), 0.1)
            except TimeoutError:
                pass


    def status(self):
        # No live ledger/scorer containers are inspected from the event loop.
        counts = self.error_log.counts()
        return {"catalog_updated_at": None, "catalog_entries": 0,
                "last_duration_ms": None, **self._published, "skipped_rounds": self.skipped_rounds,
                "dropped_by_reason": dict(self._published["dropped_by_reason"]),
                "skipped_publications": self.skipped_publications,
                "errors": sum(counts.values()), "error_counts": counts,
                "catalog_errors": self.catalog.errors}


    async def aclose(self):
        self._closing = True
        if not self.ledger.enabled:
            return
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._shutdown())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            # The caller must not close its shared DB while a worker still owns it.
            await asyncio.shield(self._close_task)
            raise


    async def _shutdown(self):
        if self._active is not None:
            try:
                await asyncio.shield(self._active)
            except Exception:
                self.error("shutdown_wait")
        self._published = await asyncio.to_thread(lambda: asyncio.run(self._close_worker()))
        self._held = self._published.pop("held", self._held)



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



    async def statistics(self, since, until, dims, include_fake, strategy_version):
        from types import SimpleNamespace
        from app.api.routes_shadow import read_committed
        from app.services.shadow_stats import buckets
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(shadow_runtime=self)))
        rows = await read_committed(request, "closed_events", since, until, include_fake, strategy_version)
        return buckets(rows, dims, include_fake)
