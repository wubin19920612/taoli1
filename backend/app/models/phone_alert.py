from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.models.market import MarketType


class PhonePriceAlertCondition(StrEnum):
    ABOVE = "above"
    BELOW = "below"


class PhonePriceAlertPriceField(StrEnum):
    MARK_PRICE = "mark_price"
    INDEX_PRICE = "index_price"
    MID_PRICE = "mid_price"
    BID = "bid"
    ASK = "ask"


def _normalize_symbol(value: str) -> str:
    return value.strip().upper().replace("-", "").replace("_", "")


def _normalize_exchange(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


class PhonePriceAlertRule(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    enabled: bool = True
    symbol: str
    exchange: str | None = None
    market_type: MarketType = MarketType.FUTURE
    price_field: PhonePriceAlertPriceField = PhonePriceAlertPriceField.MARK_PRICE
    condition: PhonePriceAlertCondition
    target_price: float = Field(gt=0)
    cooldown_seconds: int = Field(default=300, ge=0)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)

    @field_validator("exchange")
    @classmethod
    def normalize_exchange(cls, value: str | None) -> str | None:
        return _normalize_exchange(value)


class PhonePriceAlertEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    rule_id: str
    symbol: str
    exchange: str
    market_type: MarketType
    price_field: PhonePriceAlertPriceField
    condition: PhonePriceAlertCondition
    target_price: float
    observed_price: float
    status: str
    message: str
    created_at: datetime

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)

    @field_validator("exchange")
    @classmethod
    def normalize_required_exchange(cls, value: str) -> str:
        normalized = _normalize_exchange(value)
        return normalized or value


class PhonePriceAlertDiagnostic(BaseModel):
    rule_id: str
    rule_name: str
    symbol: str
    exchange: str | None = None
    market_type: MarketType
    price_field: PhonePriceAlertPriceField
    resolved_price_field: PhonePriceAlertPriceField | None = None
    condition: PhonePriceAlertCondition
    target_price: float
    market_found: bool
    observed_price: float | None = None
    triggered: bool
    exchange_error: str | None = None
    reason: str


class PhonePriceAlertDiagnostics(BaseModel):
    phone_enabled: bool
    items: list[PhonePriceAlertDiagnostic]
