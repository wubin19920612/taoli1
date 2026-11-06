import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import aiosqlite
from fastapi import APIRouter, HTTPException, Query, Request

from app.db.repositories import ShadowTradeRepository
from app.services.shadow_stats import DIMENSIONS, buckets
from app.services.shadow_ledger import STRATEGY_VERSION

router = APIRouter(prefix="/shadow", tags=["shadow"])


def runtime(request):
    return request.app.state.shadow_runtime


def repository(request):
    if not runtime(request).status()["ready"]:
        raise HTTPException(503, "影子账本数据库尚未就绪")
    return True


async def read_committed(request, method, *args):
    repository(request)  # Keep the existing not-ready response.
    uri = Path(runtime(request).settings.sqlite_path).resolve().as_uri() + "?mode=ro"

    async def read():
        # Each request owns its reader and closes its SQLite worker before the
        # temporary event loop exits. Never borrow the ledger's writer connection.
        async with aiosqlite.connect(uri, uri=True) as db:
            db.row_factory = aiosqlite.Row
            return await getattr(ShadowTradeRepository(db), method)(*args)

    try:
        return await asyncio.to_thread(lambda: asyncio.run(read()))
    except Exception:
        runtime(request).error("read_" + method)
        raise HTTPException(503, "影子账本读取暂不可用") from None


@router.get("/status")
async def status(request: Request):
    return runtime(request).status()


@router.get("/trades")
async def trades(request: Request, limit: int = Query(100, ge=1, le=500),
                 status: Literal["open", "closed"] | None = None):
    if not runtime(request).settings.shadow_enabled:
        return []
    return await read_committed(request, "list_trades", limit, status)


@router.get("/buckets")
async def get_buckets(request: Request, dims: str = "variant,pair", since: datetime | None = None,
                      until: datetime | None = None, include_fake: bool = False,
                      strategy_version: str = STRATEGY_VERSION):
    dimensions = [s.strip() for s in dims.split(",") if s.strip()]
    if not set(dimensions) <= DIMENSIONS or len(dimensions) != len(set(dimensions)):
        raise HTTPException(400, "无效或重复的统计维度")
    now = datetime.now(UTC)
    since, until = since or now - timedelta(days=30), until or now
    if since.tzinfo is None or until.tzinfo is None or since > until:
        raise HTTPException(400, "时间范围必须包含时区且起点不晚于终点")
    if not runtime(request).settings.shadow_enabled:
        return []
    rows = await read_committed(request, "closed_events", since.astimezone(UTC), until.astimezone(UTC), include_fake,
                                strategy_version)
    try:
        return await runtime(request).statistics(rows, dimensions, include_fake)
    except Exception:
        runtime(request).error("statistics")
        raise HTTPException(503, "影子账本统计暂不可用") from None
