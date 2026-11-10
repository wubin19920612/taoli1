"""Request-time quote ages; local receipt time alone controls age filtering."""

from datetime import UTC, datetime
from typing import TypedDict

from app.models.market import MarketSnapshot


class QuoteAges(TypedDict):
    age_ms: float
    source_age_ms: float | None
    time_source: str


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def quote_age_fields(
    local_timestamp: datetime,
    source_timestamp: datetime | None,
    *,
    now: datetime,
    time_source: str | None = None,
) -> QuoteAges:
    current = _as_utc(now)
    return {
        "age_ms": (current - _as_utc(local_timestamp)).total_seconds() * 1000,
        "source_age_ms": (
            (current - _as_utc(source_timestamp)).total_seconds() * 1000
            if source_timestamp is not None else None
        ),
        "time_source": time_source or ("upstream" if source_timestamp is not None else "received"),
    }


def market_source_timestamp(market: MarketSnapshot) -> datetime | None:
    if market.book_timestamp is not None:
        # Book enrichment may also copy its local fallback into upstream_timestamp.
        return None if market.quote_time_source == "received" else market.book_timestamp
    return market.upstream_timestamp


def market_time_source(market: MarketSnapshot) -> str:
    if market.quote_time_source in {"ws", "received_batch"} or market.book_timestamp is not None:
        return market.quote_time_source
    return "upstream" if market.upstream_timestamp is not None else "received"
