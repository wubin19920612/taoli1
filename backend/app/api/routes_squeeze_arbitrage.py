from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

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


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    status = await _repo(request).status(
        enabled=bool(getattr(getattr(request.app.state, "squeeze_monitor", None), "running", False)),
        now=datetime.now(UTC),
    )
    status["routes"] = await _route_repo(request).status(
        enabled=bool(getattr(getattr(request.app.state, "squeeze_route_monitor", None), "running", False))
    )
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
