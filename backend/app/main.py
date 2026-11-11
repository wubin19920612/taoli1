from app.api import routes_auth
from app.db.auth import AuthStore
import asyncio
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import AsyncIterator, Iterator
from uuid import NAMESPACE_URL, uuid5

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.runtime.gc_pause import GcPauseMonitor
from app.runtime.gc_tuning import start_gc_tuning
from app.runtime.loop_stall_watchdog import LoopStallWatchdog
from app.runtime.supervisor import TaskSupervisor, beat

from app.api import (
    routes_account_connections,
    routes_account_positions,
    routes_announcements,
    routes_astro,
    routes_arb_mvp,
    routes_live_exec,
    routes_admin,
    routes_alerts,
    routes_features,
    routes_funding_arbitrage,
    routes_gate_twap,
    routes_health,
    routes_history,
    routes_hyperliquid_trade_status,
    routes_index_components,
    routes_instruments,
    routes_opportunities,
    routes_pair_spread,
    routes_premium_index,
    routes_phone_alerts,
    routes_settings,
    routes_market_identities,
    routes_trade_availability,
    stream,
)
from app.core.config import Settings, get_settings
from app.core.security import require_dashboard_password
from app.db.database import connect_database
from app.db.repositories import (
    AccountConnectionRepository,
    AlertEventRepository,
    AnnouncementRepository,
    IndexComponentRepository,
    AlertRuleRepository,
    OpportunityHistoryRepository,
    PhonePriceAlertEventRepository,
    PhonePriceAlertRuleRepository,
    SettingsRepository,
)
from app.db.schema import initialize_schema
from app.exchanges.hyperliquid import HyperliquidAdapter
from app.exchanges.hyperliquid_bbo import HyperliquidBboStream
from app.exchanges.gate import GateAdapter
from app.services.gate_bbo_runtime import GateBboRuntime
from app.services.astro_card_attempts import AstroCardAttemptRepository
from app.db.market_identity_repository import MarketIdentityRepository
from app.services.market_validation_review import AlertFilterChanged, MarketValidationService
from app.services.market_identity_catalog import OfficialMarketIdentityCatalog
from app.models.alert import AlertEvent
from app.models.orderbook import DepthValidationResult
from app.models.phone_alert import PhonePriceAlertEvent
from app.models.settings import AlertMessageTemplateSettings, AstroCardSettings, RiskSettings
from app.services.account_positions import AccountPositionService, GateAccountPositionProvider
from app.services.account_connections import AccountConnectionService
from app.services.alert_delivery_store import AlertDeliveryStore
from app.services.alert_engine import (
    AlertEngine,
    AlertMatch,
    filter_opportunities_by_alert_rules,
    observations_are_stable,
    required_open_spread_pct,
    suppresses_sf_negative_funding,
)
from app.services.alert_messages import build_alert_message, build_alert_rating_header
from app.services.alert_metrics import observe_alert_metrics
from app.services.announcements import (
    AnnouncementMonitor,
    default_announcement_provider,
    run_announcement_enrichment_loop,
    run_announcement_loop,
)
from app.services.astro_alerts import AstroAlertService
from app.services.astro_client import AstroSdkClient, AstroSdkConfig
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig
from app.services.astro_preadd import AstroPreaddService
from app.services.collector import MarketCollector, default_exchange_adapters, run_collector_loop
from app.services.data_filters import filter_opportunities
from app.services.feishu import FeishuConfig, FeishuNotifier
from app.services.gate_crossex_hyperliquid import GateCrossExHyperliquidCatalog
from app.services.gate_twap import GateTwapClient, GateTwapJobManager
from app.services.history import OpportunityHistoryRecorder
from app.services.hyperliquid_trade_status import (
    HyperliquidTradeStatusMonitor,
    HyperliquidTradeStatusService,
    HyperliquidTradeStatusWatchRepository,
)
from app.services.index_components import (
    IndexComponentAutoWatchService,
    BinanceIndexComponentProvider,
    BitgetIndexComponentProvider,
    BybitIndexComponentProvider,
    EmptyIndexComponentProvider,
    GateIndexComponentProvider,
    IndexComponentMonitor,
    MultiIndexComponentProvider,
    OKXIndexComponentProvider,
)
from app.services.instrument_market_cap import InstrumentMarketCapService
from app.services.instrument_statistics import InstrumentStatisticsService
from app.services.orderbook_validator import OrderBookDepthValidator
from app.services.opportunity_trade_availability import (
    OpportunityTradeAvailabilityReport,
    build_opportunity_trade_availability_report,
)
from app.services.pair_spread_funding_recorder import PairSpreadFundingRecorder, PairSpreadFundingRepository
from app.services.pair_spread_presets import PairSpreadPresetRepository
from app.services.phone_price_alerts import PhonePriceAlertEngine, build_phone_price_alert_message
from app.services.multi_exchange_premium import MultiExchangePremiumMonitor
from app.services.risk_labels import effective_open_edge_pct, known_volume_24h_usdt
from app.services.snapshot_store import SnapshotStore
from app.services.snapshot_consumers import LatestSnapshotSlot, SnapshotConsumer
from app.services.service_control import DockerServiceController, ServiceControlConfig
from app.services.shadow_runtime import ShadowRuntime
from app.api import routes_shadow
from app.services.trade_availability import (
    TradeAvailabilityMonitor,
    TradeAvailabilityService,
    TradeAvailabilityWatchRepository,
)

logger = logging.getLogger(__name__)
alert_timing_logger = logging.getLogger("alert_timing")
alert_timing_logger.setLevel(logging.INFO)
alert_timing_logger.propagate = False
if not alert_timing_logger.handlers:
    # Emit plain JSON even when the application's root logger filters out INFO.
    alert_timing_logger.addHandler(logging.StreamHandler())
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
    fallback_automation = getattr(
        getattr(app.state, "settings", None),
        "astro_automation_settings",
        None,
    )
    if settings_repo is None:
        astro_alert_service.card_settings = fallback_settings
        if fallback_automation is not None:
            astro_alert_service.alert_auto_create_enabled = fallback_automation.alert_auto_create
            astro_alert_service.allow_same_name_variants = (
                fallback_automation.allow_same_name_variants
            )
        return
    find_settings = getattr(settings_repo, "find_astro_card_settings", None)
    stored = await find_settings() if find_settings is not None else None
    astro_alert_service.card_settings = stored or fallback_settings
    find_automation = getattr(settings_repo, "find_astro_automation_settings", None)
    stored_automation = await find_automation() if find_automation is not None else None
    if stored_automation is not None:
        astro_alert_service.alert_auto_create_enabled = stored_automation.alert_auto_create
        astro_alert_service.allow_same_name_variants = (
            stored_automation.allow_same_name_variants
        )
    elif fallback_automation is not None:
        astro_alert_service.alert_auto_create_enabled = fallback_automation.alert_auto_create
        astro_alert_service.allow_same_name_variants = (
            fallback_automation.allow_same_name_variants
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
        return "最新快照中已找不到该机会"
    if suppresses_sf_negative_funding(match.rule, latest):
        return "SF 卖出侧资金费率已转负"
    required_spread = required_open_spread_pct(match.rule, latest)
    if latest.open_spread_pct + 1e-9 < required_spread:
        return (
            f"开仓价差 {latest.open_spread_pct:.3f}% 低于规则阈值 "
            f"{required_spread:.3f}%"
        )
    effective_edge = effective_open_edge_pct(latest, settings)
    required_edge = max(match.rule.min_fee_adjusted_open_pct, settings.min_effective_open_pct)
    if effective_edge + 1e-9 < required_edge:
        return (
            f"滑点后有效收益 {effective_edge:.3f}% 低于最低要求 "
            f"{required_edge:.3f}%"
        )
    min_volume = known_volume_24h_usdt(latest)
    if min_volume is not None and min_volume < match.rule.min_volume_24h_usdt:
        return (
            f"24h 成交额 {min_volume:.0f} USDT 低于规则阈值 "
            f"{match.rule.min_volume_24h_usdt:.0f} USDT"
        )
    if (now - latest.last_seen_at).total_seconds() > match.rule.max_data_age_seconds:
        return "最新行情数据已过期"
    excluded_labels = set(latest.risk_labels).intersection(match.rule.excluded_risk_labels)
    if excluded_labels:
        return f"最新机会包含已排除的风险标签：{', '.join(sorted(excluded_labels))}"

    observations = list(match.observations)
    if not observations or observations[-1].open_spread_pct != latest.open_spread_pct:
        observations.append(observe_alert_metrics(latest, now))
    if not observations_are_stable(observations, settings):
        return "最近几次观察中开仓价差衰减过快"
    return None


def _signed_pct(value: float) -> str:
    return f"{value:+.3f}%"


def _format_order_book_validation_failure(result: DepthValidationResult) -> str:
    details = "；".join(result.blockers) if result.blockers else "深度校验未通过"
    metrics: list[str] = [f"验证金额 {result.target_notional_usdt:.2f} USDT"]
    if result.price_band_pct is not None:
        metrics.append(f"价格带 {result.price_band_pct:.3f}%")
    if result.required_depth_usdt is not None:
        metrics.append(f"要求深度 {result.required_depth_usdt:.2f} USDT")
    if result.min_depth_usdt is not None:
        metrics.append(f"最小价格带深度 {result.min_depth_usdt:.2f} USDT")
    if result.executable_open_pct is not None:
        metrics.append(f"实际可成交开仓价差 {_signed_pct(result.executable_open_pct)}")
    if result.cost_pct is not None:
        metrics.append(f"成本修正 {_signed_pct(-result.cost_pct)}")
    if result.funding_edge_pct is not None:
        metrics.append(f"资金费边际 {_signed_pct(result.funding_edge_pct)}")
    if result.slippage_buffer_pct is not None:
        metrics.append(f"滑点缓冲 {_signed_pct(-result.slippage_buffer_pct)}")
    if result.effective_executable_edge_pct is not None:
        metrics.append(f"实际可成交有效收益 {_signed_pct(result.effective_executable_edge_pct)}")
    return f"{details}（{'，'.join(metrics)}）"


def _astro_plan_validation_failure(
    opportunity,
    card_settings: AstroCardSettings | None,
) -> str | None:
    plan = AstroPairPlanner(
        AstroPlannerConfig.from_card_settings(card_settings or AstroCardSettings())
    ).plan(opportunity)
    if plan.can_submit:
        return None
    return "; ".join(plan.blockers) if plan.blockers else "Astro pair cannot be submitted"


def _astro_card_settings_for_opportunity(
    astro_alert_service: AstroAlertService,
    opportunity,
) -> AstroCardSettings | None:
    return getattr(astro_alert_service, "card_settings", None)


def _exception_message(exc: BaseException) -> str:
    text = str(exc).strip()
    return text if text else exc.__class__.__name__


async def _close_state_resources(app: FastAPI, *names: str) -> None:
    for name in names:
        resource = getattr(app.state, name, None)
        if resource is None:
            continue
        close = getattr(resource, "aclose", None)
        if close is None:
            continue
        try:
            await close()
        except Exception:  # noqa: BLE001 - close every resource during shutdown.
            logger.exception("failed to close app state resource %s", name)


def _start_background_task(
    supervisor: TaskSupervisor,
    coro_factory,
    *,
    name: str,
) -> None:
    supervisor.start(
        name, coro_factory, restart=True,
        critical=name in {"market-collector", "alert-loop"},
    )


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


async def _restore_alert_delivery_state(app: FastAPI) -> None:
    store: AlertDeliveryStore = app.state.alert_delivery_store
    engine: AlertEngine = app.state.alert_engine
    now = datetime.now(UTC)
    # Failure to load must prevent the loop from starting with empty cooldowns.
    states = await store.load_all()
    for key in engine.restore(states, now):
        state = engine.export_state(key)
        rule_id, _, opportunity_id = key.partition(":")
        event = AlertEvent(
            # Stable across crashes between event insertion and clearing pending.
            id=uuid5(
                NAMESPACE_URL, f"alert-delivery-unknown:{key}:{state.last_sent_at.isoformat()}",
            ).hex,
            rule_id=rule_id,
            opportunity_id=opportunity_id,
            symbol="未知",
            status="unknown",
            # This existing prefix keeps the history API from rebuilding the message.
            message=(
                "【需评估】\n上次发送过程中进程退出，结果未知，按已发送处理，不重发"
                f"\n告警键：{key}"
            ),
            created_at=now,
        )
        try:
            await app.state.alert_event_repo.create(event)
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorcode != sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY:
                logger.exception("unknown alert event write failed: %s", key)
                continue
            # The same recovery event was committed before an earlier crash.
        except Exception:
            logger.exception("unknown alert event write failed: %s", key)
            continue
        try:
            await store.record(key, state.last_sent_at, state.retry_after, state.retry_attempts)
        except Exception:
            logger.exception("recovered alert delivery state write failed: %s", key)


async def _process_alert_match(
    app: FastAPI,
    match: AlertMatch,
    settings: RiskSettings,
    alert_template: AlertMessageTemplateSettings,
    delivery_store: AlertDeliveryStore,
    event_repo: AlertEventRepository,
    write_lock: asyncio.Lock,
    app_settings: Settings,
) -> bool:
    # Only fixed metadata and durations belong in this log, never messages/settings.
    key = f"{match.rule.id}:{match.opportunity.id}"
    started = monotonic()
    durations: dict[str, float | None] = {
        "diag_ms": None, "orderbook_ms": None, "astro_ms": None, "feishu_ms": None,
    }
    timeouts: list[str] = []

    @contextmanager
    def timed(stage: str) -> Iterator[None]:
        stage_started = monotonic()
        try:
            yield
        finally:
            durations[f"{stage}_ms"] = (monotonic() - stage_started) * 1000

    status = "sent"
    try:
        card_summary = "未创建（自动建卡未启用）"
        card_condition_failure = False
        existing_card_skipped = False
        signal_condition_failure = False
        latest_opportunity = _find_latest_opportunity(app, match.opportunity.id)
        message = build_alert_message(
            match.rule,
            latest_opportunity or match.opportunity,
            observations=match.observations,
            template=alert_template,
            include_rating=False,
        )
        availability_report = None
        trade_availability_service = getattr(
            app.state,
            "trade_availability_service",
            None,
        )
        if trade_availability_service is not None:
            try:
                with timed("diag"):
                    availability_report = await asyncio.wait_for(
                        build_opportunity_trade_availability_report(
                            trade_availability_service,
                            latest_opportunity or match.opportunity,
                        ),
                        timeout=getattr(app_settings, "alert_diagnostic_timeout_seconds", 20.0),
                    )
            except Exception as exc:  # Keep the original alert deliverable.
                logger.exception("trade availability alert enrichment failed")
                diagnostic_error = _exception_message(exc)
                if isinstance(exc, TimeoutError):
                    timeouts.append("diag")
                    diagnostic_error = "超时"
                availability_report = OpportunityTradeAvailabilityReport(
                    text=(
                        "【交易与充提状态】\n"
                        f"诊断：未知（{diagnostic_error}）\n"
                        "口径：诊断失败不等于交易或充提关闭；未发送探测订单。"
                    ),
                    opening_restricted=True,
                )
            message = f"{message}\n\n{availability_report.text}"
        validation_failure = _latest_signal_validation_failure(
            match,
            latest_opportunity,
            settings,
            datetime.now(UTC),
        )
        if validation_failure is not None:
            signal_condition_failure = True
            card_summary = "未创建（最新信号校验未通过）"
            message = (
                f"{message}\n\n"
                f"Astro: 最新信号校验未通过：{validation_failure}"
            )
        astro_alert_service: AstroAlertService | None = getattr(
            app.state,
            "astro_alert_service",
            None,
        )
        astro_processing_failed = False
        if astro_alert_service is not None and not signal_condition_failure:
            try:
                card_settings = _astro_card_settings_for_opportunity(
                    astro_alert_service,
                    latest_opportunity,
                )
                plan_failure = _astro_plan_validation_failure(
                    latest_opportunity,
                    card_settings,
                )
                if plan_failure is not None:
                    card_condition_failure = True
                    card_summary = "未创建（卡片参数校验未通过）"
                    message = (
                        f"{message}\n\n"
                        f"Astro: 卡片参数校验未通过：{plan_failure}"
                    )
                else:
                    order_book_timed_out = False
                    try:
                        with timed("orderbook"):
                            order_book_failure = await asyncio.wait_for(
                                _order_book_validation_failure(
                                    app,
                                    latest_opportunity,
                                    settings,
                                    card_settings,
                                ),
                                timeout=getattr(app_settings, "alert_orderbook_timeout_seconds", 15.0),
                            )
                    except TimeoutError:
                        order_book_timed_out = True
                        timeouts.append("orderbook")
                    if order_book_timed_out:
                        card_condition_failure = True
                        card_summary = "未创建（订单簿校验超时）"
                        message = f"{message}\n\nAstro: 订单簿校验超时"
                    elif order_book_failure is not None:
                        card_condition_failure = True
                        card_summary = "未创建（订单簿校验未通过）"
                        message = (
                            f"{message}\n\n"
                            f"Astro: 订单簿校验未通过：{order_book_failure}"
                        )
                    else:
                        # No outer wait_for: cancelling add can leave an S7-B attempt
                        # unknown. Astro already enforces its own HTTP timeout.
                        with timed("astro"):
                            astro_result = await astro_alert_service.handle_alert(latest_opportunity)
                        if astro_result.status == "skipped" and astro_result.action in {
                            "unsupported",
                            "conflict",
                        }:
                            card_condition_failure = True
                        if astro_result.status == "skipped" and astro_result.action == "existing":
                            existing_card_skipped = True
                        if astro_result.status == "created":
                            card_summary = "已创建"
                        elif astro_result.status == "updated":
                            card_summary = "已更新"
                        elif astro_result.action == "existing":
                            card_summary = "已有卡片"
                        else:
                            card_summary = f"未创建（{astro_result.message[:80]}）"
                        message = f"{message}\n\n{astro_result.format_message()}"
            except Exception as exc:  # noqa: BLE001 - keep alert delivery independent.
                logger.exception("astro alert follow-up failed")
                astro_processing_failed = True
                card_summary = "未创建（Astro 处理失败）"
                message = f"{message}\n\nAstro: 处理失败，{_exception_message(exc)}"
        rating_header = build_alert_rating_header(
            match.rule,
            latest_opportunity or match.opportunity,
            include_reason=(
                alert_template.include_funding
                and alert_template.include_volume
                and alert_template.include_risk
            ),
            validation_failed=(
                signal_condition_failure
                or card_condition_failure
                or existing_card_skipped
                or astro_processing_failed
                or (
                    availability_report is not None
                    and availability_report.opening_restricted
                )
            ),
        )
        message = f"{rating_header}\n\n{message}"
        if alert_template.format == "compact":
            message = build_alert_message(
                match.rule,
                latest_opportunity or match.opportunity,
                template=alert_template,
            )
            message += f"\n卡片：{card_summary}"
            if availability_report is not None and availability_report.opening_restricted:
                message += "\n交易状态：需核实开仓可用性"
        if signal_condition_failure or existing_card_skipped:
            status = "muted"
        elif alert_template.suppress_when_card_conditions_fail and card_condition_failure:
            status = "muted"
        elif not getattr(app.state, "feishu_live_send_enabled", True):
            status = "muted"
            message = f"{message}\n\n飞书实时发送未启用"
        else:
            try:
                async with write_lock:
                    await delivery_store.mark_pending(key, datetime.now(UTC))
            except Exception:
                logger.exception("alert delivery pending write failed: %s", key)
                status = "pending_write_failed"
                return False
            try:
                # Feishu also has an HTTP timeout; do not cancel an in-flight send
                # with a new stage deadline or hold the database lock across it.
                with timed("feishu"):
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
        event = AlertEvent(
            rule_id=match.rule.id,
            opportunity_id=match.opportunity.id,
            symbol=match.opportunity.symbol,
            status=status,
            message=message,
            created_at=datetime.now(UTC),
        )
        app.state.alert_engine.record_delivery_result(match, status, now=event.created_at)
        state = app.state.alert_engine.export_state(key)
        state_write_ok = True
        try:
            async with write_lock:
                await delivery_store.record(
                    key, state.last_sent_at, state.retry_after, state.retry_attempts,
                )
        except Exception:
            # The send already happened; still keep its audit event.
            logger.exception("alert delivery state write failed: %s", key)
            status = "state_write_failed"
            state_write_ok = False
        try:
            async with write_lock:
                await event_repo.create(event)
        except Exception:
            logger.exception("alert event write failed: %s", key)
            status = "event_write_failed"
            return False
        return state_write_ok
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except Exception:
        status = "failed"
        logger.exception("alert match processing failed: %s", key)
        return False
    finally:
        alert_timing_logger.info(json.dumps(
            {"alert_timing": 1, "key": key, "symbol": match.opportunity.symbol,
             "status": status, **durations, "total_ms": (monotonic() - started) * 1000,
             "timeouts": timeouts},
            separators=(",", ":"),
        ))


class _AlertFilterTiming:
    """One local aggregation window per alert loop; no per-opportunity log traffic."""

    def __init__(self):
        self.started = monotonic()
        self.samples = []
        self.recomputed = 0
        self.retries = 0
        self.skipped = 0

    def record(self, filter_ms, recomputed, *, retries=0, skipped=False):
        from statistics import median

        self.samples.append(filter_ms)
        self.recomputed += recomputed
        self.retries += retries
        self.skipped += int(skipped)
        now = monotonic()
        if now - self.started < 300:
            return
        alert_timing_logger.info(json.dumps({"alert_filter_timing": {
            "count": len(self.samples),
            "filter_ms": {"p50": median(self.samples), "max": max(self.samples)},
            "recomputed": self.recomputed,
            "retries": self.retries,
            "skipped": self.skipped,
        }}, separators=(",", ":")))
        self.started = now
        self.samples.clear()
        self.recomputed = 0
        self.retries = 0
        self.skipped = 0


async def _run_alert_loop(app: FastAPI, interval_seconds: float, stop_event: asyncio.Event) -> None:
    last_delivery_prune: datetime | None = None
    filter_timing = _AlertFilterTiming()
    write_lock = asyncio.Lock()
    app_settings = getattr(app.state, "settings", Settings())
    while not stop_event.is_set():
        try:
            repo: AlertRuleRepository = app.state.alert_rule_repo
            event_repo: AlertEventRepository = app.state.alert_event_repo
            settings_repo: SettingsRepository | None = getattr(
                app.state,
                "settings_repo",
                None,
            )
            rules = await repo.list()
            delivery_store: AlertDeliveryStore = app.state.alert_delivery_store
            prune_now = datetime.now(UTC)
            if last_delivery_prune is None or prune_now - last_delivery_prune >= timedelta(days=1):
                retention = max(
                    timedelta(days=7),
                    timedelta(seconds=max((rule.cooldown_seconds for rule in rules), default=0))
                    + timedelta(days=1),
                )
                try:
                    await delivery_store.prune(prune_now - retention)
                except Exception:
                    logger.exception("alert delivery state prune failed")
                last_delivery_prune = prune_now
            settings = await settings_repo.get_risk_settings() if settings_repo is not None else RiskSettings()
            alert_template = (
                await settings_repo.get_alert_message_template()
                if settings_repo is not None
                else AlertMessageTemplateSettings()
            )
            await _refresh_astro_runtime_settings(app, settings_repo)
            now = datetime.now(UTC)
            filter_started = monotonic()
            identity_validator = getattr(app.state, "market_validation", None)
            recomputed = 0
            for retries in range(3):  # Initial attempt plus at most two immediate retries.
                opportunities = filter_opportunities(
                    app.state.snapshot_store.get_opportunities(), settings,
                )
                try:
                    if identity_validator is not None:
                        opportunities = await identity_validator.filter_opportunities_async(
                            opportunities, settings,
                        )
                except AlertFilterChanged:
                    recomputed += identity_validator.last_filter_recomputed
                    if retries < 2:
                        continue
                    filter_timing.record(
                        (monotonic() - filter_started) * 1000, recomputed,
                        retries=retries, skipped=True,
                    )
                    raise
                if identity_validator is not None:
                    recomputed += identity_validator.last_filter_recomputed
                break
            filter_timing.record(
                (monotonic() - filter_started) * 1000, recomputed,
                retries=retries, skipped=False,
            )
            opportunities = filter_opportunities_by_alert_rules(
                opportunities,
                rules,
                settings,
                now=now,
            )
            matches = app.state.alert_engine.evaluate(opportunities, rules, now=now, risk_settings=settings)
            concurrency = getattr(
                app_settings, "alert_match_concurrency", Settings.alert_match_concurrency,
            )
            semaphore = asyncio.Semaphore(concurrency)
            matches_by_symbol: dict[str, list[AlertMatch]] = {}
            for match in matches:
                matches_by_symbol.setdefault(match.opportunity.symbol, []).append(match)
            # At concurrency=1 preserve even interleaved symbols in the original order.
            groups = [matches] if concurrency == 1 else list(matches_by_symbol.values())

            async def process_bounded(group: list[AlertMatch]) -> bool:
                async with semaphore:
                    group_ok = True
                    for match in group:
                        match_ok = await _process_alert_match(
                            app, match, settings, alert_template,
                            delivery_store, event_repo, write_lock,
                            app_settings,
                        )
                        group_ok = group_ok and match_ok
                    return group_ok

            # First-seen symbol order preserves ordinary match order at admission;
            # keeping the permit for the whole group serializes same-symbol matches.
            tasks = [asyncio.create_task(process_bounded(group)) for group in groups]
            try:
                results = await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                # gather does not cancel siblings when a child itself is cancelled.
                # Drain them before leaving the round so no match is orphaned.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
        except AlertFilterChanged:
            beat("alert-loop", ok=False)
            logger.warning("alert filtering skipped after 2 retries: mapping or snapshot kept changing")
        except Exception:
            beat("alert-loop", ok=False)
            logger.exception("alert loop failed")
        else:
            beat("alert-loop", ok=all(results))
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
    start_background_workers: bool = True,
) -> FastAPI:
    if start_collector and not start_background_workers:
        raise ValueError("start_collector requires start_background_workers")
    app_settings = settings or get_settings()
    disabled = app_settings.disabled_features
    store = snapshot_store or SnapshotStore()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop_event = asyncio.Event()
        app.state.hyperliquid_bbo_stream = None
        app.state.gate_bbo_runtime = GateBboRuntime(app_settings, store)
        if not app.state.settings.dashboard_password:
            logger.warning("DASHBOARD_PASSWORD is empty; protected API routes deny access unless explicit non-production auth bypass is enabled")
        db_path = _sqlite_path(app_settings)
        _ensure_database_parent(db_path)
        db = await connect_database(db_path, recover_stale_snapshots=True)
        await initialize_schema(db)
        app.state.db = db
        app.state.shadow_runtime = ShadowRuntime(app_settings, store)
        await app.state.shadow_runtime.initialize(db)
        app.state.auth_store = await AuthStore.open(db_path)
        attempts_db = db if db_path == ":memory:" else await connect_database(db_path)
        if attempts_db is not db:
            # A post-create mark that times out leaves the route unknown; wait longer.
            await attempts_db.execute("PRAGMA busy_timeout=15000")
        app.state.astro_alert_service.attempts = AstroCardAttemptRepository(attempts_db)
        app.state.alert_rule_repo = AlertRuleRepository(db)
        app.state.alert_event_repo = AlertEventRepository(db)
        app.state.alert_delivery_store = AlertDeliveryStore(db)
        await _restore_alert_delivery_state(app)
        app.state.phone_price_alert_rule_repo = PhonePriceAlertRuleRepository(db)
        app.state.phone_price_alert_event_repo = PhonePriceAlertEventRepository(db)
        app.state.hyperliquid_trade_status_watch_repo = HyperliquidTradeStatusWatchRepository(db)
        app.state.trade_availability_watch_repo = TradeAvailabilityWatchRepository(db)
        if app.state.trade_availability_service is None:
            app.state.trade_availability_service = TradeAvailabilityService(
                store,
                default_exchange_adapters(),
                app.state.hyperliquid_trade_status_service,
            )
        app.state.settings_repo = SettingsRepository(db)
        identity_db = db if db_path == ":memory:" else await connect_database(db_path)
        app.state.market_identity_repo = MarketIdentityRepository(identity_db)
        app.state.market_validation = MarketValidationService(app.state.market_identity_repo, store)
        await app.state.market_validation.initialize(seed_reviewed=start_collector)
        app.state.market_identity_catalog = OfficialMarketIdentityCatalog(app.state.market_validation)
        app.state.astro_alert_service.market_validation = app.state.market_validation
        app.state.account_connection_service = AccountConnectionService(
            AccountConnectionRepository(db),
            master_key=app_settings.account_credentials_master_key,
            dashboard_password=app_settings.dashboard_password,
        )
        app.state.account_position_service.set_dynamic_provider_loader(
            app.state.account_connection_service.providers
        )
        app.state.astro_alert_service.risk_settings_loader = (
            app.state.settings_repo.get_risk_settings
        )
        preadd_notifier = getattr(app.state, "feishu_notifier", None)
        preadd_alert_sender = (
            preadd_notifier.send_text
            if getattr(getattr(preadd_notifier, "config", None), "webhook_url", "")
            else None
        )
        app.state.astro_preadd_service = AstroPreaddService(
            store,
            app.state.settings_repo,
            app.state.astro_alert_service,
            alert_sender=preadd_alert_sender,
        )
        app.state.history_repo = OpportunityHistoryRepository(db)
        app.state.index_component_repo = IndexComponentRepository(db)
        app.state.announcement_repo = AnnouncementRepository(db)
        text_alert_sender = (
            (lambda message: _send_index_component_alert(app, message))
            if app_settings.feishu_live_send_enabled
            else None
        )
        app.state.pair_spread_funding_recorder = PairSpreadFundingRecorder(PairSpreadFundingRepository(db))
        preset_db = db if db_path == ":memory:" else await connect_database(db_path)
        app.state.pair_spread_preset_repo = PairSpreadPresetRepository(preset_db)
        app.state.index_component_auto_watch = IndexComponentAutoWatchService(
            app.state.index_component_repo,
            app.state.settings_repo,
            app.state.pair_spread_preset_repo,
            app.state.astro_client,
        )
        app.state.hyperliquid_trade_status_monitor = HyperliquidTradeStatusMonitor(
            app.state.hyperliquid_trade_status_watch_repo,
            app.state.hyperliquid_trade_status_service,
            alert_sender=text_alert_sender,
        )
        app.state.trade_availability_monitor = TradeAvailabilityMonitor(
            app.state.trade_availability_watch_repo,
            app.state.trade_availability_service,
            alert_sender=text_alert_sender,
        )
        # Fresh supervisor per lifespan so the same app can start again after stop().
        supervisor = TaskSupervisor()
        app.state.task_supervisor = supervisor
        if start_background_workers and app_settings.memory_probe_enabled:
            supervisor.enable_memory_probe()
        supervisor.start("event-loop-lag", supervisor.loop_lag.run, restart=True, critical=False)
        gc_pause_monitor = GcPauseMonitor()
        app.state.gc_pause_monitor = gc_pause_monitor
        supervisor.start("gc-pause-log", gc_pause_monitor.run, restart=False, critical=False)
        loop_stall_watchdog = LoopStallWatchdog(gc_monitor=gc_pause_monitor)
        app.state.loop_stall_watchdog = loop_stall_watchdog
        if loop_stall_watchdog.enabled:
            supervisor.start(
                "loop-stall-watchdog", loop_stall_watchdog.run, restart=False, critical=False,
            )
        if start_background_workers:
            _start_background_task(
                supervisor,
                lambda: app.state.pair_spread_funding_recorder.run(stop_event),
                name="pair-spread-funding-recorder",
            )
            _start_background_task(
                supervisor,
                lambda: app.state.hyperliquid_trade_status_monitor.run(stop_event),
                name="hyperliquid-trade-status-monitor",
            )
            _start_background_task(
                supervisor,
                lambda: app.state.trade_availability_monitor.run(stop_event),
                name="trade-availability-monitor",
            )
        collector: MarketCollector | None = None
        announcement_provider = None
        if start_collector:
            _start_background_task(
                supervisor,
                lambda: app.state.market_identity_catalog.run(
                    stop_event,
                    startup_delay_seconds=app_settings.market_identity_catalog_startup_delay_seconds,
                ),
                name="market-identity-catalog",
            )
            exchange_adapters = default_exchange_adapters()
            for adapter in exchange_adapters:
                if isinstance(adapter, GateAdapter):
                    adapter.bbo_runtime = app.state.gate_bbo_runtime
                    adapter.batch_quotes_executable = app_settings.gate_batch_quotes_executable
            if start_background_workers:
                await app.state.gate_bbo_runtime.start()
            if start_background_workers and app_settings.hl_bbo_enabled:
                hyperliquid_adapter = next(
                    (adapter for adapter in exchange_adapters if isinstance(adapter, HyperliquidAdapter)),
                    None,
                )
                if hyperliquid_adapter is not None:
                    bbo_stream = HyperliquidBboStream(
                        max_coins=app_settings.hl_bbo_max_coins,
                        max_age_seconds=app_settings.hl_bbo_max_age_seconds,
                    )
                    app.state.hyperliquid_bbo_stream = bbo_stream
                    hyperliquid_adapter.bbo_stream = bbo_stream
                    _start_background_task(
                        supervisor, lambda: bbo_stream.run(stop_event), name="hyperliquid-bbo",
                    )
            app.state.market_validation.set_adapters(exchange_adapters)
            history_recorder = OpportunityHistoryRecorder(
                app.state.history_repo,
                app_settings.history_settings,
            )
            index_component_monitor = IndexComponentMonitor(
                app.state.index_component_repo,
                alert_sender=text_alert_sender,
                auto_watch=app.state.index_component_auto_watch,
                notification_settings_loader=app.state.settings_repo.get_index_component_notification_settings,
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
            app.state.snapshot_consumers = {}
            snapshot_slots = None
            if app_settings.collector_post_publish_mode == "background":
                history_slot = LatestSnapshotSlot()
                index_slot = LatestSnapshotSlot()
                snapshot_slots = [history_slot, index_slot]
            shadow_slot = LatestSnapshotSlot()
            use_shadow_slot = app_settings.shadow_enabled and snapshot_slots is not None
            if use_shadow_slot:
                snapshot_slots.append(shadow_slot)
            collector = MarketCollector(
                exchange_adapters,
                store,
                risk_settings_loader=app.state.settings_repo.get_risk_settings,
                history_recorder=history_recorder,
                index_component_provider=app.state.index_component_provider,
                index_component_monitor=index_component_monitor,
                poll_interval_seconds=app_settings.poll_interval_seconds,
                max_due_adapters_per_cycle=app_settings.collector_max_due_adapters_per_cycle,
                market_validation=app.state.market_validation,
                snapshot_slots=snapshot_slots,
                risk_settings_timeout_seconds=app_settings.collector_risk_settings_timeout_seconds,
            )
            app.state.market_collector = collector
            shadow_consumer = None
            if app_settings.shadow_enabled:
                shadow_consumer = SnapshotConsumer("shadow-ledger", shadow_slot, app.state.shadow_runtime.submit)
                _start_background_task(
                    supervisor,
                    (lambda: shadow_consumer.run(stop_event)) if use_shadow_slot
                    else (lambda: app.state.shadow_runtime.run_inline_observer(stop_event)),
                    name="shadow-ledger",
                )
                _start_background_task(supervisor, lambda: app.state.shadow_runtime.catalog.run(stop_event),
                                       name="shadow-crossex-catalog")
            if snapshot_slots is not None:
                app.state.snapshot_consumers = {
                    "history-recorder": SnapshotConsumer("history-recorder", history_slot, collector.record_history),
                    "index-components": SnapshotConsumer(
                        "index-components", index_slot, lambda s: collector.process_index_components(s.markets),
                    ),
                }
                for name, consumer in app.state.snapshot_consumers.items():
                    _start_background_task(
                        supervisor, lambda consumer=consumer: consumer.run(stop_event), name=name,
                    )
            app.state.orderbook_validator = OrderBookDepthValidator(exchange_adapters)
            app.state.shadow_consumer = shadow_consumer
            _start_background_task(
                supervisor,
                lambda: run_collector_loop(collector, app_settings.poll_interval_seconds, stop_event),
                name="market-collector",
            )
            _start_background_task(
                supervisor,
                lambda: app.state.astro_preadd_service.run_loop(stop_event),
                name="astro-preadd-loop",
            )
            _start_background_task(
                supervisor,
                lambda: _run_alert_loop(app, app_settings.poll_interval_seconds, stop_event),
                name="alert-loop",
            )
            if app_settings.feishu_live_send_enabled and app_settings.feishu_phone_enabled:
                _start_background_task(
                    supervisor,
                    lambda: _run_phone_price_alert_loop(
                        app,
                        app_settings.poll_interval_seconds,
                        stop_event,
                    ),
                    name="phone-price-alert-loop",
                )
            app.state.multi_exchange_premium_monitor = MultiExchangePremiumMonitor(app)
            _start_background_task(
                supervisor, lambda: app.state.multi_exchange_premium_monitor.run_loop(stop_event),
                name="multi-exchange-premium-loop",
            )
            announcement_monitor = AnnouncementMonitor(
                app.state.announcement_repo,
                alert_sender=text_alert_sender,
            )
            announcement_provider = default_announcement_provider(app.state.announcement_repo)
            app.state.announcement_monitor = announcement_monitor
            app.state.announcement_provider = announcement_provider
            _start_background_task(
                supervisor,
                lambda: run_announcement_loop(
                    announcement_provider,
                    announcement_monitor,
                    app.state.settings_repo.get_announcement_settings,
                    stop_event,
                ),
                name="announcement-loop",
            )
            _start_background_task(
                supervisor,
                lambda: run_announcement_enrichment_loop(
                    announcement_provider,
                    announcement_monitor,
                    app.state.settings_repo.get_announcement_settings,
                    stop_event,
                ),
                name="announcement-enrichment-loop",
            )
        if start_background_workers and app_settings.memory_probe_enabled:
            from app.runtime.memory_probe import MemoryProbe

            census_targets = None
            if app_settings.memory_census_enabled:
                from app.runtime.cache_census import build_census_targets

                census_targets = build_census_targets(app.state)
            memory_probe = MemoryProbe(
                store, supervisor, interval_seconds=app_settings.memory_probe_interval_seconds,
                census_targets=census_targets,
                census_interval_seconds=app_settings.memory_census_interval_seconds,
                census_max_objects=app_settings.memory_census_max_objects,
                census_budget_seconds=app_settings.memory_census_budget_seconds,
                census_target_seconds=app_settings.memory_census_target_seconds,
            )
            _start_background_task(
                supervisor, lambda: memory_probe.run(stop_event), name="memory-sampler",
            )
        if start_background_workers and app_settings.memory_tracemalloc_seconds > 0:
            from app.runtime.memory_probe import TracemallocWindow

            trace_window = TracemallocWindow(app_settings.memory_tracemalloc_seconds)
            supervisor.start("memory-tracemalloc", trace_window.run, restart=False, critical=False)
        restore_gc = start_gc_tuning(supervisor)
        try:
            yield
        finally:
            restore_gc()
            stop_event.set()
            loop_stall_watchdog.stop()
            await supervisor.stop()
            try:
                # Both process deadlines begin together; Gate consumes no extra
                # sequential time from shadow's existing six-second budget.
                close_results = await asyncio.gather(
                    app.state.shadow_runtime.aclose(), app.state.gate_bbo_runtime.aclose(),
                    return_exceptions=True,
                )
                for name, result in zip(("shadow", "gate_bbo"), close_results):
                    if isinstance(result, BaseException):
                        if name == "shadow":
                            app.state.shadow_runtime.error("shutdown_lifespan")
                        logger.error("%s runtime shutdown failed: %s", name, type(result).__name__)
            except Exception:
                app.state.shadow_runtime.error("shutdown_lifespan")
            finally:
                await app.state.market_validation.aclose()
                if collector is not None:
                    await collector.close()
                if announcement_provider is not None:
                    await announcement_provider.aclose()
                await _close_state_resources(
                    app,
                    "astro_client",
                    "service_controller",
                    # The manager owns client shutdown when present.
                    "gate_twap_manager" if app.state.gate_twap_manager is not None else "gate_twap_client",
                    "pair_spread_funding_recorder",
                    "trade_availability_service",
                    "hyperliquid_trade_status_service",
                    "account_connection_service",
                    "instrument_market_cap_service",
                    "instrument_statistics_service",
                    "feishu_notifier",
                )
                if identity_db is not db:
                    await identity_db.close()
                if preset_db is not db:
                    await preset_db.close()
                if attempts_db is not db:
                    await attempts_db.close()
                await app.state.auth_store.close()
                await db.close()

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.state.task_supervisor = TaskSupervisor()
    app.state.start_collector = start_collector
    app.state.settings = app_settings
    app.state.snapshot_store = store
    app.state.shadow_runtime = ShadowRuntime(app_settings, store)
    app.state.instrument_market_cap_service = InstrumentMarketCapService()
    app.state.instrument_statistics_service = InstrumentStatisticsService()
    app.state.market_collector = None
    app.state.orderbook_validator = None
    app.state.pair_spread_query_service_factory = None
    app.state.premium_index_query_service_factory = None
    app.state.pair_spread_funding_recorder = None
    app.state.pair_spread_preset_repo = None
    app.state.index_component_auto_watch = None
    app.state.hyperliquid_trade_status_watch_repo = None
    app.state.hyperliquid_trade_status_monitor = None
    app.state.trade_availability_watch_repo = None
    app.state.trade_availability_monitor = None
    app.state.trade_availability_service = None
    app.state.account_position_service = None
    app.state.account_connection_service = None
    app.state.alert_engine = AlertEngine()
    app.state.phone_price_alert_engine = PhonePriceAlertEngine()
    app.state.hyperliquid_trade_status_service = HyperliquidTradeStatusService()
    app.state.astro_client = AstroSdkClient(
        AstroSdkConfig(
            base_url=app_settings.astro_sdk_base_url,
            admin_prefix=app_settings.astro_admin_prefix,
            api_key=app_settings.astro_api_key,
            verify_tls=app_settings.astro_verify_tls,
            ca_bundle=app_settings.astro_ca_bundle,
            timeout_seconds=app_settings.astro_request_timeout_seconds,
        )
    )
    app.state.astro_hl_gc_catalog = GateCrossExHyperliquidCatalog()
    app.state.astro_alert_service = AstroAlertService(
        app.state.astro_client, app_settings, hl_gc_catalog=app.state.astro_hl_gc_catalog,
    )
    app.state.astro_preadd_service = None
    app.state.service_controller = DockerServiceController(
        ServiceControlConfig(
            enabled=app_settings.service_control_enabled,
            environment=app_settings.environment,
            compose_project_name=app_settings.compose_project_name,
            docker_socket_path=app_settings.service_control_docker_socket_path,
            restart_delay_seconds=app_settings.service_control_restart_delay_seconds,
        )
    )
    gate_twap_client = GateTwapClient()
    app.state.gate_twap_client = gate_twap_client
    app.state.gate_twap_manager = (
        GateTwapJobManager(gate_twap_client) if "gate_twap" not in disabled else None
    )
    app.state.account_position_service = AccountPositionService(
        [GateAccountPositionProvider(gate_twap_client)]
    )
    app.state.feishu_live_send_enabled = app_settings.feishu_live_send_enabled
    feishu_text_send_enabled = app_settings.feishu_live_send_enabled
    app.state.feishu_notifier = FeishuNotifier(
        FeishuConfig(
            webhook_url=app_settings.feishu_webhook_url if feishu_text_send_enabled else "",
            secret=app_settings.feishu_secret if feishu_text_send_enabled else "",
            app_id=app_settings.feishu_app_id if app_settings.feishu_live_send_enabled else "",
            app_secret=app_settings.feishu_app_secret if app_settings.feishu_live_send_enabled else "",
            alert_chat_id=app_settings.feishu_alert_chat_id if app_settings.feishu_live_send_enabled else "",
            phone_user_ids=[
                item.strip()
                for item in app_settings.feishu_phone_user_ids.split(",")
                if item.strip()
            ],
            phone_user_id_type=app_settings.feishu_phone_user_id_type,
            phone_enabled=app_settings.feishu_live_send_enabled and app_settings.feishu_phone_enabled,
        )
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Every API router uses the same guard; public exceptions live in security.py.
    for router in (
        routes_shadow.router,
        routes_auth.router,
        routes_features.router,
        routes_health.router,
        routes_account_connections.router,
        routes_account_positions.router,
        routes_astro.router,
        routes_arb_mvp.router,
        routes_live_exec.router,
        routes_opportunities.router,
        routes_history.router,
        routes_hyperliquid_trade_status.router,
        routes_trade_availability.router,
        routes_instruments.router,
        routes_instruments.market_cap_router,
        routes_pair_spread.router,
        routes_premium_index.router,
        routes_index_components.router,
        routes_announcements.router,
        routes_alerts.router,
        routes_phone_alerts.router,
        routes_funding_arbitrage.router,
        routes_gate_twap.router,
        routes_settings.router,
        routes_market_identities.router,
        routes_admin.router,
        stream.router,
    ):
        app.include_router(router, prefix="/api", dependencies=[Depends(require_dashboard_password)])
    return app


api_app = create_app(start_background_workers=False)
app = create_app(start_collector=True)
