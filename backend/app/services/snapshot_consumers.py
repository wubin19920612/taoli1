"""Single-event-loop, latest-only mailboxes for read-only snapshot consumers."""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from app.models.market import MarketSnapshot
from app.models.opportunity import Opportunity
from app.runtime.supervisor import beat, distribution

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishedSnapshot:
    generation: int
    published_at: datetime
    markets: tuple[MarketSnapshot, ...]
    opportunities: tuple[Opportunity, ...]


class LatestSnapshotSlot:
    """One subscriber per slot; putting never waits for the subscriber."""

    def __init__(self) -> None:
        self._latest: PublishedSnapshot | None = None
        self._pending = False
        self._dropped = 0
        self._changed = asyncio.Event()

    def put(self, snapshot: PublishedSnapshot) -> None:
        if self._pending:
            self._dropped += 1
        self._latest = snapshot
        self._pending = True
        self._changed.set()

    async def get_newer(
        self, after_generation: int, stop_event: asyncio.Event,
    ) -> PublishedSnapshot | None:
        while not stop_event.is_set():
            if self._latest is not None and self._latest.generation > after_generation:
                self._pending = False
                return self._latest
            # No await between inspecting the latest generation and clearing.
            self._changed.clear()
            changed = asyncio.create_task(self._changed.wait())
            stopped = asyncio.create_task(stop_event.wait())
            try:
                await asyncio.wait((changed, stopped), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (changed, stopped):
                    task.cancel()
                await asyncio.gather(changed, stopped, return_exceptions=True)
        return None

    def stats(self) -> dict[str, int | None]:
        return {
            "latest_generation": self._latest.generation if self._latest is not None else None,
            "dropped": self._dropped,
        }


class SnapshotConsumer:
    def __init__(
        self, name: str, slot: LatestSnapshotSlot,
        handler: Callable[[PublishedSnapshot], Awaitable[None]],
        now_fn: Callable[[], float] = time.monotonic, window: int = 120,
    ) -> None:
        self.name = name
        self.slot = slot
        self.handler = handler
        self._now = now_fn
        self._durations: deque[float] = deque(maxlen=max(1, window))
        self._processed_generation = 0
        self._processed = 0
        self._failed = 0

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            snapshot = await self.slot.get_newer(self._processed_generation, stop_event)
            if snapshot is None:
                return
            started = self._now()
            try:
                await self.handler(snapshot)
            except Exception:
                self._failed += 1
                logger.exception("snapshot consumer %s failed at generation %s", self.name, snapshot.generation)
                beat(self.name, ok=False)
            else:
                self._processed += 1
                beat(self.name, ok=True)
            # A failed generation is consumed too. Cancellation propagates without
            # advancing so a supervisor restart may retry the interrupted snapshot.
            self._processed_generation = snapshot.generation
            self._durations.append(max(0.0, self._now() - started))

    def stats(self) -> dict[str, object]:
        latest = self.slot.stats()["latest_generation"]
        return {
            "processed_generation": self._processed_generation,
            "processed": self._processed,
            "failed": self._failed,
            "last_duration_seconds": self._durations[-1] if self._durations else None,
            "duration_p95_seconds": distribution(list(self._durations))["p95"],
            "lag_generations": (latest or 0) - self._processed_generation,
        }
