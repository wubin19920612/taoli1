from fastapi import APIRouter, HTTPException, Request

from app.db.repositories import AnnouncementRepository
from app.models.announcement import AnnouncementKind, ExchangeAnnouncement
from app.services.announcements import ANNOUNCEMENT_EXCHANGE_OPTIONS

router = APIRouter(prefix="/announcements")


def _repo(request: Request) -> AnnouncementRepository:
    repo = getattr(request.app.state, "announcement_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Announcement repository is not ready")
    return repo


def _normalize_exchange(value: str | None) -> str | None:
    return value.strip().lower() if value and value.strip() else None


@router.get("", response_model=list[ExchangeAnnouncement])
async def list_announcements(
    request: Request,
    exchange: str | None = None,
    kind: AnnouncementKind | None = None,
    limit: int = 100,
) -> list[ExchangeAnnouncement]:
    bounded_limit = min(max(limit, 1), 500)
    normalized_exchange = _normalize_exchange(exchange)
    return await _repo(request).list(
        exchange=normalized_exchange,
        kind=kind,
        limit=bounded_limit,
        demote_baseline=normalized_exchange is None,
    )


@router.get("/exchanges")
async def list_announcement_exchanges() -> list[dict[str, str]]:
    return ANNOUNCEMENT_EXCHANGE_OPTIONS
