from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.db.repositories import OilNewsRepository
from app.models.oil_news import (
    OilNewsDirection,
    OilNewsItem,
    OilNewsRefreshResult,
    OilNewsSeverity,
)

router = APIRouter(prefix="/oil-news")


def _repo(request: Request) -> OilNewsRepository:
    repo = getattr(request.app.state, "oil_news_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Oil news repository is not ready")
    return repo


@router.get("", response_model=list[OilNewsItem])
async def list_oil_news(
    request: Request,
    severity: OilNewsSeverity | None = None,
    direction: OilNewsDirection | None = None,
    limit: int = 100,
) -> list[OilNewsItem]:
    return await _repo(request).list(
        severity=severity,
        direction=direction,
        limit=min(max(limit, 1), 500),
    )


@router.post("/refresh", response_model=OilNewsRefreshResult)
async def refresh_oil_news(
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> OilNewsRefreshResult:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    monitor = getattr(request.app.state, "oil_news_monitor", None)
    if monitor is None:
        raise HTTPException(status_code=503, detail="Oil news monitor is not ready")
    settings = await request.app.state.settings_repo.get_oil_news_settings()
    return await monitor.poll(settings)
