import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.shadow_stats import DIMENSIONS, buckets

router = APIRouter(prefix="/shadow", tags=["shadow"])


def runtime(request):
    return request.app.state.shadow_runtime


def repository(request):
    repo = runtime(request).repo
    if repo is None:
        raise HTTPException(503, "影子账本数据库尚未就绪")
    return repo


@router.get("/status")
async def status(request: Request):
    return runtime(request).status()


@router.get("/trades")
async def trades(request: Request, limit: int = Query(100, ge=1, le=500),
                 status: Literal["open", "closed"] | None = None):
    if not runtime(request).ledger.enabled:
        return []
    return await repository(request).list_trades(limit, status)


@router.get("/buckets")
async def get_buckets(request: Request, dims: str = "variant,pair", since: datetime | None = None,
                      until: datetime | None = None, include_fake: bool = False):
    dimensions = [s.strip() for s in dims.split(",") if s.strip()]
    if not set(dimensions) <= DIMENSIONS or len(dimensions) != len(set(dimensions)):
        raise HTTPException(400, "无效或重复的统计维度")
    now = datetime.now(UTC)
    since, until = since or now - timedelta(days=30), until or now
    if since.tzinfo is None or until.tzinfo is None or since > until:
        raise HTTPException(400, "时间范围必须包含时区且起点不晚于终点")
    if not runtime(request).ledger.enabled:
        return []
    rows = await repository(request).closed_events(since.astimezone(UTC), until.astimezone(UTC), include_fake)
    return await asyncio.to_thread(buckets, rows, dimensions, include_fake)
