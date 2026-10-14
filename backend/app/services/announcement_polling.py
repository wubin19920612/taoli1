"""Independent announcement polling with durable source-visibility evidence."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.repositories import AnnouncementRepository
    from app.services.announcements import AnnouncementMonitor, AnnouncementProvider, SettingsLoader

logger = logging.getLogger(__name__)


def poll_state_key(provider: AnnouncementProvider) -> str:
    return f"poll:{provider.exchange}:{type(provider).__name__}"


async def _record_poll(
    repository: AnnouncementRepository, provider: AnnouncementProvider, observation: dict,
) -> None:
    key = poll_state_key(provider)
    previous = await repository.get_provider_state(key) or {}
    history = previous.get("history", [])
    signature = {name: observation.get(name) for name in (
        "status", "error", "articles", "source_articles", "source_errors", "localize_error",
    )}
    if history and history[-1].get("result") == signature:
        history[-1]["last_at"] = observation["finished_at"]
        history[-1]["polls"] += 1
    else:
        history.append({"first_at": observation["started_at"],
                        "last_at": observation["finished_at"], "polls": 1, "result": signature})
    await repository.set_provider_state(key, {
        "exchange": provider.exchange, "provider": type(provider).__name__,
        "latest": observation, "history": history[-120:],
    })


async def _wait(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0.001, seconds))
    except TimeoutError:
        pass


async def run_announcement_sources(
    providers: list[AnnouncementProvider], monitor: AnnouncementMonitor,
    settings_loader: SettingsLoader, stop_event: asyncio.Event, *,
    min_interval_seconds: float = 30.0, fetch_timeout_seconds: float = 20.0,
    localize_timeout_seconds: float = 3.0,
) -> None:
    async def poll_source(provider: AnnouncementProvider) -> None:
        while not stop_event.is_set():
            started = time.monotonic()
            interval = min_interval_seconds
            observation = {"started_at": datetime.now(UTC).isoformat()}
            active = False
            try:
                settings = await settings_loader()
                interval = max(min_interval_seconds, float(settings.poll_interval_seconds))
                active = settings.enabled and provider.exchange in settings.record_exchanges
                if active:
                    bootstrap = not await monitor.repository.has_any()
                    fetch_start = time.monotonic()
                    try:
                        async with asyncio.timeout(fetch_timeout_seconds):
                            rows = await provider.fetch_headlines()
                    finally:
                        observation["source_articles"] = getattr(provider, "last_source_articles", {})
                        observation["source_errors"] = getattr(provider, "last_source_errors", {})
                    observation["fetch_seconds"] = round(time.monotonic() - fetch_start, 3)
                    observation["articles"] = {
                        row.announcement_id: row.published_at.isoformat() for row in rows
                    }
                    localize_start = time.monotonic()
                    localize = getattr(provider, "localize", None)
                    if localize:
                        try:
                            async with asyncio.timeout(localize_timeout_seconds):
                                rows = await localize(rows)
                        except Exception as exc:
                            # Translation is optional; never discard discovered headlines.
                            observation["localize_error"] = type(exc).__name__
                            logger.warning("Announcement localization failed provider=%s",
                                           type(provider).__name__, exc_info=True)
                    observation["localize_seconds"] = round(time.monotonic() - localize_start, 3)
                    process_start = time.monotonic()
                    created = await monitor.process(rows, settings, bootstrap=bootstrap)
                    observation.update({
                        "status": "ok", "row_count": len(rows), "new_count": len(created),
                        "process_seconds": round(time.monotonic() - process_start, 3),
                    })
            except Exception as exc:
                observation.update({"status": "error", "error": type(exc).__name__})
                logger.warning("Announcement poll failed provider=%s error=%s",
                               type(provider).__name__, type(exc).__name__, exc_info=True)
            if active:
                observation.update({"finished_at": datetime.now(UTC).isoformat(),
                                    "total_seconds": round(time.monotonic() - started, 3)})
                try:
                    await _record_poll(monitor.repository, provider, observation)
                except Exception:
                    # Diagnostics must never prevent subsequent announcement polls.
                    logger.exception("Failed to record announcement poll evidence")
            await _wait(stop_event, interval - (time.monotonic() - started))

    async def poll_reminders() -> None:
        # One owner avoids concurrent duplicate reminder sends across sources.
        while not stop_event.is_set():
            interval = min_interval_seconds
            try:
                settings = await settings_loader()
                interval = max(min_interval_seconds, float(settings.poll_interval_seconds))
                if settings.enabled:
                    await monitor.process_due_event_reminders(settings)
            except Exception:
                logger.exception("Announcement event reminder loop failed")
            await _wait(stop_event, interval)

    tasks = [asyncio.create_task(poll_source(provider)) for provider in providers]
    tasks.append(asyncio.create_task(poll_reminders()))
    try:
        await stop_event.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
