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
from app.services.risk_labels import is_new_listing_opportunity


class AstroPairClient(Protocol):
    async def list_pairs(self) -> list[dict]:
        ...

    async def add_pair(self, pair: dict) -> dict:
        ...

    async def update_pair(self, pair: dict) -> dict:
        ...


def _same_route(existing: dict, planned: dict) -> bool:
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
    return f"{pair.get('buyEx')}->{pair.get('sellEx')}"


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
        new_listing_card_settings: AstroCardSettings | None = None,
        live_pilot_settings: LivePilotSettings | None = None,
        add_restart_delay_seconds: float = 3.0,
        risk_settings_loader: Callable[[], Awaitable[RiskSettings]] | None = None,
    ):
        self.client = client
        self.settings = settings
        self.planner = planner
        self.card_settings = card_settings or settings.astro_card_settings
        self.new_listing_card_settings = new_listing_card_settings or settings.astro_new_listing_card_settings
        self.live_pilot_settings = live_pilot_settings or LivePilotSettings()
        self.alert_auto_create_enabled = settings.astro_alert_auto_create
        self.add_restart_delay_seconds = add_restart_delay_seconds
        self.risk_settings_loader = risk_settings_loader

    async def handle_alert(self, opportunity: Opportunity) -> AstroAlertActionResult:
        return await self._handle(
            opportunity,
            enabled=self.alert_auto_create_enabled,
            disabled_message="自动创建卡片未开启",
            auto_open_new_listing=True,
        )

    async def handle_new_listing_alert(self, opportunity: Opportunity) -> AstroAlertActionResult:
        return await self._handle(
            opportunity,
            enabled=self.alert_auto_create_enabled,
            disabled_message="自动创建卡片未开启",
            auto_open_new_listing=True,
            add_restart_delay_seconds=0,
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
    ) -> AstroAlertActionResult:
        return await self._handle(
            opportunity,
            enabled=self.settings.astro_manual_card_create,
            disabled_message="Manual card creation is disabled.",
            card_request=card_request,
        )

    async def _handle(
        self,
        opportunity: Opportunity,
        enabled: bool,
        disabled_message: str,
        card_request: AstroCardCreateRequest | None = None,
        live_pilot: bool = False,
        auto_open_new_listing: bool = False,
        add_restart_delay_seconds: float | None = None,
    ) -> AstroAlertActionResult:
        if not enabled:
            return AstroAlertActionResult(
                enabled=False,
                status="disabled",
                action="none",
                message=disabled_message,
            )
        if self.risk_settings_loader is not None:
            try:
                risk_settings = await self.risk_settings_loader()
            except Exception as exc:  # noqa: BLE001 - fail closed before writing to Astro.
                return AstroAlertActionResult(
                    enabled=True,
                    status="failed",
                    action="risk_settings",
                    message=f"读取全局黑名单失败，已阻止创建 Astro 卡片：{exc}",
                )
            if symbol_is_excluded(opportunity.symbol, risk_settings):
                return AstroAlertActionResult(
                    enabled=True,
                    status="skipped",
                    action="excluded_symbol",
                    message=f"{opportunity.symbol} 已在全局黑名单，未创建 Astro 卡片",
                )
        if self.settings.astro_dry_run_only:
            return AstroAlertActionResult(
                enabled=True,
                status="skipped",
                action="dry_run",
                message="dry-run 模式开启，未写入 Astro",
            )

        use_new_listing_settings = auto_open_new_listing and is_new_listing_opportunity(opportunity)
        base_card_settings = self.new_listing_card_settings if use_new_listing_settings else self.card_settings
        effective_card_settings = _settings_with_create_overrides(base_card_settings, card_request)
        if use_new_listing_settings:
            effective_card_settings = effective_card_settings.model_copy(update={"open_enabled": True})
        if live_pilot and not use_new_listing_settings:
            effective_card_settings = live_pilot_card_settings(
                effective_card_settings,
                self.live_pilot_settings,
            )
        planner = self.planner or AstroPairPlanner(
            AstroPlannerConfig.from_card_settings(effective_card_settings)
        )
        plan = planner.plan(opportunity)
        if not plan.can_submit or plan.pair is None:
            reason = "；".join(plan.blockers) if plan.blockers else "当前机会无法提交 Astro"
            return AstroAlertActionResult(
                enabled=True,
                status="skipped",
                action="unsupported",
                message=reason,
            )

        if use_new_listing_settings:
            pair_enabled = True
        elif live_pilot:
            pair_enabled = self.live_pilot_settings.create_cards_enabled
        else:
            pair_enabled = effective_card_settings.open_enabled
        pair = _with_card_enabled(plan.pair, pair_enabled)
        pair_variants = _pair_variants(pair)
        pair_name = str(pair.get("name", ""))
        pair_type = str(pair.get("type", ""))

        try:
            existing_pairs = await self.client.list_pairs()
        except AstroClientError as exc:
            return AstroAlertActionResult(
                enabled=True,
                status="failed",
                action="list",
                message=f"查询现有卡片失败，{exc.message}",
                pair_name=pair_name,
                pair_type=pair_type,
            )

        same_name_pairs = [item for item in existing_pairs if item.get("name") == pair_name]
        conflicting_pairs = [
            existing
            for existing in same_name_pairs
            if not any(_same_route(existing, planned) for planned in pair_variants)
        ]
        if conflicting_pairs:
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
            return AstroAlertActionResult(
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
                    return AstroAlertActionResult(
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
                return AstroAlertActionResult(
                    enabled=True,
                    status="failed",
                    action="add",
                    message=f"创建 {failed_route} 失败，{exc.message}",
                    pair_name=pair_name,
                    pair_type=pair_type,
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
        return AstroAlertActionResult(
            enabled=True,
            status="created",
            action="add",
            message=message,
            pair_name=pair_name,
            pair_type=pair_type,
        )
