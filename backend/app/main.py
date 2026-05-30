import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    routes_astro,
    routes_admin,
    routes_alerts,
    routes_funding_arbitrage,
    routes_health,
    routes_history,
    routes_index_components,
    routes_opportunities,
    routes_phone_alerts,
    routes_settings,
    stream,
)
from app.core.config import Settings, get_settings
from app.db.database import connect_database
from app.db.repositories import (
    AlertEventRepository,
    IndexComponentRepository,
    AlertRuleRepository,
    OpportunityHistoryRepository,
    PhonePriceAlertEventRepository,
    PhonePriceAlertRuleRepository,
    SettingsRepository,
)
from app.db.schema import initialize_schema
from app.models.alert import AlertEvent
from app.models.orderbook import DepthValidationResult
from app.models.phone_alert import PhonePriceAlertEvent
from app.models.settings import AlertMessageTemplateSettings, AstroCardSettings, LivePilotSettings, RiskSettings
from app.services.alert_engine import AlertEngine, AlertMatch, observations_are_stable
from app.services.alert_messages import build_alert_message
from app.services.alert_metrics import observe_alert_metrics
from app.services.astro_alerts import AstroAlertService
from app.services.astro_client import AstroSdkClient, AstroSdkConfig
from app.services.collector import MarketCollector, default_exchange_adapters, run_collector_loop
from app.services.data_filters import filter_opportunities
from app.services.feishu import FeishuConfig, FeishuNotifier
from app.services.history import OpportunityHistoryRecorder
from app.services.index_components import (
    BinanceIndexComponentProvider,
    BitgetIndexComponentProvider,
    BybitIndexComponentProvider,
    EmptyIndexComponentProvider,
    GateIndexComponentProvider,
    IndexComponentMonitor,
    MultiIndexComponentProvider,
    OKXIndexComponentProvider,
)
from app.services.live_pilot import (
    filter_opportunities_by_alert_rules,
    select_live_pilot_matches,
    select_live_pilot_opportunities,
)
from app.services.orderbook_validator import OrderBookDepthValidator
from app.services.phone_price_alerts import PhonePriceAlertEngine, build_phone_price_alert_message
from app.services.risk_labels import effective_open_edge_pct, known_volume_24h_usdt
from app.services.snapshot_store import SnapshotStore
from app.services.service_control import DockerServiceController, ServiceControlConfig

logger = logging.getLogger(__name__)


def _sqlite_path(settings: Settings) -> str:
    return settings.sqlite_path


def _ensure_database_parent(path: str) -> None:
    if path == ":memory:":
        return
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


async def _refresh_astro_runtime_settings(app: FastAPI, settings_repo: SettingsRepository | None) -> None:
    astro_alert_service: AstroAlertService | None = getattr(
        app.state,
        "astro_alert_service",
        None,
    )
    if astro_alert_service is None:
        return
    if not hasattr(astro_alert_service, "card_settings"):
        return
    fallback_settings = getattr(
        getattr(app.state, "settings", None),
        "astro_card_settings",
        getattr(astro_alert_service, "card_settings", None),
    )
    if settings_repo is None:
        astro_alert_service.card_settings = fallback_settings
        astro_alert_service.live_pilot_settings = LivePilotSettings()
        return
    find_settings = getattr(settings_repo, "find_astro_card_settings", None)
    stored = await find_settings() if find_settings is not None else None
    astro_alert_service.card_settings = stored or fallback_settings
    get_live_pilot_settings = getattr(settings_repo, "get_live_pilot_settings", None)
    astro_alert_service.live_pilot_settings = (
        await get_live_pilot_settings()
        if get_live_pilot_settings is not None
        else LivePilotSettings()
    )


def _find_latest_opportunity(app: FastAPI, opportunity_id: str):
    store = getattr(app.state, "snapshot_store", None)
    if store is None:
        return None
    return next((item for item in store.get_opportunities() if item.id == opportunity_id), None)


def _latest_signal_validation_failure(
    match: AlertMatch,
    latest,
    settings: RiskSettings,
    now: datetime,
) -> str | None:
    if latest is None:
        return "opportunity disappeared from the latest snapshot"
    if latest.open_spread_pct + 1e-9 < match.rule.min_open_spread_pct:
        return (
            f"open spread {latest.open_spread_pct:.3f}% is below rule threshold "
            f"{match.rule.min_open_spread_pct:.3f}%"
        )
    effective_edge = effective_open_edge_pct(latest, settings)
    required_edge = max(match.rule.min_fee_adjusted_open_pct, settings.min_effective_open_pct)
    if effective_edge + 1e-9 < required_edge:
        return (
            f"effective edge after slippage {effective_edge:.3f}% is below "
            f"{required_edge:.3f}%"
        )
    min_volume = known_volume_24h_usdt(latest)
    if min_volume is not None and min_volume < match.rule.min_volume_24h_usdt:
        return (
            f"24h volume {min_volume:.0f} USDT is below rule threshold "
            f"{match.rule.min_volume_24h_usdt:.0f} USDT"
        )
    if (now - latest.last_seen_at).total_seconds() > match.rule.max_data_age_seconds:
        return "latest market data is stale"
    excluded_labels = set(latest.risk_labels).intersection(match.rule.excluded_risk_labels)
    if excluded_labels:
        return f"latest opportunity has excluded risk labels: {', '.join(sorted(excluded_labels))}"

    observations = list(match.observations)
    if not observations or observations[-1].open_spread_pct != latest.open_spread_pct:
        observations.append(observe_alert_metrics(latest, now))
    if not observations_are_stable(observations, settings):
        return "open spread decayed too quickly across recent observations"
    return None


def _format_order_book_validation_failure(result: DepthValidationResult) -> str:
    details = "; ".join(result.blockers) if result.blockers else "depth validation failed"
    metrics: list[str] = [f"target {result.target_notional_usdt:.2f} USDT"]
    if result.executable_open_pct is not None:
        metrics.append(f"executable open {result.executable_open_pct:.3f}%")
    if result.effective_executable_edge_pct is not None:
        metrics.append(f"effective edge {result.effective_executable_edge_pct:.3f}%")
    return f"{details} ({', '.join(metrics)})"


def _exception_message(exc: BaseException) -> str:
    text = str(exc).strip()
    return text if text else exc.__class__.__name__


async def _order_book_validation_failure(
    app: FastAPI,
    opportunity,
    risk_settings: RiskSettings,
    card_settings: AstroCardSettings | None,
    override_notional_usdt: float | None = None,
) -> str | None:
    validator = getattr(app.state, "orderbook_validator", None)
    if validator is None:
        return None
    result = await validator.validate(
        opportunity,
        risk_settings=risk_settings,
        card_settings=card_settings,
        override_notional_usdt=override_notional_usdt,
    )
    if result.passed:
        return None
    return _format_order_book_validation_failure(result)


async def _run_alert_loop(app: FastAPI, interval_seconds: float, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            repo: AlertRuleRepository = app.state.alert_rule_repo
            event_repo: AlertEventRepository = app.state.alert_event_repo
            settings_repo: SettingsRepository | None = getattr(app.state, "settings_repo", None)
            rules = await repo.list()
            settings = await settings_repo.get_risk_settings() if settings_repo is not None else RiskSettings()
            alert_template = (
                await settings_repo.get_alert_message_template()
                if settings_repo is not None
                else AlertMessageTemplateSettings()
            )
            live_pilot_settings = (
                await settings_repo.get_live_pilot_settings()
                if settings_repo is not None
                else LivePilotSettings()
            )
            await _refresh_astro_runtime_settings(app, settings_repo)
            opportunities = filter_opportunities(app.state.snapshot_store.get_opportunities(), settings)
            opportunities = filter_opportunities_by_alert_rules(
                opportunities,
                rules,
                settings,
            )
            opportunities = select_live_pilot_opportunities(
                opportunities,
                live_pilot_settings,
                settings,
            )
            matches = app.state.alert_engine.evaluate(opportunities, rules, risk_settings=settings)
            matches = select_live_pilot_matches(matches, live_pilot_settings, settings)
            for match in matches:
                status = "sent"
                card_condition_failure = False
                message = build_alert_message(
                    match.rule,
                    match.opportunity,
                    observations=match.observations,
                    template=alert_template,
                )
                astro_alert_service: AstroAlertService | None = getattr(
                    app.state,
                    "astro_alert_service",
                    None,
                )
                if astro_alert_service is not None:
                    try:
                        latest_opportunity = _find_latest_opportunity(app, match.opportunity.id)
                        validation_failure = _latest_signal_validation_failure(
                            match,
                            latest_opportunity,
                            settings,
                            datetime.now(UTC),
                        )
                        if validation_failure is not None:
                            card_condition_failure = True
                            message = (
                                f"{message}\n\n"
                                f"Astro: skipped latest signal validation: {validation_failure}"
                            )
                        else:
                            card_settings = getattr(astro_alert_service, "card_settings", None)
                            validation_notional = (
                                live_pilot_settings.notional_per_symbol_usdt
                                if live_pilot_settings.enabled
                                else None
                            )
                            order_book_failure = await _order_book_validation_failure(
                                app,
                                latest_opportunity,
                                settings,
                                card_settings,
                                validation_notional,
                            )
                            if order_book_failure is not None:
                                card_condition_failure = True
                                message = (
                                    f"{message}\n\n"
                                    f"Astro: skipped order book validation: {order_book_failure}"
                                )
                            else:
                                astro_result = await astro_alert_service.handle_alert(latest_opportunity)
                                message = f"{message}\n\n{astro_result.format_message()}"
                    except Exception as exc:  # noqa: BLE001 - keep alert delivery independent.
                        logger.exception("astro alert follow-up failed")
                        message = f"{message}\n\nAstro: 处理失败，{_exception_message(exc)}"
                if alert_template.suppress_when_card_conditions_fail and card_condition_failure:
                    status = "muted"
                else:
                    try:
                        await app.state.feishu_notifier.send_alert(
                            match.rule,
                            match.opportunity,
                            observations=match.observations,
                            template=alert_template,
                            prebuilt_text=message,
                        )
                    except Exception as exc:  # noqa: BLE001 - preserve event even when webhook fails.
                        status = "failed"
                        message = f"{message}\n\n飞书发送失败：{exc}"
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


async def _send_index_component_alert(app: FastAPI, message: str) -> None:
    notifier = getattr(app.state, "feishu_notifier", None)
    if notifier is None:
        return
    await notifier.send_text(message)


async def _run_phone_price_alert_loop(
    app: FastAPI,
    interval_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            repo: PhonePriceAlertRuleRepository = app.state.phone_price_alert_rule_repo
            event_repo: PhonePriceAlertEventRepository = app.state.phone_price_alert_event_repo
            engine: PhonePriceAlertEngine = getattr(
                app.state,
                "phone_price_alert_engine",
                PhonePriceAlertEngine(),
            )
            app.state.phone_price_alert_engine = engine
            now = datetime.now(UTC)
            matches = engine.evaluate(
                app.state.snapshot_store.get_markets(),
                await repo.list(),
                now=now,
            )
            for match in matches:
                status = "sent"
                message = build_phone_price_alert_message(match, observed_at=now)
                try:
                    await app.state.feishu_notifier.send_phone_urgent_text(message)
                except Exception as exc:  # noqa: BLE001 - keep event history even if phone fails.
                    status = "failed"
                    message = f"{message}\n\nFeishu phone urgent failed: {exc}"
                await event_repo.create(
                    PhonePriceAlertEvent(
                        rule_id=match.rule.id,
                        symbol=match.market.symbol,
                        exchange=match.market.exchange,
                        market_type=match.market.market_type,
                        price_field=match.resolved_price_field,
                        condition=match.rule.condition,
                        target_price=match.rule.target_price,
                        observed_price=match.observed_price,
                        status=status,
                        message=message,
                        created_at=now,
                    )
                )
        except Exception:
            logger.exception("phone price alert loop failed")
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
        app.state.phone_price_alert_rule_repo = PhonePriceAlertRuleRepository(db)
        app.state.phone_price_alert_event_repo = PhonePriceAlertEventRepository(db)
        app.state.settings_repo = SettingsRepository(db)
        app.state.history_repo = OpportunityHistoryRepository(db)
        app.state.index_component_repo = IndexComponentRepository(db)
        tasks: list[asyncio.Task] = []
        collector: MarketCollector | None = None
        if start_collector:
            exchange_adapters = default_exchange_adapters()
            history_recorder = OpportunityHistoryRecorder(
                app.state.history_repo,
                app_settings.history_settings,
            )
            index_component_monitor = IndexComponentMonitor(
                app.state.index_component_repo,
                alert_sender=lambda message: _send_index_component_alert(app, message),
            )
            app.state.index_component_monitor = index_component_monitor
            index_component_provider_classes = {
                "binance": BinanceIndexComponentProvider,
                "okx": OKXIndexComponentProvider,
                "bybit": BybitIndexComponentProvider,
                "bitget": BitgetIndexComponentProvider,
                "gate": GateIndexComponentProvider,
            }
            index_component_providers = [
                index_component_provider_classes[adapter.name](adapter)
                for adapter in exchange_adapters
                if adapter.name in index_component_provider_classes
            ]
            app.state.index_component_provider = (
                MultiIndexComponentProvider(index_component_providers)
                if index_component_providers
                else EmptyIndexComponentProvider()
            )
            collector = MarketCollector(
                exchange_adapters,
                store,
                risk_settings_loader=app.state.settings_repo.get_risk_settings,
                history_recorder=history_recorder,
                index_component_provider=app.state.index_component_provider,
                index_component_monitor=index_component_monitor,
                poll_interval_seconds=app_settings.poll_interval_seconds,
            )
            app.state.market_collector = collector
            app.state.orderbook_validator = OrderBookDepthValidator(exchange_adapters)
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
            if app_settings.feishu_phone_enabled:
                tasks.append(
                    asyncio.create_task(
                        _run_phone_price_alert_loop(
                            app,
                            app_settings.poll_interval_seconds,
                            stop_event,
                        )
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
            astro_client = getattr(app.state, "astro_client", None)
            if astro_client is not None:
                await astro_client.aclose()
            service_controller = getattr(app.state, "service_controller", None)
            if service_controller is not None:
                close = getattr(service_controller, "aclose", None)
                if close is not None:
                    await close()
            await app.state.feishu_notifier.client.aclose()
            await db.close()

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.snapshot_store = store
    app.state.market_collector = None
    app.state.orderbook_validator = None
    app.state.alert_engine = AlertEngine()
    app.state.phone_price_alert_engine = PhonePriceAlertEngine()
    app.state.astro_client = AstroSdkClient(
        AstroSdkConfig(
            base_url=app_settings.astro_sdk_base_url,
            admin_prefix=app_settings.astro_admin_prefix,
            api_key=app_settings.astro_api_key,
            verify_tls=app_settings.astro_verify_tls,
            timeout_seconds=app_settings.astro_request_timeout_seconds,
        )
    )
    app.state.astro_alert_service = AstroAlertService(app.state.astro_client, app_settings)
    app.state.service_controller = DockerServiceController(
        ServiceControlConfig(
            enabled=app_settings.service_control_enabled,
            environment=app_settings.environment,
            compose_project_name=app_settings.compose_project_name,
            docker_socket_path=app_settings.service_control_docker_socket_path,
            restart_delay_seconds=app_settings.service_control_restart_delay_seconds,
        )
    )
    app.state.feishu_notifier = FeishuNotifier(
        FeishuConfig(
            webhook_url=app_settings.feishu_webhook_url,
            secret=app_settings.feishu_secret,
            app_id=app_settings.feishu_app_id,
            app_secret=app_settings.feishu_app_secret,
            alert_chat_id=app_settings.feishu_alert_chat_id,
            phone_user_ids=[
                item.strip()
                for item in app_settings.feishu_phone_user_ids.split(",")
                if item.strip()
            ],
            phone_user_id_type=app_settings.feishu_phone_user_id_type,
            phone_enabled=app_settings.feishu_phone_enabled,
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
    app.include_router(routes_astro.router, prefix="/api")
    app.include_router(routes_opportunities.router, prefix="/api")
    app.include_router(routes_history.router, prefix="/api")
    app.include_router(routes_index_components.router, prefix="/api")
    app.include_router(routes_alerts.router, prefix="/api")
    app.include_router(routes_phone_alerts.router, prefix="/api")
    app.include_router(routes_funding_arbitrage.router, prefix="/api")
    app.include_router(routes_settings.router, prefix="/api")
    app.include_router(routes_admin.router, prefix="/api")
    app.include_router(stream.router, prefix="/api")
    return app


app = create_app(start_collector=True)
