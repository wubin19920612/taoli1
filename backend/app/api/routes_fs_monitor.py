from fastapi import APIRouter, Header, HTTPException, Query, Request

from app.core.security import verify_dashboard_password
from app.models.fs_monitor import FsReport, FsSettings
from app.services.fs_bybit import FsDataError

router = APIRouter(prefix="/fs-monitor", tags=["fs-monitor"])


def service(request, password):
    expected = request.app.state.settings.dashboard_password
    if not expected:
        raise HTTPException(status_code=503, detail="请先配置非空仪表盘密码")
    verify_dashboard_password(expected, password)
    monitor = getattr(request.app.state, "fs_monitor", None)
    if monitor is None:
        raise HTTPException(status_code=503, detail="FS 观察服务尚未就绪")
    return monitor


@router.get("/report", response_model=FsReport)
async def report(request: Request, x_dashboard_password: str | None = Header(default=None)):
    return await service(request, x_dashboard_password).current()


@router.get("/settings", response_model=FsSettings)
async def settings(request: Request, x_dashboard_password: str | None = Header(default=None)):
    return await service(request, x_dashboard_password).repository.settings()


@router.put("/settings", response_model=FsSettings)
async def update(
    settings: FsSettings, request: Request, x_dashboard_password: str | None = Header(default=None)
):
    try:
        return await service(request, x_dashboard_password).update_settings(settings)
    except FsDataError as exc:
        raise HTTPException(
            status_code=422, detail="所选账户不可用，请检查账户连接与现货/永续读取开关"
        ) from exc


@router.post("/refresh", status_code=202)
async def refresh(request: Request, x_dashboard_password: str | None = Header(default=None)):
    monitor = service(request, x_dashboard_password)
    monitor.wake.set()
    return {"queued": True}


@router.get("/history", response_model=list[FsReport])
async def history(
    request: Request,
    limit: int = Query(default=24, ge=1, le=48),
    x_dashboard_password: str | None = Header(default=None),
):
    monitor = service(request, x_dashboard_password)
    rows = await monitor.repository.history(limit)
    for row in rows:
        row.stale = True
        for candidate in row.candidates:
            candidate.stage = "stale"
    return rows


@router.get("/events")
async def events(request: Request, x_dashboard_password: str | None = Header(default=None)):
    return await service(request, x_dashboard_password).repository.events()
