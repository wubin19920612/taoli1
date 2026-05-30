from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class AnnouncementKind(StrEnum):
    LISTING = "listing"
    DELISTING = "delisting"
    OTHER = "other"


class ExchangeAnnouncement(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    exchange: str
    announcement_id: str
    kind: AnnouncementKind
    title: str
    url: str
    source: str
    category: str | None = None
    published_at: datetime
    fetched_at: datetime
    alert_status: str = "pending"

    @field_validator("exchange", "source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("announcement_id", "title", "url")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("value must not be empty")
        return text

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class AnnouncementSettings(BaseModel):
    enabled: bool = True
    poll_interval_seconds: int = Field(default=300, ge=30, le=86_400)
    record_exchanges: list[str] = Field(default_factory=lambda: ["okx", "bybit", "bitget"])
    alert_exchanges: list[str] = Field(default_factory=list)
    bootstrap_alerts_enabled: bool = False

    @field_validator("record_exchanges", "alert_exchanges")
    @classmethod
    def normalize_exchanges(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            exchange = item.strip().lower()
            if not exchange or exchange in seen:
                continue
            normalized.append(exchange)
            seen.add(exchange)
        return normalized


class AnnouncementFilters(BaseModel):
    exchange: str | None = None
    kind: AnnouncementKind | None = None
    limit: int = Field(default=100, ge=1, le=500)
