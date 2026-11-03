"""Single-flight worker with detached immutable inputs and atomic status publication."""

import asyncio
import logging
from collections import namedtuple
from operator import itemgetter
from datetime import UTC, datetime
from time import monotonic, perf_counter, thread_time

from app.models.opportunity import Opportunity
from app.db.database import connect_database
from app.db.repositories import ShadowTradeRepository
from app.db.schema import initialize_shadow_schema
from app.services.execution_routes import ExecutionRoutes
from app.services.fake_opportunity import FakeOpportunityScorer, MarketChanges, prepare_markets
from app.services.shadow_ledger import ShadowLedger
from app.services.snapshot_consumers import PublishedSnapshot

logger = logging.getLogger(__name__)

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


FrozenSnapshot = namedtuple("FrozenSnapshot", "generation published_at markets opportunities market_lists op_lists")


def freeze_snapshot(snapshot, previous=None):
    market_values, estimated = [], []
    for market in snapshot.markets:
        values = market.__dict__
        market_values.extend(_market_values(values))
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

class ShadowRuntime:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.ledger = ShadowLedger(settings)
        self.catalog = ExecutionRoutes(settings.native_route_exchanges, settings.crossex_catalog_refresh_seconds)
        # The refresh task owns catalog; only this independent cache is used by
        # the computation worker, with a captured immutable catalogue per round.
        self.worker_catalog = ExecutionRoutes(settings.native_route_exchanges)
        self.scorer = FakeOpportunityScorer()
        self.changes = MarketChanges()
        self.repo = None
        self.db = None
        self.owns_db = False
        self.restored = False
        self.errors = 0
        self.last_log = -float("inf")
        self.prune_at = 0
        self.last_duration_ms = None
        self.stored_open_count = 0
        self.skipped_rounds = 0
        self._capture_errors = 0
        self._active = None
        self._display_cache = {}
        self._input_cache = {}
        self._validation_cache = ()
        self._closing = False
        self._published = self._worker_status()

    def error(self):
        self.errors += 1
        current = monotonic()
        if current - self.last_log >= 300:
            logger.exception("shadow consumer failed (count=%s); collection and alerts continue", self.errors)
            self.last_log = current

    async def initialize(self, shared_db):
        self._published = await asyncio.to_thread(lambda: asyncio.run(self._initialize(shared_db)))

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
            self.error()
        return self._worker_status()

    async def submit(self, snapshot):
        """Mailbox handler: never wait for previous work or enqueue a backlog."""
        started = perf_counter()
        if self._closing or (self._active is not None and not self._active.done()):
            self.skipped_rounds += 1
            return None
        try:
            frozen, self._input_cache = freeze_snapshot(snapshot, self._input_cache)
            entries = frozenset(self.catalog.entries)
            catalog_status = (self.catalog.updated_at, len(entries), self.catalog.errors)
            validations = tuple(op.validation for op in snapshot.opportunities)
            timing = []
            task = asyncio.create_task(self._finish(snapshot.generation, validations, frozen, entries, catalog_status, timing))
            self._active = task
            timing.append((perf_counter() - started) * 1000)
            return task
        except Exception:
            # Capture errors are isolated as well; do not touch worker-owned state.
            logger.exception("shadow input capture failed")
            self._capture_errors += 1
            return None

    async def consume(self, snapshot):
        task = await self.submit(snapshot)
        if task is not None:
            # Cancelling a consumer must not release the guard while to_thread
            # continues running. _finish owns publication and stays alive.
            await asyncio.shield(task)

    async def _finish(self, generation, validations, frozen, entries, catalog_status, timing):
        dispatch_ms = timing[0]
        annotations, status, cpu_ms, wall_ms = await asyncio.to_thread(self._work, frozen, entries)
        resumed = perf_counter()
        display_changed = status.pop("display_changed", False)
        if self.store.generation == generation and annotations is not None:
            if display_changed or validations != self._validation_cache:
                for validation, target in zip(validations, annotations):
                    target.__dict__["validation"] = validation
            self._validation_cache = validations
            self.store.set_opportunities(annotations)
        status.update(catalog_updated_at=catalog_status[0], catalog_entries=catalog_status[1],
                      catalog_errors=catalog_status[2], worker_cpu_ms=cpu_ms, worker_wall_ms=wall_ms)
        del frozen  # Include detached-input release in event-loop occupancy.
        self.last_duration_ms = dispatch_ms + (perf_counter() - resumed) * 1000
        status["last_duration_ms"] = self.last_duration_ms
        status["capture_ms"] = dispatch_ms
        status["publish_ms"] = self.last_duration_ms - dispatch_ms
        self._published = status

    def _work(self, snapshot, entries):
        started, cpu = perf_counter(), thread_time()
        try:
            display, status = self._calculate(snapshot, entries)
        except Exception:
            self.error()
            display, status = None, self._worker_status()
        return display, status, (thread_time() - cpu) * 1000, (perf_counter() - started) * 1000

    def _calculate(self, snapshot, entries):
        market_width = len(MARKET_FIELDS) - 1
        payloads = snapshot.opportunities
        lists = snapshot.op_lists
        snapshot = PublishedSnapshot(snapshot.generation, snapshot.published_at,
            tuple(MarketInput._make(snapshot.markets[i * market_width:(i + 1) * market_width] + (fields,))
                  for i, fields in enumerate(snapshot.market_lists)),
            tuple(OpportunityInput._make(_op_projection(values) + fields[-3:])
                  for values, fields in zip(payloads, lists)))
        annotations = asyncio.run(self._process(snapshot, entries))
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

    async def _process(self, snapshot, entries):
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
            prepared = prepare_markets(snapshot.markets, snapshot.published_at)
            changed = self.changes.update(prepared)
            annotations = self.scorer.score(snapshot.markets, snapshot.opportunities, snapshot.published_at, prepared, changed)
            if self.worker_catalog.entries != entries:
                self.worker_catalog.entries = entries
            for op in snapshot.opportunities:
                fields = self.worker_catalog.fields(op)
                fields["buy_routes"] = list(fields["buy_routes"])
                fields["sell_routes"] = list(fields["sell_routes"])
                annotations[op.id].update(fields)
            if self.repo is not None and self.ledger.enabled:
                self.ledger.process(snapshot.markets, snapshot.opportunities, snapshot.published_at, annotations, prepared, changed)
                await self.ledger.flush(self.repo)
        except Exception:
            self.error()
        return annotations

    async def run_inline_observer(self, stop):
        generation = 0
        while not stop.is_set():
            if self.store.generation > generation:
                generation = self.store.generation
                await self.submit(PublishedSnapshot(generation, self.store.published_at,
                    tuple(self.store.get_markets()), tuple(self.store.get_opportunities())))
            try:
                await asyncio.wait_for(stop.wait(), 0.1)
            except TimeoutError:
                pass

    def _worker_status(self):
        return {"enabled": self.ledger.enabled, "ready": self.restored,
                "open_count": self.ledger.open_count if self.ledger.enabled else self.stored_open_count,
                "dropped": self.ledger.dropped, "errors": self.errors,
                "pending_writes": len(self.ledger.pending), "scorer_dropped": self.scorer.dropped,
                "recomputed_positions": self.ledger.recomputed}

    def status(self):
        # No live ledger/scorer containers are inspected from the event loop.
        return {"catalog_updated_at": None, "catalog_entries": 0, "catalog_errors": 0,
                "last_duration_ms": None, **self._published, "skipped_rounds": self.skipped_rounds,
                "errors": self._published["errors"] + self._capture_errors}

    async def _close_worker(self):
        try:
            if self.repo is not None:
                for position in self.ledger.positions.values():
                    self.ledger._queue(position)
                await self.ledger.flush(self.repo)
        except Exception:
            self.error()
        finally:
            if self.owns_db and self.db is not None:
                await self.db.close()
        return self._worker_status()

    async def aclose(self):
        self._closing = True
        if self._active is not None:
            await asyncio.shield(self._active)
        self._published = await asyncio.to_thread(lambda: asyncio.run(self._close_worker()))
