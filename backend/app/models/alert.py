from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertRule(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    enabled: bool = True
    types: list[str] = Field(default_factory=lambda: ["SF", "FF", "SS"])
    include_exchanges: list[str] = Field(default_factory=list)
    exclude_exchanges: list[str] = Field(default_factory=list)
    include_symbols: list[str] = Field(default_factory=list)
    exclude_symbols: list[str] = Field(default_factory=list)
    min_open_spread_pct: float = 0.0
    min_fee_adjusted_open_pct: float = 0.0
    min_volume_24h_usdt: float = 0.0
    max_data_age_seconds: int = 600
    excluded_risk_labels: list[str] = Field(default_factory=list)
    consecutive_hits: int = Field(default=3, ge=1)
    cooldown_seconds: int = Field(default=300, ge=0)
    severity: AlertSeverity = AlertSeverity.INFO


class AlertEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    rule_id: str
    opportunity_id: str
    symbol: str
    status: str
    message: str
    created_at: datetime
