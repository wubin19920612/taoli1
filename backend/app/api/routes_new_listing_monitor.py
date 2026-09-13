from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.new_listing import (
    NewListingAlertEvent,
    NewListingHistoryResult,
    NewListingMonitorStatus,
    NewListingSpreadSample,
    NewListingWatchItem,
)
from app.models.pair_spread import normalize_pair_spread_symbol
from app.models.second_level_sampling import SUPPORTED_SECOND_LEVEL_EXCHANGES
from app.services.new_listing_monitor import NewListingMonitor

router = APIRouter(prefix="/new-listing-monitor")


def _monitor(request: Request) -> NewListingMonitor:
    monitor = getattr(request.app.state, "new_listing_monitor", None)
    if monitor is None:
        raise HTTPException(status_code=503, detail="新币极速监控还没有准备好")
    return monitor


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@router.get("/exchanges", response_model=list[str])
async def list_new_listing_exchanges() -> list[str]:
    return list(SUPPORTED_SECOND_LEVEL_EXCHANGES)


@router.get("/watchlist", response_model=list[NewListingWatchItem])
async def list_new_listing_watchlist(request: Request) -> list[NewListingWatchItem]:
    return await _monitor(request).repo.list_watch_items()


@router.post("/watchlist", response_model=NewListingWatchItem)
async def upsert_new_listing_watch_item(
    item: NewListingWatchItem,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> NewListingWatchItem:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _monitor(request).repo.upsert_watch_item(item)


@router.delete("/watchlist/{item_id}")
async def delete_new_listing_watch_item(
    item_id: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> dict[str, bool]:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    await _monitor(request).repo.delete_watch_item(item_id)
    return {"ok": True}


@router.post("/watchlist/{item_id}/collect", response_model=list[NewListingSpreadSample])
async def collect_new_listing_watch_item(
    item_id: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> list[NewListingSpreadSample]:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    monitor = _monitor(request)
    item = await monitor.repo.get_watch_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="监控标的不存在")
    return await monitor.collect_watch_item(item)


@router.get("/status", response_model=NewListingMonitorStatus)
async def get_new_listing_monitor_status(request: Request) -> NewListingMonitorStatus:
    return await _monitor(request).status()


@router.get("/samples", response_model=list[NewListingSpreadSample])
async def list_new_listing_samples(
    request: Request,
    watch_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    minutes: int = Query(default=60, ge=1, le=60 * 24 * 30),
    limit: int = Query(default=1000, ge=1, le=20_000),
) -> list[NewListingSpreadSample]:
    normalized_symbol = normalize_pair_spread_symbol(symbol) if symbol else None
    return await _monitor(request).repo.list_samples(
        watch_id=watch_id,
        symbol=normalized_symbol,
        start_at=datetime.now(UTC) - timedelta(minutes=minutes),
        limit=limit,
    )


@router.get("/events", response_model=list[NewListingAlertEvent])
async def list_new_listing_events(
    request: Request,
    watch_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    minutes: int = Query(default=60 * 24, ge=1, le=60 * 24 * 30),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[NewListingAlertEvent]:
    normalized_symbol = normalize_pair_spread_symbol(symbol) if symbol else None
    return await _monitor(request).repo.list_events(
        watch_id=watch_id,
        symbol=normalized_symbol,
        start_at=datetime.now(UTC) - timedelta(minutes=minutes),
        limit=limit,
    )


@router.get("/history", response_model=NewListingHistoryResult)
async def query_new_listing_history(
    request: Request,
    watch_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    hours: int = Query(default=6, ge=1, le=720),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=50_000),
) -> NewListingHistoryResult:
    end = _as_utc(end_at) if end_at is not None else datetime.now(UTC)
    start = _as_utc(start_at) if start_at is not None else end - timedelta(hours=hours)
    if start > end:
        raise HTTPException(status_code=422, detail="开始时间不能晚于结束时间")
    normalized_symbol = normalize_pair_spread_symbol(symbol) if symbol else None
    return await _monitor(request).history(
        watch_id=watch_id,
        symbol=normalized_symbol,
        start_at=start,
        end_at=end,
        limit=limit,
    )
