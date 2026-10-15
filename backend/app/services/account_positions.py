import asyncio
import hashlib
import logging
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.exchanges.base import normalize_usdt_symbol, parse_float
from app.models.account_position import (
    AccountPosition,
    AccountPositionAccountState,
    AccountPositionAccountStatus,
    AccountPositionFreshness,
    AccountPositionSnapshot,
    account_position_id,
)
from app.models.market import MarketType
from app.services.gate_twap import GateTwapClient, GateTwapError

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _finite_float(value: Any) -> float | None:
    parsed = parse_float(value)
    return parsed if parsed is not None and math.isfinite(parsed) else None


class AccountPositionQueryError(RuntimeError):
    pass


class AccountPositionPermissionError(AccountPositionQueryError):
    pass


class AccountPositionProvider(Protocol):
    exchange: str
    account_id: str
    account_label: str
    market_type: MarketType
    dex: str | None

    @property
    def configured(self) -> bool: ...

    async def fetch_positions(self) -> list[AccountPosition]: ...


class GateAccountPositionProvider:
    exchange = "gate"
    market_type = MarketType.FUTURE
    dex = None

    def __init__(
        self,
        client: GateTwapClient,
        *,
        account_id: str | None = None,
        account_label: str | None = None,
        settle: str = "usdt",
    ):
        self.client = client
        configured_id = account_id or os.getenv("GATE_ACCOUNT_ID", "default")
        configured_label = account_label or os.getenv("GATE_ACCOUNT_LABEL", "默认账户")
        opaque_account_id = hashlib.sha256(
            (configured_id.strip() or "default").encode("utf-8")
        ).hexdigest()[:16]
        self.account_id = f"gate:{opaque_account_id}"
        self.account_label = configured_label.strip() or "默认账户"
        self.settle = settle.strip().lower() or "usdt"

    @property
    def configured(self) -> bool:
        return self.client.has_credentials

    async def fetch_positions(self) -> list[AccountPosition]:
        if not self.configured:
            raise AccountPositionQueryError("Gate 账户尚未配置")
        try:
            rows = await self.client.list_positions(self.settle)
        except GateTwapError as exc:
            if exc.status_code in {401, 403}:
                raise AccountPositionPermissionError("凭据无持仓读取权限或已失效") from exc
            raise AccountPositionQueryError("Gate 持仓接口查询失败") from exc
        if not isinstance(rows, list):
            raise AccountPositionQueryError("Gate 持仓接口返回格式无效")

        contract_rows: list[dict[str, Any]] = []
        try:
            payload = await self.client.list_contracts(self.settle)
            if isinstance(payload, list):
                contract_rows = [item for item in payload if isinstance(item, dict)]
        except GateTwapError:
            logger.warning("Gate contract metadata query failed; position multipliers are unavailable")
        contracts = {
            str(item.get("name", "")).strip(): item
            for item in contract_rows
            if str(item.get("name", "")).strip()
        }
        observed_at = utc_now()
        parsed: list[AccountPosition] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            position = self._parse_position(item, contracts, observed_at)
            if position is not None:
                parsed.append(position)
        return parsed

    def _parse_position(
        self,
        item: dict[str, Any],
        contracts: dict[str, dict[str, Any]],
        observed_at: datetime,
    ) -> AccountPosition | None:
        raw_symbol = str(item.get("contract", "")).strip()
        contract_quantity = _finite_float(item.get("size"))
        if not raw_symbol or contract_quantity is None or contract_quantity == 0:
            return None
        try:
            symbol, base, _ = normalize_usdt_symbol(raw_symbol)
        except ValueError as exc:
            raise AccountPositionQueryError("Gate 持仓包含无法识别的原始市场") from exc

        mode = str(item.get("mode", "")).strip().lower()
        if mode.endswith("short"):
            side = "short"
        elif mode.endswith("long"):
            side = "long"
        else:
            side = "short" if contract_quantity < 0 else "long"
        absolute_contracts = abs(contract_quantity)
        contract_multiplier = _finite_float(
            contracts.get(raw_symbol, {}).get("quanto_multiplier")
        )
        if contract_multiplier is not None and contract_multiplier <= 0:
            contract_multiplier = None
        quantity = (
            absolute_contracts * contract_multiplier
            if contract_multiplier is not None
            else absolute_contracts
        )
        quantity_unit = base if contract_multiplier is not None else "张"

        entry_price = _finite_float(item.get("entry_price"))
        mark_price = _finite_float(item.get("mark_price"))
        notional = _finite_float(item.get("value"))
        if notional is not None:
            notional = abs(notional)
        estimated_fields: list[str] = []
        if notional is None and mark_price is not None and contract_multiplier is not None:
            notional = quantity * mark_price
            estimated_fields.append("notional_usdt")

        unrealized_pnl = _finite_float(
            item.get("unrealised_pnl", item.get("unrealized_pnl"))
        )
        roi_pct = _finite_float(item.get("roe"))
        if roi_pct is not None:
            roi_pct *= 100
        else:
            margin = _finite_float(item.get("margin"))
            if unrealized_pnl is not None and margin not in {None, 0}:
                roi_pct = unrealized_pnl / abs(margin) * 100
                estimated_fields.append("roi_pct")
        leverage = _finite_float(item.get("leverage"))
        if leverage is not None and leverage <= 0:
            leverage = None

        identity = {
            "account_id": self.account_id,
            "account_label": self.account_label,
            "exchange": self.exchange,
            "market_type": MarketType.FUTURE,
            "raw_symbol": raw_symbol,
            "symbol": symbol,
            "side": side,
            "dex": None,
        }
        return AccountPosition(
            id=account_position_id(
                account_id=self.account_id,
                exchange=self.exchange,
                market_type=MarketType.FUTURE,
                raw_symbol=raw_symbol,
                side=side,
                dex=None,
            ),
            **identity,
            quantity=quantity,
            quantity_unit=quantity_unit,
            contract_quantity=absolute_contracts,
            contract_multiplier=contract_multiplier,
            entry_price=entry_price,
            mark_price=mark_price,
            notional_usdt=notional,
            unrealized_pnl_usdt=unrealized_pnl,
            roi_pct=roi_pct,
            leverage=leverage,
            price_basis="Gate 标记价（mark_price）",
            estimated_fields=estimated_fields,
            updated_at=observed_at,
        )


@dataclass
class _CachedAccountPositions:
    positions: list[AccountPosition]
    updated_at: datetime


class AccountPositionService:
    def __init__(
        self,
        providers: list[AccountPositionProvider],
        *,
        timeout_seconds: float = 8.0,
        dynamic_provider_loader: Callable[[], Awaitable[list[AccountPositionProvider]]] | None = None,
    ):
        self.providers = providers
        self.timeout_seconds = timeout_seconds
        self.dynamic_provider_loader = dynamic_provider_loader
        self._cache: dict[tuple[str, str, str, str], _CachedAccountPositions] = {}

    def set_dynamic_provider_loader(
        self,
        loader: Callable[[], Awaitable[list[AccountPositionProvider]]] | None,
    ) -> None:
        self.dynamic_provider_loader = loader

    async def _providers(self) -> list[AccountPositionProvider]:
        dynamic: list[AccountPositionProvider] = []
        if self.dynamic_provider_loader is not None:
            try:
                dynamic = await self.dynamic_provider_loader()
            except Exception as exc:  # noqa: BLE001 - keep legacy providers available.
                logger.warning(
                    "account connection provider reload failed failure_type=%s",
                    exc.__class__.__name__,
                )
        dynamic_scopes = {
            (item.exchange, item.market_type, item.dex or "")
            for item in dynamic
        }
        static = [
            item
            for item in self.providers
            if (item.exchange, item.market_type, item.dex or "") not in dynamic_scopes
        ]
        return [*static, *dynamic]

    async def snapshot(self) -> AccountPositionSnapshot:
        queried_at = utc_now()
        providers = await self._providers()
        results = await asyncio.gather(
            *(self._query_provider(provider, queried_at) for provider in providers)
        )
        positions = [position for provider_positions, _ in results for position in provider_positions]
        accounts = [status for _, status in results]
        positions.sort(
            key=lambda item: (
                item.exchange,
                item.account_label.casefold(),
                item.symbol,
                item.market_type.value,
                item.raw_symbol,
                item.dex or "",
                item.side,
            )
        )
        return AccountPositionSnapshot(
            positions=positions,
            accounts=accounts,
            queried_at=queried_at,
        )

    async def _query_provider(
        self,
        provider: AccountPositionProvider,
        queried_at: datetime,
    ) -> tuple[list[AccountPosition], AccountPositionAccountStatus]:
        key = (
            provider.exchange,
            provider.account_id,
            provider.market_type.value,
            provider.dex or "",
        )
        if not provider.configured:
            return [], self._status(
                provider,
                queried_at,
                configured=False,
                state=AccountPositionAccountState.NOT_CONFIGURED,
                message="尚未配置账户凭据",
            )
        try:
            positions = await asyncio.wait_for(
                provider.fetch_positions(),
                timeout=self.timeout_seconds,
            )
            updated_at = max(
                (item.updated_at for item in positions),
                default=queried_at,
            )
            fresh_positions = [
                item.model_copy(
                    update={
                        "freshness": AccountPositionFreshness.FRESH,
                        "age_seconds": max(0, (queried_at - item.updated_at).total_seconds()),
                    }
                )
                for item in positions
            ]
            self._cache[key] = _CachedAccountPositions(fresh_positions, updated_at)
            state = (
                AccountPositionAccountState.OK
                if fresh_positions
                else AccountPositionAccountState.EMPTY
            )
            message = "持仓读取成功" if fresh_positions else "账户已核验，当前无未平仓持仓"
            return fresh_positions, self._status(
                provider,
                queried_at,
                configured=True,
                state=state,
                message=message,
                position_count=len(fresh_positions),
                data_updated_at=updated_at,
            )
        except AccountPositionPermissionError:
            return self._failed_result(
                provider,
                queried_at,
                key,
                AccountPositionAccountState.PERMISSION_DENIED,
                "凭据无持仓读取权限或已失效",
            )
        except TimeoutError:
            return self._failed_result(
                provider,
                queried_at,
                key,
                AccountPositionAccountState.ERROR,
                "账户持仓接口查询超时",
            )
        except Exception as exc:  # noqa: BLE001 - isolate failures to one account scope.
            logger.warning(
                "account position query failed exchange=%s account_id=%s failure_type=%s",
                provider.exchange,
                provider.account_id,
                exc.__class__.__name__,
            )
            return self._failed_result(
                provider,
                queried_at,
                key,
                AccountPositionAccountState.ERROR,
                "账户持仓接口查询失败",
            )

    def _failed_result(
        self,
        provider: AccountPositionProvider,
        queried_at: datetime,
        key: tuple[str, str, str, str],
        failure_state: AccountPositionAccountState,
        failure_message: str,
    ) -> tuple[list[AccountPosition], AccountPositionAccountStatus]:
        cached = self._cache.get(key)
        if cached is None:
            return [], self._status(
                provider,
                queried_at,
                configured=True,
                state=failure_state,
                message=failure_message,
            )
        age_seconds = max(0, (queried_at - cached.updated_at).total_seconds())
        stale_positions = [
            item.model_copy(
                update={
                    "freshness": AccountPositionFreshness.STALE,
                    "age_seconds": age_seconds,
                }
            )
            for item in cached.positions
        ]
        return stale_positions, self._status(
            provider,
            queried_at,
            configured=True,
            state=AccountPositionAccountState.STALE,
            message=f"{failure_message}；当前显示最近一次成功快照",
            position_count=len(stale_positions),
            data_updated_at=cached.updated_at,
            age_seconds=age_seconds,
        )

    @staticmethod
    def _status(
        provider: AccountPositionProvider,
        queried_at: datetime,
        *,
        configured: bool,
        state: AccountPositionAccountState,
        message: str,
        position_count: int = 0,
        data_updated_at: datetime | None = None,
        age_seconds: float | None = None,
    ) -> AccountPositionAccountStatus:
        return AccountPositionAccountStatus(
            account_id=provider.account_id,
            account_label=provider.account_label,
            exchange=provider.exchange,
            market_type=provider.market_type,
            dex=provider.dex,
            configured=configured,
            state=state,
            message=message,
            position_count=position_count,
            queried_at=queried_at,
            data_updated_at=data_updated_at,
            age_seconds=age_seconds,
        )
