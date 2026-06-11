from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.gate_twap import (
    GateTwapJobStatus,
    GateTwapMarketSnapshot,
    GateTwapPlan,
    GateTwapRequest,
    GateTwapRunRequest,
)
from app.services.gate_twap import (
    GateTwapError,
    GateTwapJobManager,
    fetch_market_snapshot,
    normalize_contract,
)

router = APIRouter(prefix="/gate-twap")


def _manager(request: Request) -> GateTwapJobManager:
    manager = getattr(request.app.state, "gate_twap_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Gate TWAP manager is not ready")
    return manager


def _require_dashboard_password(request: Request, password: str | None) -> None:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)


@router.get("/market", response_model=GateTwapMarketSnapshot)
async def get_gate_twap_market(
    request: Request,
    contract: str = "SKHYNIX_USDT",
    settle: str = "usdt",
) -> GateTwapMarketSnapshot:
    manager = _manager(request)
    try:
        return await fetch_market_snapshot(
            manager.client,
            contract=normalize_contract(contract),
            settle=settle.lower(),
        )
    except GateTwapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/preview", response_model=GateTwapPlan)
async def preview_gate_twap(
    payload: GateTwapRequest,
    request: Request,
) -> GateTwapPlan:
    manager = _manager(request)
    try:
        return await manager.preview(payload)
    except GateTwapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/jobs", response_model=list[GateTwapJobStatus])
async def list_gate_twap_jobs(request: Request) -> list[GateTwapJobStatus]:
    return _manager(request).list_jobs()


@router.get("/jobs/{job_id}", response_model=GateTwapJobStatus)
async def get_gate_twap_job(job_id: str, request: Request) -> GateTwapJobStatus:
    job = _manager(request).get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Gate TWAP job not found")
    return job


@router.post("/jobs", response_model=GateTwapJobStatus)
async def start_gate_twap_job(
    payload: GateTwapRunRequest,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> GateTwapJobStatus:
    _require_dashboard_password(request, password)
    manager = _manager(request)
    try:
        return await manager.start(payload)
    except GateTwapError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/jobs/{job_id}", response_model=GateTwapJobStatus)
async def cancel_gate_twap_job(
    job_id: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> GateTwapJobStatus:
    _require_dashboard_password(request, password)
    manager = _manager(request)
    try:
        return await manager.cancel(job_id)
    except GateTwapError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
