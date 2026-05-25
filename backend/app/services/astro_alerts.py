import asyncio
from typing import Protocol

from app.core.config import Settings
from app.models.astro import AstroAlertActionResult, AstroCardCreateRequest
from app.models.opportunity import Opportunity
from app.models.settings import AstroCardSettings, LivePilotSettings
from app.services.astro_client import AstroClientError
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig


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


def _card_state_message(enabled: bool) -> str:
    return "开启卡片" if enabled else "暂停卡片"


def _disable_open_message(enabled: bool) -> str:
    return "禁开=false" if enabled else "禁开=true"


def _with_existing_id(pair: dict, existing: dict) -> dict | None:
    existing_id = existing.get("id")
    if not existing_id:
        return None
    next_pair = dict(pair)
    next_pair["id"] = existing_id
    return next_pair


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
        }.items()
        if value is not None
    }
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _settings_with_live_pilot_overrides(
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
    ):
        self.client = client
        self.settings = settings
        self.planner = planner
        self.card_settings = card_settings or settings.astro_card_settings
        self.live_pilot_settings = live_pilot_settings or LivePilotSettings()
        self.add_restart_delay_seconds = add_restart_delay_seconds

    async def handle_alert(self, opportunity: Opportunity) -> AstroAlertActionResult:
        return await self._handle(
            opportunity,
            enabled=self.settings.astro_alert_auto_create,
            disabled_message="自动创建卡片未开启",
            live_pilot=self.live_pilot_settings.enabled,
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
    ) -> AstroAlertActionResult:
        if not enabled:
            return AstroAlertActionResult(
                enabled=False,
                status="disabled",
                action="none",
                message=disabled_message,
            )
        if self.settings.astro_dry_run_only:
            return AstroAlertActionResult(
                enabled=True,
                status="skipped",
                action="dry_run",
                message="dry-run 模式开启，未写入 Astro",
            )

        effective_card_settings = _settings_with_create_overrides(self.card_settings, card_request)
        if live_pilot:
            effective_card_settings = _settings_with_live_pilot_overrides(
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

        pair_enabled = live_pilot and self.live_pilot_settings.create_cards_enabled
        pair = _with_card_enabled(plan.pair, pair_enabled)
        pair_name = str(pair.get("name", ""))
        pair_type = str(pair.get("type", ""))
        route = f"{pair.get('buyEx')}->{pair.get('sellEx')}"

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
        if not same_name_pairs:
            try:
                await self.client.add_pair(pair)
            except AstroClientError as exc:
                return AstroAlertActionResult(
                    enabled=True,
                    status="failed",
                    action="add",
                    message=f"创建失败，{exc.message}",
                    pair_name=pair_name,
                    pair_type=pair_type,
                )
            if self.add_restart_delay_seconds > 0:
                await asyncio.sleep(self.add_restart_delay_seconds)
            return AstroAlertActionResult(
                enabled=True,
                status="created",
                action="add",
                message=(
                    f"已创建{_card_state_message(pair_enabled)} "
                    f"{pair_name} {pair_type} {route}，{_disable_open_message(pair_enabled)}"
                ),
                pair_name=pair_name,
                pair_type=pair_type,
            )

        same_route_pair = next(
            (existing for existing in same_name_pairs if _same_route(existing, pair)),
            None,
        )
        if same_route_pair is not None:
            update_pair = _with_existing_id(pair, same_route_pair)
            if update_pair is None:
                return AstroAlertActionResult(
                    enabled=True,
                    status="failed",
                    action="update",
                    message=f"更新失败，Astro 同名卡片 {pair_name} 缺少 id",
                    pair_name=pair_name,
                    pair_type=pair_type,
                )
            try:
                await self.client.update_pair(update_pair)
            except AstroClientError as exc:
                return AstroAlertActionResult(
                    enabled=True,
                    status="failed",
                    action="update",
                    message=f"更新失败，{exc.message}",
                    pair_name=pair_name,
                    pair_type=pair_type,
                )
            return AstroAlertActionResult(
                enabled=True,
                status="updated",
                action="update",
                message=(
                    f"已更新{_card_state_message(pair_enabled)} "
                    f"{pair_name} {pair_type} {route}，{_disable_open_message(pair_enabled)}"
                ),
                pair_name=pair_name,
                pair_type=pair_type,
            )

        return AstroAlertActionResult(
            enabled=True,
            status="skipped",
            action="conflict",
            message=f"已跳过，Astro 已存在同名 {pair_name} 但类型或交易所不同",
            pair_name=pair_name,
            pair_type=pair_type,
        )
