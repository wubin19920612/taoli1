from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.squeeze_arbitrage.paper_report import build_paper_report
from app.services.squeeze_arbitrage.paper_repository import SqueezePaperRepository
from app.services.squeeze_arbitrage.repository import SqueezeRepository
from app.services.squeeze_arbitrage.route_repository import SqueezeRouteRepository

router = APIRouter(prefix="/squeeze-arbitrage", tags=["squeeze-arbitrage"])


def _repo(request: Request) -> SqueezeRepository:
    repo = getattr(request.app.state, "squeeze_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Squeeze research store is unavailable")
    return repo


def _route_repo(request: Request) -> SqueezeRouteRepository:
    repo = getattr(request.app.state, "squeeze_route_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Squeeze route store is unavailable")
    return repo


def _paper_repo(request: Request) -> SqueezePaperRepository:
    repo = getattr(request.app.state, "squeeze_paper_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Squeeze paper store is unavailable")
    return repo


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    status = await _repo(request).status(
        enabled=bool(getattr(getattr(request.app.state, "squeeze_monitor", None), "running", False)),
        now=datetime.now(UTC),
    )
    route_monitor = getattr(request.app.state, "squeeze_route_monitor", None)
    status["routes"] = (
        await route_monitor.status() if route_monitor is not None
        else await _route_repo(request).status(enabled=False)
    )
    paper_monitor = getattr(request.app.state, "squeeze_paper_monitor", None)
    status["paper"] = await paper_monitor.status() if paper_monitor else None
    return status


@router.get("/watchlist")
async def get_watchlist(
    request: Request,
    limit: int = Query(default=30, ge=1, le=30),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    return await _repo(request).list_events(
        now=datetime.now(UTC), active_only=True, limit=limit, offset=offset
    )


@router.get("/events")
async def get_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    return await _repo(request).list_events(
        now=datetime.now(UTC), limit=limit, offset=offset
    )


@router.get("/routes")
async def get_routes(
    request: Request,
    limit: int = Query(default=10, ge=1, le=10),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    return await _route_repo(request).list_routes(limit=limit, offset=offset)


@router.get("/route-events")
async def get_route_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_inputs: bool = False,
) -> list[dict[str, Any]]:
    return await _route_repo(request).list_events(
        limit=limit, offset=offset, include_inputs=include_inputs
    )


@router.get("/paper/status")
async def get_paper_status(request: Request) -> dict[str, Any]:
    monitor = getattr(request.app.state, "squeeze_paper_monitor", None)
    if monitor is None:
        raise HTTPException(status_code=503, detail="Squeeze paper monitor is unavailable")
    return await monitor.status()


@router.get("/paper/positions")
async def get_paper_positions(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    trades = await _paper_repo(request).list_trades(
        limit=limit, offset=offset, active_only=True
    )
    return [trade.model_dump(mode="json") for trade in trades]


@router.get("/paper/trades")
async def get_paper_trades(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    trades = await _paper_repo(request).list_trades(limit=limit, offset=offset)
    return [trade.model_dump(mode="json") for trade in trades]


@router.get("/paper/report")
async def get_paper_report(request: Request) -> dict[str, Any]:
    repo = _paper_repo(request)
    return build_paper_report(
        await repo.load_run(), await repo.list_report_trades(),
        await repo.load_accounts(), datetime.now(UTC),
    )
