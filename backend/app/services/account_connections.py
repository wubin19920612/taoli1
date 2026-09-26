import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from app.db.repositories import AccountConnectionRepository
from app.models.account_connection import (
    AccountConnectionExchange,
    AccountConnectionOverview,
    AccountConnectionSecretPayload,
    AccountConnectionTestResult,
    AccountConnectionTestScope,
    AccountConnectionUpdate,
    AccountConnectionView,
    AccountConnectionWrite,
    StoredAccountConnection,
    SupportedAccountExchange,
)
from app.models.account_position import AccountPositionAccountState
from app.models.market import MarketType
from app.services.account_position_providers import (
    CcxtAccountPositionProvider,
    HyperliquidAccountPositionProvider,
)
from app.services.account_positions import (
    AccountPositionPermissionError,
    AccountPositionProvider,
)

SUPPORTED_EXCHANGES = [
    SupportedAccountExchange(
        exchange=AccountConnectionExchange.BINANCE,
        label="Binance",
        supports_spot=True,
        supports_futures=True,
        note="只读 API；支持现货余额和 U 本位永续持仓",
    ),
    SupportedAccountExchange(
        exchange=AccountConnectionExchange.OKX,
        label="OKX",
        supports_spot=True,
        supports_futures=True,
        requires_passphrase=True,
        note="只读 API；需要 API Key、Secret 和 Passphrase",
    ),
    SupportedAccountExchange(
        exchange=AccountConnectionExchange.BYBIT,
        label="Bybit",
        supports_spot=True,
        supports_futures=True,
        note="只读 API；支持现货余额和永续持仓",
    ),
    SupportedAccountExchange(
        exchange=AccountConnectionExchange.GATE,
        label="Gate",
        supports_spot=True,
        supports_futures=True,
        note="只读 API；支持现货余额和永续持仓",
    ),
    SupportedAccountExchange(
        exchange=AccountConnectionExchange.BITGET,
        label="Bitget",
        supports_spot=True,
        supports_futures=True,
        requires_passphrase=True,
        note="只读 API；需要 API Key、Secret 和 Passphrase",
    ),
    SupportedAccountExchange(
        exchange=AccountConnectionExchange.HYPERLIQUID,
        label="Hyperliquid",
        supports_spot=False,
        supports_futures=True,
        uses_public_address=True,
        note="仅需公开地址；每个连接固定查询 main 或一个 builder-deployed DEX",
    ),
]
PASSPHRASE_EXCHANGES = {
    AccountConnectionExchange.OKX,
    AccountConnectionExchange.BITGET,
}
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
DEX_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class AccountConnectionConfigurationError(ValueError):
    pass


class AccountConnectionNotFoundError(LookupError):
    pass


class AccountCredentialCipher:
    def __init__(self, master_key: str):
        self._fernet: Fernet | None = None
        self.message = "账户凭据加密密钥尚未配置"
        normalized = master_key.strip()
        if not normalized:
            return
        try:
            self._fernet = Fernet(normalized.encode("ascii"))
            self.message = "账户凭据加密存储已就绪"
        except (ValueError, UnicodeEncodeError):
            self.message = "账户凭据加密密钥格式无效"

    @property
    def ready(self) -> bool:
        return self._fernet is not None

    def encrypt(self, payload: AccountConnectionSecretPayload) -> str:
        if self._fernet is None:
            raise AccountConnectionConfigurationError(self.message)
        serialized = payload.model_dump_json().encode("utf-8")
        return self._fernet.encrypt(serialized).decode("ascii")

    def decrypt(self, token: str) -> AccountConnectionSecretPayload:
        if self._fernet is None:
            raise AccountConnectionConfigurationError(self.message)
        try:
            serialized = self._fernet.decrypt(token.encode("ascii"))
            return AccountConnectionSecretPayload.model_validate_json(serialized)
        except (InvalidToken, ValueError, UnicodeEncodeError, json.JSONDecodeError) as exc:
            raise AccountConnectionConfigurationError("已保存的账户凭据无法解密") from exc


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    get_secret_value = getattr(value, "get_secret_value", None)
    raw = get_secret_value() if callable(get_secret_value) else str(value)
    return raw.strip()


def _mask(value: str, *, address: bool = False) -> str:
    if not value:
        return "未配置"
    if address:
        return f"{value[:6]}...{value[-4:]}"
    suffix = value[-4:] if len(value) >= 4 else value[-2:]
    return f"****{suffix}"


class AccountConnectionService:
    def __init__(
        self,
        repository: AccountConnectionRepository,
        *,
        master_key: str,
        dashboard_password: str,
    ):
        self.repository = repository
        self.cipher = AccountCredentialCipher(master_key)
        self.dashboard_password_configured = bool(dashboard_password)
        self._provider_cache: dict[str, tuple[str, list[AccountPositionProvider]]] = {}

    @property
    def storage_ready(self) -> bool:
        return self.dashboard_password_configured and self.cipher.ready

    @property
    def storage_message(self) -> str:
        if not self.dashboard_password_configured:
            return "必须先配置非空的 DASHBOARD_PASSWORD"
        return self.cipher.message

    def ensure_storage_ready(self) -> None:
        if not self.storage_ready:
            raise AccountConnectionConfigurationError(self.storage_message)

    async def overview(self) -> AccountConnectionOverview:
        connections = await self.repository.list()
        return AccountConnectionOverview(
            storage_ready=self.storage_ready,
            storage_message=self.storage_message,
            supported_exchanges=SUPPORTED_EXCHANGES,
            connections=[AccountConnectionView.model_validate(item) for item in connections],
        )

    @staticmethod
    def _credentials(payload: AccountConnectionWrite | AccountConnectionUpdate) -> AccountConnectionSecretPayload:
        return AccountConnectionSecretPayload(
            api_key=_secret_value(payload.api_key),
            api_secret=_secret_value(payload.api_secret),
            passphrase=_secret_value(payload.passphrase),
            public_address=_secret_value(payload.public_address),
        )

    @staticmethod
    def _validate_credentials(
        exchange: AccountConnectionExchange,
        credentials: AccountConnectionSecretPayload,
        dex: str | None,
    ) -> None:
        if exchange == AccountConnectionExchange.HYPERLIQUID:
            if not ADDRESS_PATTERN.fullmatch(credentials.public_address):
                raise AccountConnectionConfigurationError("Hyperliquid 公开地址格式无效")
            if not dex or not DEX_PATTERN.fullmatch(dex):
                raise AccountConnectionConfigurationError("Hyperliquid DEX 格式无效")
            return
        if not credentials.api_key or not credentials.api_secret:
            raise AccountConnectionConfigurationError("API Key 和 Secret 均为必填项")
        if exchange in PASSPHRASE_EXCHANGES and not credentials.passphrase:
            raise AccountConnectionConfigurationError("该交易所还需要 Passphrase")

    @staticmethod
    def _credential_hint(
        exchange: AccountConnectionExchange,
        credentials: AccountConnectionSecretPayload,
    ) -> str:
        if exchange == AccountConnectionExchange.HYPERLIQUID:
            return _mask(credentials.public_address, address=True)
        return _mask(credentials.api_key)

    async def create(self, payload: AccountConnectionWrite) -> AccountConnectionView:
        self.ensure_storage_ready()
        credentials = self._credentials(payload)
        self._validate_credentials(payload.exchange, credentials, payload.dex)
        now = datetime.now(UTC)
        connection = StoredAccountConnection(
            id=f"account_{uuid4().hex}",
            exchange=payload.exchange,
            account_label=payload.account_label,
            enabled=payload.enabled,
            include_spot=payload.include_spot,
            include_futures=payload.include_futures,
            dex=payload.dex,
            credential_hint=self._credential_hint(payload.exchange, credentials),
            encrypted_credentials=self.cipher.encrypt(credentials),
            created_at=now,
            updated_at=now,
        )
        await self.repository.save(connection)
        return AccountConnectionView.model_validate(connection)

    async def update(
        self,
        connection_id: str,
        payload: AccountConnectionUpdate,
    ) -> AccountConnectionView:
        self.ensure_storage_ready()
        current = await self.repository.get(connection_id)
        if current is None:
            raise AccountConnectionNotFoundError("账户连接不存在")
        credentials = self.cipher.decrypt(current.encrypted_credentials)
        submitted = self._credentials(payload)
        updates = payload.model_fields_set
        for field in ("api_key", "api_secret", "passphrase", "public_address"):
            value = getattr(submitted, field)
            if field in updates and value:
                setattr(credentials, field, value)
        account_label = payload.account_label if payload.account_label is not None else current.account_label
        enabled = payload.enabled if payload.enabled is not None else current.enabled
        include_spot = payload.include_spot if payload.include_spot is not None else current.include_spot
        include_futures = (
            payload.include_futures
            if payload.include_futures is not None
            else current.include_futures
        )
        dex = payload.dex if "dex" in updates else current.dex
        validated = AccountConnectionWrite(
            exchange=current.exchange,
            account_label=account_label,
            enabled=enabled,
            include_spot=include_spot,
            include_futures=include_futures,
            dex=dex,
        )
        self._validate_credentials(current.exchange, credentials, validated.dex)
        now = datetime.now(UTC)
        changed_connection = current.model_copy(
            update={
                "account_label": validated.account_label,
                "enabled": validated.enabled,
                "include_spot": validated.include_spot,
                "include_futures": validated.include_futures,
                "dex": validated.dex,
                "credential_hint": self._credential_hint(current.exchange, credentials),
                "encrypted_credentials": self.cipher.encrypt(credentials),
                "last_test_state": None,
                "last_test_message": None,
                "last_tested_at": None,
                "updated_at": now,
            }
        )
        await self.repository.save(changed_connection)
        await self._invalidate(connection_id)
        return AccountConnectionView.model_validate(changed_connection)

    async def delete(self, connection_id: str) -> None:
        self.ensure_storage_ready()
        if not await self.repository.delete(connection_id):
            raise AccountConnectionNotFoundError("账户连接不存在")
        await self._invalidate(connection_id)

    def _build_providers(
        self,
        connection: StoredAccountConnection,
        credentials: AccountConnectionSecretPayload,
    ) -> list[AccountPositionProvider]:
        account_id = f"{connection.exchange.value}:{connection.id}"
        if connection.exchange == AccountConnectionExchange.HYPERLIQUID:
            return [
                HyperliquidAccountPositionProvider(
                    account_id=account_id,
                    account_label=connection.account_label,
                    public_address=credentials.public_address,
                    dex=connection.dex or "main",
                )
            ]
        providers: list[AccountPositionProvider] = []
        credential_dict = credentials.model_dump()
        if connection.include_spot:
            providers.append(
                CcxtAccountPositionProvider(
                    exchange=connection.exchange.value,
                    account_id=account_id,
                    account_label=connection.account_label,
                    market_type=MarketType.SPOT,
                    credentials=credential_dict,
                )
            )
        if connection.include_futures:
            providers.append(
                CcxtAccountPositionProvider(
                    exchange=connection.exchange.value,
                    account_id=account_id,
                    account_label=connection.account_label,
                    market_type=MarketType.FUTURE,
                    credentials=credential_dict,
                )
            )
        return providers

    async def providers(self) -> list[AccountPositionProvider]:
        if not self.storage_ready:
            return []
        connections = [item for item in await self.repository.list() if item.enabled]
        active_ids = {item.id for item in connections}
        for connection_id in list(self._provider_cache):
            if connection_id not in active_ids:
                await self._invalidate(connection_id)
        result: list[AccountPositionProvider] = []
        for connection in connections:
            stamp = connection.updated_at.isoformat()
            cached = self._provider_cache.get(connection.id)
            if cached is None or cached[0] != stamp:
                await self._invalidate(connection.id)
                credentials = self.cipher.decrypt(connection.encrypted_credentials)
                providers = self._build_providers(connection, credentials)
                self._provider_cache[connection.id] = (stamp, providers)
            result.extend(self._provider_cache[connection.id][1])
        return result

    async def test_saved(self, connection_id: str) -> AccountConnectionTestResult:
        self.ensure_storage_ready()
        connection = await self.repository.get(connection_id)
        if connection is None:
            raise AccountConnectionNotFoundError("账户连接不存在")
        credentials = self.cipher.decrypt(connection.encrypted_credentials)
        result = await self._test_connection(connection, credentials)
        state = self._aggregate_test_state(result)
        updated = connection.model_copy(
            update={
                "last_test_state": state,
                "last_test_message": "；".join(scope.message for scope in result.scopes),
                "last_tested_at": result.tested_at,
            }
        )
        await self.repository.save(updated)
        return result

    async def test_draft(self, payload: AccountConnectionWrite) -> AccountConnectionTestResult:
        self.ensure_storage_ready()
        credentials = self._credentials(payload)
        self._validate_credentials(payload.exchange, credentials, payload.dex)
        now = datetime.now(UTC)
        connection = StoredAccountConnection(
            id=f"draft_{uuid4().hex}",
            exchange=payload.exchange,
            account_label=payload.account_label,
            enabled=True,
            include_spot=payload.include_spot,
            include_futures=payload.include_futures,
            dex=payload.dex,
            credential_hint=self._credential_hint(payload.exchange, credentials),
            encrypted_credentials="draft",
            created_at=now,
            updated_at=now,
        )
        return await self._test_connection(connection, credentials, connection_id=None)

    async def _test_connection(
        self,
        connection: StoredAccountConnection,
        credentials: AccountConnectionSecretPayload,
        *,
        connection_id: str | None = "saved",
    ) -> AccountConnectionTestResult:
        providers = self._build_providers(connection, credentials)
        scopes: list[AccountConnectionTestScope] = []
        try:
            for provider in providers:
                try:
                    positions = await provider.fetch_positions()
                    state = (
                        AccountPositionAccountState.OK
                        if positions
                        else AccountPositionAccountState.EMPTY
                    )
                    message = "读取成功" if positions else "已核验，当前无持仓"
                    scopes.append(
                        AccountConnectionTestScope(
                            market_type=provider.market_type,
                            dex=provider.dex,
                            state=state,
                            message=message,
                            position_count=len(positions),
                        )
                    )
                except AccountPositionPermissionError:
                    scopes.append(
                        AccountConnectionTestScope(
                            market_type=provider.market_type,
                            dex=provider.dex,
                            state=AccountPositionAccountState.PERMISSION_DENIED,
                            message="凭据无持仓读取权限或已失效",
                        )
                    )
                except Exception:  # noqa: BLE001 - keep other account scopes testable.
                    scopes.append(
                        AccountConnectionTestScope(
                            market_type=provider.market_type,
                            dex=provider.dex,
                            state=AccountPositionAccountState.ERROR,
                            message="账户接口测试失败",
                        )
                    )
        finally:
            for provider in providers:
                close = getattr(provider, "close", None)
                if close is not None:
                    await close()
        success = all(
            scope.state in {AccountPositionAccountState.OK, AccountPositionAccountState.EMPTY}
            for scope in scopes
        )
        return AccountConnectionTestResult(
            connection_id=connection.id if connection_id == "saved" else connection_id,
            exchange=connection.exchange,
            account_label=connection.account_label,
            success=success,
            scopes=scopes,
            tested_at=datetime.now(UTC),
        )

    @staticmethod
    def _aggregate_test_state(result: AccountConnectionTestResult) -> AccountPositionAccountState:
        states = {scope.state for scope in result.scopes}
        if AccountPositionAccountState.PERMISSION_DENIED in states:
            return AccountPositionAccountState.PERMISSION_DENIED
        if AccountPositionAccountState.ERROR in states:
            return AccountPositionAccountState.ERROR
        if AccountPositionAccountState.OK in states:
            return AccountPositionAccountState.OK
        return AccountPositionAccountState.EMPTY

    async def _invalidate(self, connection_id: str) -> None:
        cached = self._provider_cache.pop(connection_id, None)
        if cached is None:
            return
        for provider in cached[1]:
            close = getattr(provider, "close", None)
            if close is not None:
                await close()

    async def close(self) -> None:
        for connection_id in list(self._provider_cache):
            await self._invalidate(connection_id)

    async def aclose(self) -> None:
        await self.close()
