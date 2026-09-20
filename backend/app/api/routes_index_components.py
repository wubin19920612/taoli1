from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.db.repositories import IndexComponentRepository
from app.models.index_component import (
    IndexComponentAutoWatchSettings,
    IndexComponentAutoWatchStatus,
    IndexComponentChange,
    IndexComponentSnapshot,
    IndexComponentWatchItem,
)
from app.services.index_components import IndexComponentAutoWatchService

router = APIRouter(prefix="/index-components")


def _auto_watch(request: Request) -> IndexComponentAutoWatchService:
    service = getattr(request.app.state, "index_component_auto_watch", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Index component auto watch is not ready")
    return service


@router.get("/auto-watch", response_model=IndexComponentAutoWatchStatus)
async def get_index_component_auto_watch(
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> IndexComponentAutoWatchStatus:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _auto_watch(request).status()


@router.put("/auto-watch", response_model=IndexComponentAutoWatchStatus)
async def update_index_component_auto_watch(
    settings: IndexComponentAutoWatchSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> IndexComponentAutoWatchStatus:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _auto_watch(request).configure(settings)


@router.post("/auto-watch/sync", response_model=IndexComponentAutoWatchStatus)
async def sync_index_component_auto_watch(
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> IndexComponentAutoWatchStatus:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _auto_watch(request).sync(force=True)


def _repo(request: Request) -> IndexComponentRepository:
    repo = getattr(request.app.state, "index_component_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Index component repository is not ready")
    return repo


def _normalize_symbol(value: str | None) -> str | None:
    return value.strip().upper() if value and value.strip() else None


def _normalize_exchange(value: str | None) -> str | None:
    return value.strip().lower() if value and value.strip() else None


@router.get("/changes", response_model=list[IndexComponentChange])
async def list_index_component_changes(
    request: Request,
    symbol: str | None = None,
    exchange: str | None = None,
    limit: int = 100,
) -> list[IndexComponentChange]:
    bounded_limit = min(max(limit, 1), 500)
    return await _repo(request).list_changes(
        symbol=_normalize_symbol(symbol),
        exchange=_normalize_exchange(exchange),
        limit=bounded_limit,
    )


@router.get("/snapshots", response_model=list[IndexComponentSnapshot])
async def list_index_component_snapshots(
    request: Request,
    symbol: str | None = None,
    exchange: str | None = None,
    limit: int = 500,
) -> list[IndexComponentSnapshot]:
    bounded_limit = min(max(limit, 1), 1000)
    return await _repo(request).list_snapshots(
        symbol=_normalize_symbol(symbol),
        exchange=_normalize_exchange(exchange),
        limit=bounded_limit,
    )


@router.get("/watchlist", response_model=list[IndexComponentWatchItem])
async def list_index_component_watchlist(request: Request) -> list[IndexComponentWatchItem]:
    return await _repo(request).list_watch_items()


@router.post("/watchlist", response_model=IndexComponentWatchItem)
async def create_index_component_watch_item(
    request: Request,
    item: IndexComponentWatchItem,
) -> IndexComponentWatchItem:
    return await _repo(request).create_watch_item(item)


@router.delete("/watchlist/{item_id}", status_code=204)
async def delete_index_component_watch_item(request: Request, item_id: str) -> None:
    await _repo(request).delete_watch_item(item_id)
