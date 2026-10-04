from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.market import MarketType

PositionSide = Literal["long", "short"]


class AccountPositionFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"


class AccountPositionAccountState(StrEnum):
    NOT_CONFIGURED = "not_configured"
    OK = "ok"
    EMPTY = "empty"
    PERMISSION_DENIED = "permission_denied"
    ERROR = "error"
    STALE = "stale"


def account_position_id(
    *,
    account_id: str,
    exchange: str,
    market_type: MarketType | str,
    raw_symbol: str,
    side: PositionSide | str,
    dex: str | None,
) -> str:
    import hashlib
    import json

    identity = [
        account_id.strip(),
        exchange.strip().lower(),
        str(market_type).strip().lower(),
        raw_symbol.strip(),
        side.strip().lower(),
        (dex or "").strip().lower(),
    ]
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"position_{digest}"


class AccountPositionIdentity(BaseModel):
    id: str = Field(min_length=73, max_length=73, pattern=r"^position_[0-9a-f]{64}$")
    account_id: str = Field(min_length=1, max_length=128)
    account_label: str = Field(min_length=1, max_length=80)
    exchange: str = Field(min_length=1, max_length=32)
    market_type: MarketType
    raw_symbol: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=128)
    side: PositionSide
    dex: str | None = Field(default=None, max_length=64)

    @field_validator("account_id", "account_label", "raw_symbol", "symbol")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("position identity text must not be empty")
        return stripped

    @field_validator("exchange")
    @classmethod
    def normalize_exchange(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("dex")
    @classmethod
    def normalize_dex(cls, value: str | None) -> str | None:
        normalized = value.strip().lower() if isinstance(value, str) else None
        return normalized or None

    @model_validator(mode="after")
    def validate_identity_id(self) -> "AccountPositionIdentity":
        expected = account_position_id(
            account_id=self.account_id,
            exchange=self.exchange,
            market_type=self.market_type,
            raw_symbol=self.raw_symbol,
            side=self.side,
            dex=self.dex,
        )
        if self.id != expected:
            raise ValueError("position id does not match its market identity")
        if self.exchange == "hyperliquid" and self.dex is None:
            raise ValueError("Hyperliquid positions require an explicit DEX")
        if self.dex is not None and self.exchange != "hyperliquid":
            raise ValueError("DEX is only valid for Hyperliquid positions")
        return self


class AccountPosition(AccountPositionIdentity):
    quantity: float = Field(ge=0)
    quantity_unit: str = Field(min_length=1, max_length=32)
    contract_quantity: float | None = Field(default=None, ge=0)
    contract_multiplier: float | None = Field(default=None, gt=0)
    entry_price: float | None = Field(default=None, gt=0)
    mark_price: float | None = Field(default=None, gt=0)
    notional_usdt: float | None = Field(default=None, ge=0)
    unrealized_pnl_usdt: float | None = None
    roi_pct: float | None = None
    leverage: float | None = Field(default=None, gt=0)
    price_basis: str = Field(min_length=1, max_length=120)
    estimated_fields: list[str] = Field(default_factory=list)
    updated_at: datetime
    freshness: AccountPositionFreshness = AccountPositionFreshness.FRESH
    age_seconds: float = Field(default=0, ge=0)


class AccountPositionAccountStatus(BaseModel):
    account_id: str
    account_label: str
    exchange: str
    market_type: MarketType
    dex: str | None = None
    configured: bool
    state: AccountPositionAccountState
    message: str
    position_count: int = Field(default=0, ge=0)
    queried_at: datetime
    data_updated_at: datetime | None = None
    age_seconds: float | None = Field(default=None, ge=0)


class AccountPositionSnapshot(BaseModel):
    positions: list[AccountPosition] = Field(default_factory=list)
    accounts: list[AccountPositionAccountStatus] = Field(default_factory=list)
    queried_at: datetime
