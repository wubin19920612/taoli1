from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import utc_iso
from .route_provider import PublicRouteProvider
from .route_repository import SqueezeRouteRepository
from .route_tracker import RouteTracker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteMonitorConfig:
    poll_seconds: int = 5

    def __post_init__(self) -> None:
        if self.poll_seconds < 3 or self.poll_seconds > 10:
            raise ValueError("route polling interval must be 3-10 seconds")


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
        self.last_error: str | None = None

    async def aclose(self) -> None:
        await self.provider.aclose()

    async def status(self) -> dict:
        status = await self.repo.status(enabled=self.running)
        if self.last_error is not None:
            status["last_attempt_at"] = utc_iso(self.last_attempt_at)
            status["last_error"] = self.last_error
        return status

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
            await self.repo.save_scan(
                route, next_tracker, evaluation, expensive, cheap, transition
            )
            self.tracker = next_tracker
            self.last_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("squeeze route scan failed")
            self.last_error = f"{type(exc).__name__}:{exc}"[:500]
            if self.tracker is not None:
                self.tracker.mark_source_gap()
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
        try:
            while not stop_event.is_set():
                await self.scan_once()
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self.config.poll_seconds
                    )
                except TimeoutError:
                    pass
        finally:
            self.running = False
