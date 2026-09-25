from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

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

    async def aclose(self) -> None:
        await self.provider.aclose()

    async def scan_once(self, now: datetime | None = None) -> None:
        at = now or datetime.now(UTC)
        try:
            route, expensive, cheap = await self.provider.fetch_route(at)
            if self.tracker is None:
                self.tracker = await self.repo.load_tracker(route.route_id)
            decision_at = now or datetime.now(UTC)
            evaluation, transition = self.tracker.advance(
                route, expensive, cheap, decision_at
            )
            await self.repo.save_scan(
                route, self.tracker, evaluation, expensive, cheap, transition
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("squeeze route scan failed")
            if self.tracker is not None:
                self.tracker.mark_source_gap()
                await self.repo.save_tracker(self.tracker, at)
            await self.repo.record_error(
                at, f"{type(exc).__name__}:{exc}"
            )

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
