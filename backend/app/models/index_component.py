from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def _normalize_token(value: str) -> str:
    return value.strip().upper()


def _normalize_source(value: str) -> str:
    return value.strip().lower()


class IndexComponent(BaseModel):
    source: str
    symbol: str
    weight: float | None = None
    price: float | None = None
    extra: dict[str, object] = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        return _normalize_source(value)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return _normalize_token(value)

    def identity(self) -> str:
        return f"{self.source}:{self.symbol}"


def normalize_components(components: list[IndexComponent]) -> list[IndexComponent]:
    return sorted(
        components,
        key=lambda item: (
            item.source,
            item.symbol,
            "" if item.weight is None else f"{item.weight:.12g}",
            "" if item.price is None else f"{item.price:.12g}",
            json.dumps(item.extra, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        ),
    )


def stable_component_hash(components: list[IndexComponent]) -> str:
    normalized = [
        item.model_dump(mode="json", exclude={"price"}, exclude_none=True)
        for item in normalize_components(components)
    ]
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class IndexComponentSnapshot(BaseModel):
    exchange: str
    symbol: str
    components: list[IndexComponent]
    component_hash: str
    source: str
    observed_at: datetime

    @field_validator("exchange")
    @classmethod
    def normalize_exchange(cls, value: str) -> str:
        return _normalize_source(value)

    @field_validator("symbol")
    @classmethod
    def normalize_snapshot_symbol(cls, value: str) -> str:
        return _normalize_token(value)

    @classmethod
    def from_components(
        cls,
        *,
        exchange: str,
        symbol: str,
        components: list[IndexComponent],
        source: str,
        observed_at: datetime,
    ) -> "IndexComponentSnapshot":
        normalized_components = normalize_components(components)
        return cls(
            exchange=exchange,
            symbol=symbol,
            components=normalized_components,
            component_hash=stable_component_hash(normalized_components),
            source=source,
            observed_at=observed_at,
        )


class IndexComponentChange(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    exchange: str
    symbol: str
    old_hash: str
    new_hash: str
    old_components: list[IndexComponent]
    new_components: list[IndexComponent]
    added_components: list[IndexComponent]
    removed_components: list[IndexComponent]
    changed_components: list[IndexComponent]
    source: str
    alert_status: str
    created_at: datetime

    @field_validator("exchange")
    @classmethod
    def normalize_change_exchange(cls, value: str) -> str:
        return _normalize_source(value)

    @field_validator("symbol")
    @classmethod
    def normalize_change_symbol(cls, value: str) -> str:
        return _normalize_token(value)


class IndexComponentWatchItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    symbol: str
    note: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("symbol")
    @classmethod
    def normalize_watch_symbol(cls, value: str) -> str:
        return _normalize_token(value)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None
