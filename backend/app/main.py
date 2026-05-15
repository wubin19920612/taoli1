import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_alerts, routes_health, routes_opportunities, routes_settings, stream
from app.core.config import Settings, get_settings
from app.db.database import connect_database
from app.db.repositories import AlertEventRepository, AlertRuleRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.alert import AlertEvent
from app.services.alert_engine import AlertEngine
from app.services.collector import MarketCollector, default_exchange_adapters, run_collector_loop
from app.services.feishu import FeishuConfig, FeishuNotifier
from app.services.snapshot_store import SnapshotStore

logger = logging.getLogger(__name__)


def _sqlite_path(settings: Settings) -> str:
    return settings.sqlite_path


def _ensure_database_parent(path: str) -> None:
    if path == ":memory:":
        return
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


async def _run_alert_loop(app: FastAPI, interval_seconds: float, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            repo: AlertRuleRepository = app.state.alert_rule_repo
            event_repo: AlertEventRepository = app.state.alert_event_repo
            rules = await repo.list()
            opportunities = app.state.snapshot_store.get_opportunities()
            matches = app.state.alert_engine.evaluate(opportunities, rules)
            for match in matches:
                status = "sent"
                message = (
                    f"{match.opportunity.symbol} {match.opportunity.type} "
                    f"{match.opportunity.open_spread_pct:.3f}%"
                )
                try:
                    await app.state.feishu_notifier.send_alert(match.rule, match.opportunity)
                except Exception as exc:  # noqa: BLE001 - preserve event even when webhook fails.
                    status = "failed"
                    message = f"{message}; Feishu failed: {exc}"
                await event_repo.create(
                    AlertEvent(
                        rule_id=match.rule.id,
                        opportunity_id=match.opportunity.id,
                        symbol=match.opportunity.symbol,
                        status=status,
                        message=message,
                        created_at=datetime.now(UTC),
                    )
                )
        except Exception:
            logger.exception("alert loop failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


def create_app(
    snapshot_store: SnapshotStore | None = None,
    settings: Settings | None = None,
    start_collector: bool = False,
) -> FastAPI:
    app_settings = settings or get_settings()
    store = snapshot_store or SnapshotStore()
    stop_event = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _ensure_database_parent(_sqlite_path(app_settings))
        db = await connect_database(_sqlite_path(app_settings))
        await initialize_schema(db)
        app.state.db = db
        app.state.alert_rule_repo = AlertRuleRepository(db)
        app.state.alert_event_repo = AlertEventRepository(db)
        app.state.settings_repo = SettingsRepository(db)
        tasks: list[asyncio.Task] = []
        collector: MarketCollector | None = None
        if start_collector:
            collector = MarketCollector(default_exchange_adapters(), store)
            tasks.append(
                asyncio.create_task(
                    run_collector_loop(collector, app_settings.poll_interval_seconds, stop_event)
                )
            )
            tasks.append(
                asyncio.create_task(
                    _run_alert_loop(app, app_settings.poll_interval_seconds, stop_event)
                )
            )
        try:
            yield
        finally:
            stop_event.set()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            if collector is not None:
                await collector.close()
            await app.state.feishu_notifier.client.aclose()
            await db.close()

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.snapshot_store = store
    app.state.alert_engine = AlertEngine()
    app.state.feishu_notifier = FeishuNotifier(
        FeishuConfig(
            webhook_url=app_settings.feishu_webhook_url,
            secret=app_settings.feishu_secret,
        )
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(routes_health.router, prefix="/api")
    app.include_router(routes_opportunities.router, prefix="/api")
    app.include_router(routes_alerts.router, prefix="/api")
    app.include_router(routes_settings.router, prefix="/api")
    app.include_router(stream.router, prefix="/api")
    return app


app = create_app(start_collector=True)
