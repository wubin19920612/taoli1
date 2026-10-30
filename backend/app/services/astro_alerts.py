import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from app.core.config import Settings
from app.models.astro import AstroAlertActionResult, AstroCardCreateRequest, AstroCardVariant
from app.models.opportunity import Opportunity
from app.models.settings import AstroCardSettings, LivePilotSettings, RiskSettings
from app.services.astro_card_attempts import AstroCardAttemptRepository, route_key
from app.services.astro_client import AstroClientError
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig
from app.services.data_filters import symbol_is_excluded
from app.services.gate_crossex_hyperliquid import GateCrossExHyperliquidCatalog
from app.services.market_labels import astro_pair_route_variants
from app.services.market_validation_review import ManualMappingChanged, ManualReview
from app.services.spread_engine import build_directional_opportunity

logger = logging.getLogger(__name__)

AUTOMATIC_ASTRO_BLOCKED_EXCHANGES = frozenset({"rh-lighter"})
AUTOMATIC_ASTRO_BLOCK_MESSAGE = (
    "RH-Lighter 已支持人工建卡；自动建卡、自动预建及实盘实验仍未开放"
)


def automatic_astro_blocked_exchanges(opportunity: Opportunity) -> set[str]:
    return {
        opportunity.buy_exchange.strip().lower(),
        opportunity.sell_exchange.strip().lower(),
    } & AUTOMATIC_ASTRO_BLOCKED_EXCHANGES


class AstroPairClient(Protocol):
    async def list_pairs(self) -> list[dict]:
        ...

    async def add_pair(self, pair: dict) -> dict:
        ...

    async def update_pair(self, pair: dict) -> dict:
        ...


def _same_route(existing: dict, planned: dict) -> bool:
    if not _same_exchange_route(existing, planned):
        return False
    for exchange_key, dex_key, effective_key in (
        ("buyEx", "aHlDex", "aEffectiveHlDex"),
        ("sellEx", "bHlDex", "bEffectiveHlDex"),
    ):
        if existing.get(exchange_key) in {"hl", "gc-hl"}:
            existing_dex = existing.get(effective_key) or existing.get(dex_key) or "main"
            planned_dex = planned.get(dex_key) or "main"
            if str(existing_dex).strip().lower() != str(planned_dex).strip().lower():
                return False
    return True


def _same_exchange_route(existing: dict, planned: dict) -> bool:
    return (
        existing.get("name") == planned.get("name")
        and existing.get("type") == planned.get("type")
        and existing.get("buyEx") == planned.get("buyEx")
        and existing.get("sellEx") == planned.get("sellEx")
    )


def _with_card_enabled(pair: dict, enabled: bool) -> dict:
    next_pair = dict(pair)
    next_pair["status"] = enabled
    next_pair["disableOpen"] = not enabled
    return next_pair


def _pair_variants(
    pair: dict, card_variant: AstroCardVariant = "both", *, hl_gc_supported: bool = True,
) -> list[dict]:
    variants: list[dict] = []
    for buy_exchange, sell_exchange in astro_pair_route_variants(
        pair, card_variant, hl_gc_supported=hl_gc_supported,
    ):
        variant = dict(pair)
        variant["buyEx"] = buy_exchange
        variant["sellEx"] = sell_exchange
        variants.append(variant)
    return variants


def _route(pair: dict) -> str:
    def leg(exchange_key: str, dex_key: str, effective_key: str) -> str:
        exchange = str(pair.get(exchange_key, ""))
        if exchange not in {"hl", "gc-hl"}:
            return exchange
        return f"{exchange}({pair.get(effective_key) or pair.get(dex_key) or 'main'})"

    buy_leg = leg("buyEx", "aHlDex", "aEffectiveHlDex")
    sell_leg = leg("sellEx", "bHlDex", "bEffectiveHlDex")
    return f"{buy_leg}->{sell_leg}"


def _routes(pairs: list[dict]) -> str:
    return "、".join(_route(pair) for pair in pairs)


def _card_state_message(enabled: bool) -> str:
    return "开启卡片" if enabled else "暂停卡片"


def _disable_open_message(enabled: bool) -> str:
    return "禁开=false" if enabled else "禁开=true"


def _settings_with_create_overrides(
    settings: AstroCardSettings,
    card_request: AstroCardCreateRequest | None,
) -> AstroCardSettings:
    if card_request is None:
        return settings
    updates = {
        key: value
        for key, value in {
            "max_trade_usdt": card_request.max_trade_usdt,
            "leverage": card_request.leverage,
            "min_notional": card_request.min_notional,
            "max_notional": card_request.max_notional,
            "open_enabled": card_request.open_enabled,
            "card_variant": card_request.card_variant,
        }.items()
        if value is not None
    }
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def live_pilot_card_settings(
    settings: AstroCardSettings,
    live_pilot_settings: LivePilotSettings,
) -> AstroCardSettings:
    if not live_pilot_settings.enabled:
        return settings
    notional = live_pilot_settings.notional_per_symbol_usdt
    return settings.model_copy(
        update={
            "max_trade_usdt": notional,
            "max_notional": notional,
        }
    )


class AstroAlertService:
    def __init__(
        self,
        client: AstroPairClient,
        settings: Settings,
        planner: AstroPairPlanner | None = None,
        card_settings: AstroCardSettings | None = None,
        live_pilot_settings: LivePilotSettings | None = None,
        add_restart_delay_seconds: float = 3.0,
        risk_settings_loader: Callable[[], Awaitable[RiskSettings]] | None = None,
        hl_gc_catalog: GateCrossExHyperliquidCatalog | None = None,
        attempts: AstroCardAttemptRepository | None = None,
    ):
        self.client = client
        self.settings = settings
        self.planner = planner
        self.card_settings = card_settings or settings.astro_card_settings
        self.live_pilot_settings = live_pilot_settings or LivePilotSettings()
        self.alert_auto_create_enabled = settings.astro_alert_auto_create
        self.allow_same_name_variants = (
            settings.astro_automation_settings.allow_same_name_variants
        )
        self.add_restart_delay_seconds = add_restart_delay_seconds
        self.risk_settings_loader = risk_settings_loader
        self.hl_gc_catalog = hl_gc_catalog or GateCrossExHyperliquidCatalog()
        self.market_validation = None
        self.attempts = attempts
        # Names come from the finite market catalog; keep locks for this service's
        # lifetime so waiting callers always share the same lock.
        self._pair_locks: dict[str, asyncio.Lock] = {}

    async def handle_alert(self, opportunity: Opportunity) -> AstroAlertActionResult:
        return await self._handle(
            opportunity,
            enabled=self.alert_auto_create_enabled,
            disabled_message="自动创建卡片未开启",
        )

    async def handle_live_pilot(self, opportunity: Opportunity) -> AstroAlertActionResult:
        return await self._handle(
            opportunity,
            enabled=self.live_pilot_settings.enabled,
            disabled_message="实盘实验未开启",
            live_pilot=True,
        )

    async def handle_manual_create(
        self,
        opportunity: Opportunity,
        card_request: AstroCardCreateRequest | None = None,
        card_settings: AstroCardSettings | None = None,
        *,
        manual_review: ManualReview | None = None,
        depth_check: Callable[[], Awaitable[None]] | None = None,
    ) -> AstroAlertActionResult:
        context = manual_review or ManualReview()
        try:
            return await self._handle(
                opportunity,
                enabled=self.settings.astro_manual_card_create,
                disabled_message="Manual card creation is disabled.",
                card_request=card_request,
                card_settings_override=card_settings,
                manual_override=True,
                manual_review=context,
                depth_check=depth_check,
                add_restart_delay_seconds=0,
            )
        finally:
            if manual_review is None:
                context.log("astro_manual_create_timing", opportunity.symbol)

    async def handle_preadd(
        self,
        opportunity: Opportunity,
        card_settings: AstroCardSettings,
        *,
        allow_reverse_entry: bool = False,
    ) -> AstroAlertActionResult:
        return await self._handle(
            opportunity,
            enabled=True,
            disabled_message="预建卡片未开启",
            card_settings_override=card_settings.model_copy(update={"open_enabled": False}),
            allow_route_variants=True,
            source="preadd",
            allow_reverse_entry=allow_reverse_entry,
        )

    async def _handle(
        self,
        opportunity: Opportunity,
        enabled: bool,
        disabled_message: str,
        card_request: AstroCardCreateRequest | None = None,
        card_settings_override: AstroCardSettings | None = None,
        live_pilot: bool = False,
        add_restart_delay_seconds: float | None = None,
        manual_override: bool = False,
        allow_route_variants: bool = False,
        allow_reverse_entry: bool = False,
        manual_review: ManualReview | None = None,
        depth_check: Callable[[], Awaitable[None]] | None = None,
        source: str = "alert",
    ) -> AstroAlertActionResult:
        source = "manual" if manual_override else "live_pilot" if live_pilot else source
        if not enabled:
            return AstroAlertActionResult(
                enabled=False,
                status="disabled",
                action="none",
                message=disabled_message,
            )
        if not manual_override and automatic_astro_blocked_exchanges(opportunity):
            return AstroAlertActionResult(
                enabled=True,
                status="skipped",
                action="unsupported",
                message=AUTOMATIC_ASTRO_BLOCK_MESSAGE,
            )
        manual_warnings: list[str] = []
        if opportunity.comparison_kind == "custom_cross_asset" and not manual_override:
            return AstroAlertActionResult(enabled=True, status="skipped", action="market_validation", message="跨资产研究配对不允许进入自动建卡流程")
        if self.market_validation is not None:
            try:
                risk = await self.risk_settings_loader() if self.risk_settings_loader else RiskSettings()
                if manual_override:
                    result, buy, sell = await manual_review.qualify(
                        self.market_validation, opportunity, risk,
                    )
                    reviewed = build_directional_opportunity(
                        buy, sell, mode=opportunity.type,
                        buy_fee_pct=opportunity.buy_fee_pct,
                        sell_fee_pct=opportunity.sell_fee_pct,
                        safety_slippage_pct=opportunity.safety_slippage_pct,
                        validate_identity=False,
                    )
                    opportunity = reviewed.model_copy(update={
                        "id": opportunity.id,
                        "comparison_kind": opportunity.comparison_kind,
                        "fees_are_estimated": opportunity.fees_are_estimated,
                        "risk_labels": opportunity.risk_labels,
                    })
                else:
                    result, _, _ = await self.market_validation.qualify_opportunity(opportunity, risk)
                if self.market_validation.should_enforce(result) and not result.capabilities.manual_card:
                    return AstroAlertActionResult(enabled=True, status="skipped", action="market_validation",
                                                  message="；".join(issue.message for issue in result.blockers))
                opportunity = opportunity.model_copy(update={"validation": result, "identity_enforced": self.market_validation.should_enforce(result)})
            except ManualMappingChanged as exc:
                return AstroAlertActionResult(enabled=True, status="skipped", action="market_validation", message=str(exc))
            except Exception as exc:  # noqa: BLE001 - failed identity checks must prevent external writes.
                return AstroAlertActionResult(enabled=True, status="failed", action="market_validation", message=f"市场身份校验失败：{exc}")

        def with_manual_warnings(result: AstroAlertActionResult) -> AstroAlertActionResult:
            if not manual_warnings:
                return result
            return result.model_copy(
                update={
                    "warnings": list(dict.fromkeys([*result.warnings, *manual_warnings]))
                }
            )

        if self.risk_settings_loader is not None:
            try:
                risk_settings = await self.risk_settings_loader()
            except Exception as exc:  # noqa: BLE001 - automatic writes fail closed.
                if manual_override:
                    manual_warnings.append(
                        "读取全局黑名单失败，无法完成风险校验；"
                        f"本次为人工建卡，仅作风险提示，未拦截创建：{exc}"
                    )
                    risk_settings = None
                else:
                    return AstroAlertActionResult(
                        enabled=True,
                        status="failed",
                        action="risk_settings",
                        message=f"读取全局黑名单失败，已阻止创建 Astro 卡片：{exc}",
                    )
            if risk_settings is not None and symbol_is_excluded(opportunity.symbol, risk_settings):
                if manual_override:
                    manual_warnings.append(
                        f"{opportunity.symbol} 已在全局黑名单；"
                        "本次为人工建卡，仅作风险提示，未拦截创建"
                    )
                else:
                    return AstroAlertActionResult(
                        enabled=True,
                        status="skipped",
                        action="excluded_symbol",
                        message=f"{opportunity.symbol} 已在全局黑名单，未创建 Astro 卡片",
                    )
        if self.settings.astro_dry_run_only:
            return with_manual_warnings(
                AstroAlertActionResult(
                    enabled=True,
                    status="skipped",
                    action="dry_run",
                    message="dry-run 模式开启，未写入 Astro",
                )
            )

        base_card_settings = (
            card_settings_override
            if card_settings_override is not None
            else self.card_settings
        )
        effective_card_settings = _settings_with_create_overrides(base_card_settings, card_request)
        if live_pilot:
            effective_card_settings = live_pilot_card_settings(
                effective_card_settings,
                self.live_pilot_settings,
            )
        planner = self.planner or AstroPairPlanner(
            AstroPlannerConfig.from_card_settings(effective_card_settings)
        )
        plan = planner.plan(
            opportunity,
            allow_manual_override=manual_override,
            allow_negative_open=allow_reverse_entry,
        )
        if manual_override:
            manual_warnings.extend(
                warning
                for warning in plan.warnings
                if warning.startswith("人工建卡风险提示：")
            )
        if not plan.can_submit or plan.pair is None:
            reason = "；".join(plan.blockers) if plan.blockers else "当前机会无法提交 Astro"
            return with_manual_warnings(
                AstroAlertActionResult(
                    enabled=True,
                    status="skipped",
                    action="unsupported",
                    message=reason,
                )
            )

        if live_pilot:
            pair_enabled = self.live_pilot_settings.create_cards_enabled
        else:
            pair_enabled = effective_card_settings.open_enabled
        pair = _with_card_enabled(plan.pair, pair_enabled)
        hl_gc_supported = await self.hl_gc_catalog.supports_opportunity(opportunity)
        all_pair_variants = _pair_variants(pair, hl_gc_supported=hl_gc_supported)
        if not all_pair_variants:
            return with_manual_warnings(
                AstroAlertActionResult(
                    enabled=True,
                    status="skipped",
                    action="unsupported",
                    message="当前交易所配对没有可用的 Astro 建卡路线，未写入 Astro",
                )
            )
        pair_variants = _pair_variants(
            pair, effective_card_settings.card_variant, hl_gc_supported=hl_gc_supported,
        )
        if not pair_variants:
            return with_manual_warnings(
                AstroAlertActionResult(
                    enabled=True,
                    status="skipped",
                    action="unsupported",
                    message="所选 GC/非 GC 卡片类型在当前路线上不可用，未写入 Astro",
                )
            )
        pair_name = str(pair.get("name", ""))
        pair_type = str(pair.get("type", ""))

        async with self._pair_locks.setdefault(pair_name, asyncio.Lock()):
            if self.attempts is None:
                return AstroAlertActionResult(
                    enabled=True, status="failed", action="add",
                    message="尝试账本未就绪，未创建 Astro 卡片",
                    pair_name=pair_name, pair_type=pair_type,
                )
            try:
                if manual_override:
                    async def check_depth():
                        if depth_check is not None:
                            await depth_check()

                    existing_pairs, depth_result = await asyncio.gather(
                        manual_review.measure("list_ms", self.client.list_pairs()),
                        manual_review.measure("depth_ms", check_depth()),
                        return_exceptions=True,
                    )
                    if isinstance(depth_result, Exception):
                        manual_warnings.append(
                            f"订单簿校验失败：{depth_result}；本次为人工建卡，仅作风险提示，未拦截创建"
                        )
                    elif isinstance(depth_result, BaseException):
                        raise depth_result
                    if isinstance(existing_pairs, BaseException):
                        raise existing_pairs
                else:
                    existing_pairs = await self.client.list_pairs()
            except AstroClientError as exc:
                return with_manual_warnings(
                    AstroAlertActionResult(
                        enabled=True,
                        status="failed",
                        action="list",
                        message=f"查询现有卡片失败，{exc.message}",
                        pair_name=pair_name,
                        pair_type=pair_type,
                    )
                )

            same_name_pairs = [item for item in existing_pairs if item.get("name") == pair_name]
            attempt_messages: list[str] = []
            blocked_attempt: dict | None = None
            try:
                for planned in pair_variants:
                    attempt = await self.attempts.get(route_key(planned))
                    if not attempt or attempt["state"] not in {"pending", "unknown"}:
                        continue
                    if any(_same_route(existing, planned) for existing in same_name_pairs):
                        await self.attempts.mark(route_key(planned), "reconciled", "远端已看到该路线")
                        continue
                    if manual_override:
                        manual_warnings.append("上次提交结果未知，可能产生重复卡")
                        continue
                    # Only unknown expires after 24h. Pending may represent an
                    # interrupted process and must never expire automatically.
                    if attempt["state"] == "unknown" and (
                        datetime.now(UTC) - datetime.fromisoformat(attempt["updated_at"])
                        > timedelta(hours=24)
                    ):
                        attempt_messages.append(
                            f"{_route(planned)} 上次提交结果未知（{attempt['updated_at']}），"
                            "已超过 24 小时，按过期规则重新创建"
                        )
                        continue
                    blocked_attempt = attempt
                # Reconcile every visible sibling before returning a retry barrier.
                if blocked_attempt is not None:
                    return with_manual_warnings(AstroAlertActionResult(
                        enabled=True, status="skipped", action="uncertain",
                        message=f"上次提交结果未知（{blocked_attempt['updated_at']}），远端暂未看到，"
                                "未重复创建；请在 Astro 核实",
                        pair_name=pair_name, pair_type=pair_type,
                    ))
            except Exception as exc:  # noqa: BLE001 - ledger failures must prevent external writes.
                return with_manual_warnings(AstroAlertActionResult(
                    enabled=True, status="failed", action="add",
                    message=f"查询或对账尝试账本失败，未创建 Astro 卡片：{exc}",
                    pair_name=pair_name, pair_type=pair_type,
                ))
            # Older Aster GC cards retained the native Aster leg. Recognize those
            # siblings for conflict checks only; never create or mutate that old route.
            legacy_aster_variants = []
            for planned in all_pair_variants:
                if "gc-aster" in {planned.get("buyEx"), planned.get("sellEx")}:
                    legacy = dict(planned)
                    for leg in ("buyEx", "sellEx"):
                        if legacy.get(leg) == "gc-aster":
                            legacy[leg] = "aster"
                    legacy_aster_variants.append(legacy)
            hl_market_conflicts = [
                existing
                for existing in same_name_pairs
                if any(
                    _same_exchange_route(existing, planned) and not _same_route(existing, planned)
                    for planned in pair_variants + legacy_aster_variants
                )
            ]
            if hl_market_conflicts:
                return AstroAlertActionResult(
                    enabled=True,
                    status="skipped",
                    action="conflict",
                    message=(
                        f"已跳过，Astro 同名同交易所路线的 HL 市场不同："
                        f"现有 {_routes(hl_market_conflicts)}；目标 {_routes(pair_variants)}。"
                        "请核实已有卡片市场，未自动创建重复卡片"
                    ),
                    pair_name=pair_name,
                    pair_type=pair_type,
                )
            conflicting_pairs = [
                existing
                for existing in same_name_pairs
                if not any(_same_route(existing, planned)
                           for planned in all_pair_variants + legacy_aster_variants)
            ]
            if (
                conflicting_pairs
                and not (self.allow_same_name_variants or allow_route_variants)
                and not manual_override
            ):
                return AstroAlertActionResult(
                    enabled=True,
                    status="skipped",
                    action="conflict",
                    message=(
                        f"已跳过，Astro 已存在同名 {pair_name} 但类型或交易所不同："
                        f"{_routes(conflicting_pairs)}"
                    ),
                    pair_name=pair_name,
                    pair_type=pair_type,
                )
            if conflicting_pairs and manual_override:
                manual_warnings.append(
                    f"Astro 已存在同名 {pair_name} 但类型或交易所不同："
                    f"{_routes(conflicting_pairs)}；本次为人工建卡，仅作风险提示，未拦截创建"
                )

            existing_variants = [
                planned
                for planned in pair_variants
                if any(_same_route(existing, planned) for existing in same_name_pairs)
            ]
            missing_variants = [
                planned
                for planned in pair_variants
                if not any(_same_route(existing, planned) for existing in same_name_pairs)
            ]
            if not missing_variants:
                return with_manual_warnings(
                    AstroAlertActionResult(
                        enabled=True,
                        status="skipped",
                        action="existing",
                        message=(
                            f"已跳过，Astro 已存在卡片 {pair_name} {pair_type} "
                            f"{_routes(existing_variants)}"
                        ),
                        pair_name=pair_name,
                        pair_type=pair_type,
                    )
                )

            created_variants: list[dict] = []
            if manual_review is not None:
                manual_review.metrics["variants"] = len(missing_variants)
            restart_delay = (
                self.add_restart_delay_seconds
                if add_restart_delay_seconds is None
                else add_restart_delay_seconds
            )
            for index, planned in enumerate(missing_variants):
                try:
                    if self.market_validation is not None:
                        previous = opportunity.validation
                        if manual_override:
                            result, a, b = await manual_review.qualify(self.market_validation, opportunity, risk)
                        else:
                            result, a, b = await self.market_validation.qualify_opportunity(opportunity, risk, fresh=True)
                        if previous is not None and (previous.buy_revision, previous.sell_revision) != (result.buy_revision, result.sell_revision):
                            return AstroAlertActionResult(enabled=True, status="skipped", action="market_validation", message="市场映射已改变，请重新预览")
                        if self.market_validation.should_enforce(result) and not result.capabilities.manual_card:
                            return AstroAlertActionResult(enabled=True, status="skipped", action="market_validation", message="；".join(issue.message for issue in result.blockers))
                        if not manual_override and not allow_reverse_entry and b.bid <= a.ask:
                            return AstroAlertActionResult(enabled=True, status="skipped", action="market_validation", message="最新真实盘口已不满足正向价差，未创建卡片")
                except ManualMappingChanged as exc:
                    return AstroAlertActionResult(enabled=True, status="skipped", action="market_validation", message=str(exc))
                except (ValueError, TimeoutError, httpx.HTTPError, RuntimeError) as exc:
                    return AstroAlertActionResult(enabled=True, status="failed", action="market_validation", message=f"提交前市场复核失败：{exc}")

                state, detail = await self._submit_variant(planned, source, pair_enabled, manual_review)
                if state != "created":
                    failed_route = _route(planned)
                    failure = "结果未知，请在 Astro 核实" if state == "unknown" else "创建失败"
                    message = f"{failed_route} {failure}，{detail}"
                    if created_variants:
                        message = (
                            f"部分创建成功：已创建 {_routes(created_variants)}；{message}。"
                            f"卡片状态为{_card_state_message(pair_enabled)}，"
                            f"{_disable_open_message(pair_enabled)}"
                        )
                    if attempt_messages:
                        message += "；" + "；".join(attempt_messages)
                    return with_manual_warnings(AstroAlertActionResult(
                        enabled=True, status="failed",
                        action="add_partial" if created_variants else "uncertain" if state == "unknown" else "add",
                        message=message, pair_name=pair_name, pair_type=pair_type,
                    ))
                created_variants.append(planned)
                is_last_add = index == len(missing_variants) - 1
                delay = restart_delay if is_last_add else self.add_restart_delay_seconds
                if delay > 0:
                    if manual_review is not None:
                        await manual_review.measure("sleep_ms", asyncio.sleep(delay))
                    else:
                        await asyncio.sleep(delay)

            message = (
                f"已创建{_card_state_message(pair_enabled)} {pair_name} {pair_type} "
                f"{_route(created_variants[0])}，{_disable_open_message(pair_enabled)}"
            )
            if len(created_variants) > 1:
                message += (
                    f"；同步创建 {_routes(created_variants[1:])}"
                    f"（共 {len(created_variants)} 张）"
                )
            if attempt_messages:
                message += "；" + "；".join(attempt_messages)
            if existing_variants:
                message += f"；已存在 {_routes(existing_variants)}"
            if not hl_gc_supported:
                message += "；Gate 未确认支持此 HL 市场，使用原始 HL 路线"
            return with_manual_warnings(
                AstroAlertActionResult(
                    enabled=True,
                    status="created",
                    action="add",
                    message=message,
                    pair_name=pair_name,
                    pair_type=pair_type,
                )
            )

    async def _submit_variant(
        self, planned: dict, source: str, pair_enabled: bool,
        manual_review: ManualReview | None,
    ) -> tuple[str, str]:
        key = route_key(planned)
        try:
            await self.attempts.mark_pending(planned, source, pair_enabled)
        except Exception as exc:  # noqa: BLE001 - never submit without a committed attempt.
            return "failed", f"尝试账本写入失败，未提交远端：{exc}"

        try:
            if manual_review is not None:
                await manual_review.measure("add_ms", self.client.add_pair(planned))
            else:
                await self.client.add_pair(planned)
        except BaseException as exc:
            state = (
                "failed" if isinstance(exc, AstroClientError) and exc.explicitly_rejected
                else "unknown"
            )
            message = str(exc) or type(exc).__name__
            await self._mark_attempt_best_effort(key, state, message)
            if not isinstance(exc, Exception):
                # CancelledError must reach the caller after recording uncertainty.
                raise
            return state, message
        try:
            await self.attempts.mark(key, "created", "Astro 创建成功")
        except BaseException as exc:
            await self._mark_attempt_best_effort(key, "unknown", "远端已返回成功，但账本确认失败")
            if not isinstance(exc, Exception):
                raise
            return "unknown", "远端已返回成功，但账本确认失败"
        return "created", "Astro 创建成功"

    async def _mark_attempt_best_effort(self, key: str, state: str, message: str) -> None:
        try:
            await self.attempts.mark(key, state, message)
        except Exception:
            # The committed pending row remains a retry barrier if storage fails.
            logger.exception("Failed to record Astro attempt outcome for %s", key)
