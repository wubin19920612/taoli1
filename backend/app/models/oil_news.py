from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class OilNewsDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    WATCH = "watch"


class OilNewsSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_RANK = {
    OilNewsSeverity.LOW: 0,
    OilNewsSeverity.MEDIUM: 1,
    OilNewsSeverity.HIGH: 2,
    OilNewsSeverity.CRITICAL: 3,
}


class OilMarketSnapshot(BaseModel):
    symbol: str = "CLUSDT"
    price: float | None = None
    change_1h_pct: float | None = None
    observed_at: datetime


class OilNewsItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    fingerprint: str
    external_id: str
    source: str
    source_feed: str
    title: str
    title_zh: str | None = None
    url: str
    summary: str | None = None
    summary_zh: str | None = None
    published_at: datetime
    fetched_at: datetime
    categories: list[str] = Field(default_factory=list)
    severity: OilNewsSeverity
    impact_score: int = Field(ge=0, le=100)
    direction: OilNewsDirection
    confidence: float = Field(ge=0, le=1)
    horizon: str
    rationale: list[str] = Field(default_factory=list)
    risk_note: str
    market: OilMarketSnapshot | None = None
    alert_status: str = "pending"
    alerted_at: datetime | None = None

    @field_validator("fingerprint", "external_id", "source", "source_feed", "title", "url")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("value must not be empty")
        return text

    @field_validator("categories", "rationale")
    @classmethod
    def normalize_text_list(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = item.strip()
            if text and text not in seen:
                normalized.append(text)
                seen.add(text)
        return normalized


class OilNewsSettings(BaseModel):
    enabled: bool = True
    poll_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    feishu_notifications_enabled: bool = True
    alert_min_severity: OilNewsSeverity = OilNewsSeverity.HIGH
    alert_max_age_minutes: int = Field(default=120, ge=5, le=10_080)
    bootstrap_alerts_enabled: bool = False


class OilNewsRefreshResult(BaseModel):
    fetched_count: int
    relevant_count: int
    inserted_count: int
    alerted_count: int
    market: OilMarketSnapshot | None = None
    errors: list[str] = Field(default_factory=list)
