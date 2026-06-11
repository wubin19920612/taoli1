from fastapi import APIRouter, Query, Request

from app.models.tradfi_perp_monitor import TradfiPerpMonitorPreview
from app.services.tradfi_perp_monitor import (
    TradfiPerpLiveFetcher,
    build_tradfi_perp_monitor_preview,
)

router = APIRouter(prefix="/tradfi-perp-monitor")


async def _live_markets(request: Request):
    fetcher: TradfiPerpLiveFetcher | None = getattr(
        request.app.state,
        "tradfi_perp_live_fetcher",
        None,
    )
    if fetcher is None:
        fetcher = TradfiPerpLiveFetcher()
        request.app.state.tradfi_perp_live_fetcher = fetcher
    return await fetcher.fetch_markets()


@router.get("/preview", response_model=TradfiPerpMonitorPreview)
async def get_tradfi_perp_monitor_preview(
    request: Request,
    live: bool = Query(default=False),
    min_volume_24h_k: float = Query(default=1000, ge=0),
    max_mark_index_deviation_pct: float = Query(default=2.0, ge=0),
    max_rows: int = Query(default=500, ge=1, le=2000),
) -> TradfiPerpMonitorPreview:
    tradfi_bases = None
    if live:
        markets, tradfi_bases = await _live_markets(request)
    else:
        markets = request.app.state.snapshot_store.get_markets()
    return build_tradfi_perp_monitor_preview(
        markets,
        tradfi_bases=tradfi_bases,
        min_volume_24h_usdt=min_volume_24h_k * 1000,
        max_mark_index_deviation_pct=max_mark_index_deviation_pct,
        max_rows=max_rows,
    )


@router.post("/refresh", response_model=TradfiPerpMonitorPreview)
async def refresh_tradfi_perp_monitor_preview(
    request: Request,
    min_volume_24h_k: float = Query(default=1000, ge=0),
    max_mark_index_deviation_pct: float = Query(default=2.0, ge=0),
    max_rows: int = Query(default=500, ge=1, le=2000),
) -> TradfiPerpMonitorPreview:
    markets, tradfi_bases = await _live_markets(request)
    return build_tradfi_perp_monitor_preview(
        markets,
        tradfi_bases=tradfi_bases,
        min_volume_24h_usdt=min_volume_24h_k * 1000,
        max_mark_index_deviation_pct=max_mark_index_deviation_pct,
        max_rows=max_rows,
    )
