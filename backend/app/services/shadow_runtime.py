"""Failure-isolated snapshot consumer and read-only opportunity annotations."""

import asyncio
import logging
from datetime import UTC, datetime
from time import monotonic

from app.db.database import connect_database
from app.db.repositories import ShadowTradeRepository
from app.db.schema import initialize_shadow_schema
from app.services.execution_routes import ExecutionRoutes
from app.services.fake_opportunity import FakeOpportunityScorer, prepare_markets
from app.services.shadow_ledger import ShadowLedger
from app.services.snapshot_consumers import PublishedSnapshot

logger = logging.getLogger(__name__)


class ShadowRuntime:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.ledger = ShadowLedger(settings)
        self.catalog = ExecutionRoutes(settings.native_route_exchanges, settings.crossex_catalog_refresh_seconds)
        self.scorer = FakeOpportunityScorer()
        self.repo = None
        self.db = None
        self.owns_db = False
        self.restored = False
        self.errors = 0
        self.last_log = -float("inf")
        self.prune_at = 0
        self.last_duration_ms = None
        self.stored_open_count = 0

    def error(self):
        self.errors += 1
        current = monotonic()
        if current - self.last_log >= 300:
            logger.exception("shadow consumer failed (count=%s); collection and alerts continue", self.errors)
            self.last_log = current

    async def initialize(self, shared_db):
        try:
            if self.settings.sqlite_path == ":memory:":
                self.db = shared_db
            else:
                self.db = await connect_database(self.settings.sqlite_path)
                self.owns_db = True
            await initialize_shadow_schema(self.db)
            self.repo = ShadowTradeRepository(self.db)
            self.stored_open_count = await self.repo.count_open()
            if self.ledger.enabled:
                self.ledger.restore(*(await self.repo.restore(datetime.now(UTC))))
            self.restored = True
        except Exception:
            self.error()

    async def consume(self, snapshot):
        started = monotonic()
        try:
            prepared = prepare_markets(snapshot.markets, snapshot.published_at)
            annotations = self.scorer.score(snapshot.markets, snapshot.opportunities, snapshot.published_at, prepared)
            for op in snapshot.opportunities:
                annotations[op.id].update(self.catalog.fields(op))
            # Published models remain immutable for other subscribers. No await
            # between checking the generation and replacing its annotated copies.
            if self.store.generation == snapshot.generation:
                self.store.set_opportunities([op.model_copy(update=annotations[op.id]) for op in snapshot.opportunities])
            if self.repo is None:
                return
            if monotonic() >= self.prune_at:
                await self.repo.prune(snapshot.published_at)
                self.prune_at = monotonic() + 3600
            if not self.ledger.enabled:
                return
            if not self.restored:
                self.ledger.restore(*(await self.repo.restore(snapshot.published_at)))
                self.restored = True
            # A failed batch must commit before accepting another generation.
            # This bounds the retry queue and keeps writes idempotent.
            await self.ledger.flush(self.repo)
            self.ledger.process(snapshot.markets, snapshot.opportunities, snapshot.published_at, annotations, prepared)
            await self.ledger.flush(self.repo)
        except Exception:
            self.error()
        finally:
            self.last_duration_ms = (monotonic() - started) * 1000

    async def run_inline_observer(self, stop):
        """Observe legacy inline publications without changing its scheduler/history."""
        generation = 0
        while not stop.is_set():
            if self.store.generation > generation:
                generation = self.store.generation
                await self.consume(PublishedSnapshot(generation, self.store.published_at,
                    tuple(self.store.get_markets()), tuple(self.store.get_opportunities())))
            try:
                await asyncio.wait_for(stop.wait(), 0.1)
            except TimeoutError:
                pass

    def status(self):
        return {"enabled": self.ledger.enabled, "ready": self.restored,
                "open_count": self.ledger.open_count if self.ledger.enabled else self.stored_open_count,
                "dropped": self.ledger.dropped,
                "errors": self.errors, "pending_writes": len(self.ledger.pending),
                "catalog_updated_at": self.catalog.updated_at, "catalog_entries": len(self.catalog.entries),
                "catalog_errors": self.catalog.errors, "scorer_dropped": self.scorer.dropped,
                "last_duration_ms": self.last_duration_ms}

    async def aclose(self):
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
