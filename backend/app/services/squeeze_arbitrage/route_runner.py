from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import utc_iso
from .route_engine import LegSnapshot, Route, RouteEvaluation
from .route_provider import PublicRouteProvider
from .route_repository import SqueezeRouteRepository
from .route_tracker import RouteTracker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteMonitorConfig:
    poll_seconds: int = 5
    max_pending_scans: int = 30

    def __post_init__(self) -> None:
        if self.poll_seconds < 3 or self.poll_seconds > 10:
            raise ValueError("route polling interval must be 3-10 seconds")
        if self.max_pending_scans < 2 or self.max_pending_scans > 60:
            raise ValueError("route pending scan limit must be 2-60")


@dataclass(frozen=True)
class PendingScan:
    route: Route
    tracker: RouteTracker
    evaluation: RouteEvaluation
    expensive: LegSnapshot
    cheap: LegSnapshot
    transition: str | None


@dataclass(frozen=True)
class PendingSourceError:
    tracker: RouteTracker | None
    at: datetime
    error: str


class SqueezeRouteMonitor:
    def __init__(
        self, repo: SqueezeRouteRepository, config: RouteMonitorConfig,
        provider: PublicRouteProvider | None = None,
    ) -> None:
        self.repo = repo
        self.config = config
        self.provider = provider or PublicRouteProvider()
        self.tracker: RouteTracker | None = None
        self.running = False
        self.last_attempt_at: datetime | None = None
        self.last_collected_at: datetime | None = None
        self.last_error: str | None = None
        self.storage_error: str | None = None
        self.last_storage_error_at: datetime | None = None
        self.last_drop_at: datetime | None = None
        self._pending_storage_failures = 0
        self._pending_drops = 0
        self._queue: asyncio.Queue[PendingScan | PendingSourceError] = asyncio.Queue(
            maxsize=config.max_pending_scans
        )
        self._writer_task: asyncio.Task | None = None
        self._writer_busy = False

    async def aclose(self) -> None:
        await self.provider.aclose()

    async def status(self) -> dict:
        status = await self.repo.status(enabled=self.running)
        status["queue_depth"] = self._queue.qsize() + int(self._writer_busy)
        status["last_collected_at"] = (
            utc_iso(self.last_collected_at) if self.last_collected_at else None
        )
        status["storage_failure_count"] += self._pending_storage_failures
        status["dropped_scan_count"] += self._pending_drops
        if self._pending_storage_failures:
            status["last_storage_error_at"] = utc_iso(self.last_storage_error_at)
            status["last_storage_error"] = self.storage_error
        if self._pending_drops:
            status["last_drop_at"] = utc_iso(self.last_drop_at)
        if self.last_error is not None:
            if self.last_attempt_at is not None:
                status["last_attempt_at"] = utc_iso(self.last_attempt_at)
            status["last_error"] = self.last_error
        return status

    def _enqueue(self, item: PendingScan | PendingSourceError, at: datetime) -> bool:
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self._pending_drops += 1
            self.last_drop_at = at
            self.last_error = "route_write_queue_full"
            if self.tracker is not None:
                self.tracker.mark_source_gap()
            if self._pending_drops == 1 or self._pending_drops % 10 == 0:
                logger.error(
                    "squeeze route write queue full; dropped snapshots=%s",
                    self._pending_drops,
                )
            return False

    async def _write_loop(self) -> None:
        while True:
            item = await self._queue.get()
            self._writer_busy = True
            try:
                while True:
                    try:
                        if isinstance(item, PendingScan):
                            failures = self._pending_storage_failures
                            drops = self._pending_drops
                            await self.repo.save_scan(
                                item.route, item.tracker, item.evaluation,
                                item.expensive, item.cheap, item.transition,
                                storage_failures=failures,
                                last_storage_error_at=self.last_storage_error_at,
                                last_storage_error=self.storage_error,
                                dropped_scans=drops, last_drop_at=self.last_drop_at,
                            )
                            self._pending_storage_failures -= failures
                            self._pending_drops -= drops
                            self.storage_error = None
                            if self.last_error == "route_write_queue_full" or (
                                self.last_error and self.last_error.startswith("OperationalError:database is locked")
                            ):
                                self.last_error = None
                        else:
                            if item.tracker is not None:
                                await self.repo.save_tracker(item.tracker, item.at)
                            await self.repo.record_error(item.at, item.error)
                        break
                    except asyncio.CancelledError:
                        raise
                    except sqlite3.OperationalError as exc:
                        if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                            logger.exception("squeeze route write failed permanently")
                            self._drop_backlog()
                            break
                        self._pending_storage_failures += 1
                        self.last_storage_error_at = datetime.now(UTC)
                        self.storage_error = f"{type(exc).__name__}:{exc}"[:500]
                        self.last_error = self.storage_error
                        if self._pending_storage_failures == 1:
                            logger.warning("squeeze route storage busy; buffering scans")
                        await asyncio.sleep(1)
                    except Exception:
                        logger.exception("squeeze route write failed permanently")
                        self._drop_backlog()
                        break
            finally:
                self._writer_busy = False
                self._queue.task_done()

    def _drop_backlog(self) -> None:
        dropped = 1
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
            dropped += 1
        self._pending_drops += dropped
        self.last_drop_at = datetime.now(UTC)
        self.last_error = "route_write_failed_snapshots_dropped"
        if self.tracker is not None:
            self.tracker.mark_source_gap()

    async def scan_once(self, now: datetime | None = None) -> None:
        at = now or datetime.now(UTC)
        self.last_attempt_at = at
        try:
            route, expensive, cheap = await self.provider.fetch_route(at)
            if self.tracker is None:
                self.tracker = await self.repo.load_tracker(route.route_id)
            decision_at = now or datetime.now(UTC)
            next_tracker = RouteTracker.from_dict(self.tracker.to_dict())
            evaluation, transition = next_tracker.advance(
                route, expensive, cheap, decision_at
            )
            item = PendingScan(route, next_tracker, evaluation, expensive, cheap, transition)
            if self._writer_task is None:
                await self.repo.save_scan(
                    route, next_tracker, evaluation, expensive, cheap, transition
                )
                self.tracker = next_tracker
                self.last_collected_at = decision_at
                self.last_error = None
            elif self._enqueue(item, decision_at):
                self.tracker = next_tracker
                self.last_collected_at = decision_at
                self.last_error = self.storage_error
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("squeeze route scan failed")
            self.last_error = f"{type(exc).__name__}:{exc}"[:500]
            if self.tracker is not None:
                self.tracker.mark_source_gap()
            if self._writer_task is not None:
                tracker = (
                    RouteTracker.from_dict(self.tracker.to_dict())
                    if self.tracker is not None else None
                )
                self._enqueue(PendingSourceError(tracker, at, self.last_error), at)
            else:
                if self.tracker is not None:
                    try:
                        await self.repo.save_tracker(self.tracker, at)
                    except Exception:
                        logger.exception("squeeze route gap state could not be persisted")
                try:
                    await self.repo.record_error(at, self.last_error)
                except Exception:
                    logger.exception("squeeze route error state could not be persisted")

    async def run(self, stop_event: asyncio.Event) -> None:
        self.running = True
        self._writer_task = asyncio.create_task(self._write_loop(), name="squeeze-route-writer")
        try:
            while not stop_event.is_set():
                if self._writer_task.done():
                    try:
                        await self._writer_task
                    except asyncio.CancelledError:
                        logger.error("squeeze route writer was cancelled; restarting")
                    except Exception:
                        logger.exception("squeeze route writer stopped unexpectedly; restarting")
                    self._writer_task = asyncio.create_task(
                        self._write_loop(), name="squeeze-route-writer"
                    )
                await self.scan_once()
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self.config.poll_seconds
                    )
                except TimeoutError:
                    pass
        finally:
            self.running = False
            try:
                await asyncio.wait_for(self._queue.join(), timeout=3)
            except TimeoutError:
                logger.warning("squeeze route stopped with %s pending records", self._queue.qsize())
            self._writer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._writer_task
            self._writer_task = None
