import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.core.config import Settings
from app.models.astro import AstroAlertActionResult, AstroCardCreateRequest
from app.models.opportunity import Opportunity
from app.models.settings import AstroCardSettings, LivePilotSettings, RiskSettings
from app.services.astro_client import AstroClientError
from app.services.data_filters import symbol_is_excluded
from app.services.market_labels import astro_exchange_route_variants
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig

READ_ONLY_ASTRO_EXCHANGES = frozenset({"rh-lighter"})
READ_ONLY_ASTRO_MESSAGE = (
    "RH-Lighter 已接入官方公开只读行情，但本任务未开放 Astro 建卡或交易执行"
)


def read_only_astro_exchanges(opportunity: Opportunity) -> set[str]:
    return {
        opportunity.buy_exchange.lower(),
        opportunity.sell_exchange.lower(),
    } & READ_ONLY_ASTRO_EXCHANGES


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


def _pair_variants(pair: dict) -> list[dict]:
    variants: list[dict] = []
    for buy_exchange, sell_exchange in astro_exchange_route_variants(
        str(pair.get("buyEx", "")),
        str(pair.get("sellEx", "")),
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
    ) -> AstroAlertActionResult:
        return await self._handle(
            opportunity,
            enabled=self.settings.astro_manual_card_create,
            disabled_message="Manual card creation is disabled.",
            card_request=card_request,
            card_settings_override=card_settings,
            manual_override=True,
        )

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
    ) -> AstroAlertActionResult:
        if not enabled:
            return AstroAlertActionResult(
                enabled=False,
                status="disabled",
                action="none",
                message=disabled_message,
            )
        if read_only_astro_exchanges(opportunity):
            return AstroAlertActionResult(
                enabled=True,
                status="skipped",
                action="unsupported",
                message=READ_ONLY_ASTRO_MESSAGE,
            )
        manual_warnings: list[str] = []

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
        pair_variants = _pair_variants(pair)
        if not pair_variants or any(
            "lighter" in {planned["buyEx"], planned["sellEx"]}
            for planned in pair_variants
        ):
            return with_manual_warnings(
                AstroAlertActionResult(
                    enabled=True,
                    status="skipped",
                    action="unsupported",
                    message="Lighter 卡片只允许 gc-lighter 路由，未提交普通 lighter 或未知配对",
                )
            )
        pair_name = str(pair.get("name", ""))
        pair_type = str(pair.get("type", ""))

        try:
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
        hl_market_conflicts = [
            existing
            for existing in same_name_pairs
            if any(
                _same_exchange_route(existing, planned) and not _same_route(existing, planned)
                for planned in pair_variants
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
            if not any(_same_route(existing, planned) for planned in pair_variants)
            and not (
                any("gc-lighter" in {planned["buyEx"], planned["sellEx"]} for planned in pair_variants)
                and "lighter" in {existing.get("buyEx"), existing.get("sellEx")}
            )
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
        restart_delay = (
            self.add_restart_delay_seconds
            if add_restart_delay_seconds is None
            else add_restart_delay_seconds
        )
        for index, planned in enumerate(missing_variants):
            try:
                await self.client.add_pair(planned)
            except AstroClientError as exc:
                failed_route = _route(planned)
                if created_variants:
                    return with_manual_warnings(
                        AstroAlertActionResult(
                            enabled=True,
                            status="failed",
                            action="add_partial",
                            message=(
                                f"部分创建成功：已创建 {_routes(created_variants)}；"
                                f"{failed_route} 创建失败，{exc.message}。"
                                f"卡片状态为{_card_state_message(pair_enabled)}，"
                                f"{_disable_open_message(pair_enabled)}"
                            ),
                            pair_name=pair_name,
                            pair_type=pair_type,
                        )
                    )
                return with_manual_warnings(
                    AstroAlertActionResult(
                        enabled=True,
                        status="failed",
                        action="add",
                        message=f"创建 {failed_route} 失败，{exc.message}",
                        pair_name=pair_name,
                        pair_type=pair_type,
                    )
                )
            created_variants.append(planned)
            is_last_add = index == len(missing_variants) - 1
            delay = restart_delay if is_last_add else self.add_restart_delay_seconds
            if delay > 0:
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
        if existing_variants:
            message += f"；已存在 {_routes(existing_variants)}"
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
