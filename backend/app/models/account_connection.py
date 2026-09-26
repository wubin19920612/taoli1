from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from app.models.account_position import AccountPositionAccountState
from app.models.market import MarketType


class AccountConnectionExchange(StrEnum):
    BINANCE = "binance"
    OKX = "okx"
    BYBIT = "bybit"
    GATE = "gate"
    BITGET = "bitget"
    HYPERLIQUID = "hyperliquid"


class AccountConnectionSecretPayload(BaseModel):
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    public_address: str = ""


class AccountConnectionWrite(BaseModel):
    exchange: AccountConnectionExchange
    account_label: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    include_spot: bool = True
    include_futures: bool = True
    dex: str | None = Field(default=None, max_length=64)
    api_key: SecretStr | None = Field(default=None, max_length=512)
    api_secret: SecretStr | None = Field(default=None, max_length=512)
    passphrase: SecretStr | None = Field(default=None, max_length=512)
    public_address: SecretStr | None = Field(default=None, max_length=128)

    @field_validator("account_label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        return value.strip()

    @field_validator("dex")
    @classmethod
    def normalize_dex(cls, value: str | None) -> str | None:
        normalized = value.strip().lower() if isinstance(value, str) else None
        return normalized or None

    @model_validator(mode="after")
    def validate_scopes(self) -> "AccountConnectionWrite":
        if self.exchange == AccountConnectionExchange.HYPERLIQUID:
            if self.include_spot:
                raise ValueError("Hyperliquid 连接当前只支持永续持仓")
            if not self.include_futures:
                raise ValueError("Hyperliquid 连接必须启用永续持仓")
            if self.dex is None:
                self.dex = "main"
        else:
            if not self.include_spot and not self.include_futures:
                raise ValueError("至少启用现货或永续持仓读取")
            if self.dex is not None:
                raise ValueError("只有 Hyperliquid 连接可以配置 DEX")
        return self


class AccountConnectionUpdate(BaseModel):
    account_label: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    include_spot: bool | None = None
    include_futures: bool | None = None
    dex: str | None = Field(default=None, max_length=64)
    api_key: SecretStr | None = Field(default=None, max_length=512)
    api_secret: SecretStr | None = Field(default=None, max_length=512)
    passphrase: SecretStr | None = Field(default=None, max_length=512)
    public_address: SecretStr | None = Field(default=None, max_length=128)

    @field_validator("account_label")
    @classmethod
    def strip_optional_label(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else None

    @field_validator("dex")
    @classmethod
    def normalize_optional_dex(cls, value: str | None) -> str | None:
        normalized = value.strip().lower() if isinstance(value, str) else None
        return normalized or None


class SupportedAccountExchange(BaseModel):
    exchange: AccountConnectionExchange
    label: str
    supports_spot: bool
    supports_futures: bool
    requires_passphrase: bool = False
    uses_public_address: bool = False
    note: str


class AccountConnectionView(BaseModel):
    id: str
    exchange: AccountConnectionExchange
    account_label: str
    enabled: bool
    include_spot: bool
    include_futures: bool
    dex: str | None = None
    credential_hint: str
    last_test_state: AccountPositionAccountState | None = None
    last_test_message: str | None = None
    last_tested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class StoredAccountConnection(AccountConnectionView):
    encrypted_credentials: str


class AccountConnectionOverview(BaseModel):
    storage_ready: bool
    storage_message: str
    supported_exchanges: list[SupportedAccountExchange]
    connections: list[AccountConnectionView]


class AccountConnectionTestScope(BaseModel):
    market_type: MarketType
    dex: str | None = None
    state: AccountPositionAccountState
    message: str
    position_count: int = Field(default=0, ge=0)


class AccountConnectionTestResult(BaseModel):
    connection_id: str | None = None
    exchange: AccountConnectionExchange
    account_label: str
    success: bool
    scopes: list[AccountConnectionTestScope]
    tested_at: datetime


AccountConnectionTestState = Literal["ok", "permission_denied", "error"]
