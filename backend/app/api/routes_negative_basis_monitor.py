from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.negative_basis import (
    NEGATIVE_BASIS_FUTURE_EXCHANGES,
    NEGATIVE_BASIS_SPOT_EXCHANGES,
    NegativeBasisAlertEvent,
    NegativeBasisAnalysisResult,
    NegativeBasisAutoCandidate,
    NegativeBasisAutoScanSettings,
    NegativeBasisMonitorStatus,
    NegativeBasisSignalSample,
    NegativeBasisWatchItem,
)
from app.models.pair_spread import normalize_pair_spread_symbol
from app.services.negative_basis_monitor import NegativeBasisMonitor
from app.services.pair_spread_query import PairSpreadQueryError

router = APIRouter(prefix="/negative-basis-monitor")


def _monitor(request: Request) -> NegativeBasisMonitor:
    monitor = getattr(request.app.state, "negative_basis_monitor", None)
    if monitor is None:
        raise HTTPException(status_code=503, detail="负基差埋伏监控还没有准备好")
    return monitor


@router.get("/exchanges")
async def list_negative_basis_exchanges() -> dict[str, list[str]]:
    return {
        "spot": list(NEGATIVE_BASIS_SPOT_EXCHANGES),
        "future": list(NEGATIVE_BASIS_FUTURE_EXCHANGES),
    }


@router.get("/watchlist", response_model=list[NegativeBasisWatchItem])
async def list_negative_basis_watchlist(request: Request) -> list[NegativeBasisWatchItem]:
    return await _monitor(request).repo.list_watch_items()


@router.post("/watchlist", response_model=NegativeBasisWatchItem)
async def upsert_negative_basis_watch_item(
    item: NegativeBasisWatchItem,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> NegativeBasisWatchItem:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _monitor(request).repo.upsert_watch_item(item)


@router.delete("/watchlist/{item_id}")
async def delete_negative_basis_watch_item(
    item_id: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> dict[str, bool]:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    await _monitor(request).repo.delete_watch_item(item_id)
    return {"ok": True}


@router.post("/watchlist/{item_id}/collect", response_model=NegativeBasisAnalysisResult)
async def collect_negative_basis_watch_item(
    item_id: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> NegativeBasisAnalysisResult:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    monitor = _monitor(request)
    item = await monitor.repo.get_watch_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="监控标的不存在")
    try:
        return await monitor.collect_watch_item(item)
    except PairSpreadQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/status", response_model=NegativeBasisMonitorStatus)
async def get_negative_basis_monitor_status(request: Request) -> NegativeBasisMonitorStatus:
    return await _monitor(request).status()


@router.post("/auto-scan", response_model=list[NegativeBasisAutoCandidate])
async def refresh_negative_basis_auto_scan(
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> list[NegativeBasisAutoCandidate]:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _monitor(request).discover_auto_candidates(force=True)


@router.get("/auto-scan/settings", response_model=NegativeBasisAutoScanSettings)
async def get_negative_basis_auto_scan_settings(
    request: Request,
) -> NegativeBasisAutoScanSettings:
    return await _monitor(request).repo.get_auto_scan_settings()


@router.put("/auto-scan/settings", response_model=NegativeBasisAutoScanSettings)
async def update_negative_basis_auto_scan_settings(
    settings: NegativeBasisAutoScanSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> NegativeBasisAutoScanSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _monitor(request).update_auto_scan_settings(settings)


@router.post("/auto-scan/block-symbol", response_model=NegativeBasisAutoScanSettings)
async def block_negative_basis_auto_symbol(
    request: Request,
    symbol: str = Query(...),
    password: str | None = Depends(dashboard_password_header),
) -> NegativeBasisAutoScanSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    try:
        return await _monitor(request).block_auto_symbol(symbol)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/auto-scan/block-symbol", response_model=NegativeBasisAutoScanSettings)
async def unblock_negative_basis_auto_symbol(
    request: Request,
    symbol: str = Query(...),
    password: str | None = Depends(dashboard_password_header),
) -> NegativeBasisAutoScanSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _monitor(request).unblock_auto_symbol(symbol)


@router.post("/auto-scan/block-exchange", response_model=NegativeBasisAutoScanSettings)
async def block_negative_basis_auto_exchange(
    request: Request,
    exchange: str = Query(...),
    password: str | None = Depends(dashboard_password_header),
) -> NegativeBasisAutoScanSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    try:
        return await _monitor(request).block_auto_exchange(exchange)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/auto-scan/block-exchange", response_model=NegativeBasisAutoScanSettings)
async def unblock_negative_basis_auto_exchange(
    request: Request,
    exchange: str = Query(...),
    password: str | None = Depends(dashboard_password_header),
) -> NegativeBasisAutoScanSettings:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _monitor(request).unblock_auto_exchange(exchange)


@router.get("/samples", response_model=list[NegativeBasisSignalSample])
async def list_negative_basis_samples(
    request: Request,
    watch_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    minutes: int = Query(default=60 * 24, ge=1, le=60 * 24 * 90),
    limit: int = Query(default=1000, ge=1, le=20_000),
) -> list[NegativeBasisSignalSample]:
    normalized_symbol = normalize_pair_spread_symbol(symbol) if symbol else None
    return await _monitor(request).repo.list_samples(
        watch_id=watch_id,
        symbol=normalized_symbol,
        start_at=datetime.now(UTC) - timedelta(minutes=minutes),
        limit=limit,
    )


@router.get("/events", response_model=list[NegativeBasisAlertEvent])
async def list_negative_basis_events(
    request: Request,
    watch_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    minutes: int = Query(default=60 * 24 * 7, ge=1, le=60 * 24 * 90),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[NegativeBasisAlertEvent]:
    normalized_symbol = normalize_pair_spread_symbol(symbol) if symbol else None
    return await _monitor(request).repo.list_events(
        watch_id=watch_id,
        symbol=normalized_symbol,
        start_at=datetime.now(UTC) - timedelta(minutes=minutes),
        limit=limit,
    )


@router.get("/query", response_model=NegativeBasisAnalysisResult)
async def query_negative_basis(
    request: Request,
    symbol: str = Query(default="PROM"),
    spot_exchange: str = Query(default="binance"),
    future_exchange: str = Query(default="gate"),
    spot_symbol: str | None = Query(default=None),
    future_symbol: str | None = Query(default=None),
    future_multiplier: float = Query(default=1.0, gt=0),
    hours: int = Query(default=4, ge=1, le=720),
    watch_threshold_pct: float = Query(default=0.5, ge=0),
    building_threshold_pct: float = Query(default=1.0, ge=0),
    confirmed_threshold_pct: float = Query(default=2.0, ge=0),
    strong_threshold_pct: float = Query(default=3.0, ge=0),
    extreme_threshold_pct: float = Query(default=10.0, ge=0),
    watch_consecutive_hits: int = Query(default=3, ge=1, le=60),
    building_consecutive_hits: int = Query(default=3, ge=1, le=60),
    confirmed_consecutive_hits: int = Query(default=3, ge=1, le=60),
    strong_consecutive_hits: int = Query(default=2, ge=1, le=60),
    extreme_consecutive_hits: int = Query(default=1, ge=1, le=60),
    spot_volume_growth_threshold: float = Query(default=3.0, ge=0),
    oi_confirmed_growth_pct: float = Query(default=20.0, ge=0),
    oi_strong_growth_pct: float = Query(default=30.0, ge=0),
    min_spot_hourly_volume_usdt: float = Query(default=0.0, ge=0),
) -> NegativeBasisAnalysisResult:
    try:
        item = NegativeBasisWatchItem(
            id="ad-hoc",
            enabled=False,
            symbol=symbol,
            spot_exchange=spot_exchange,
            future_exchange=future_exchange,
            spot_symbol=spot_symbol,
            future_symbol=future_symbol,
            future_multiplier=future_multiplier,
            lookback_hours=hours,
            watch_threshold_pct=watch_threshold_pct,
            building_threshold_pct=building_threshold_pct,
            confirmed_threshold_pct=confirmed_threshold_pct,
            strong_threshold_pct=strong_threshold_pct,
            extreme_threshold_pct=extreme_threshold_pct,
            watch_consecutive_hits=watch_consecutive_hits,
            building_consecutive_hits=building_consecutive_hits,
            confirmed_consecutive_hits=confirmed_consecutive_hits,
            strong_consecutive_hits=strong_consecutive_hits,
            extreme_consecutive_hits=extreme_consecutive_hits,
            spot_volume_growth_threshold=spot_volume_growth_threshold,
            oi_confirmed_growth_pct=oi_confirmed_growth_pct,
            oi_strong_growth_pct=oi_strong_growth_pct,
            min_spot_hourly_volume_usdt=min_spot_hourly_volume_usdt,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return await _monitor(request).analyze_item(item)
    except PairSpreadQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
