from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.service_control import ServiceControlStatus, ServiceRestartResult
from app.services.service_control import ALLOWED_SERVICES, ServiceControlError

router = APIRouter(prefix="/admin")


def _service_controller(request: Request):
    controller = getattr(request.app.state, "service_controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Service control is not ready")
    return controller


def _require_service_control_access(request: Request, password: str | None) -> None:
    expected_password = request.app.state.settings.dashboard_password
    if not expected_password:
        raise HTTPException(
            status_code=403,
            detail="Service control requires DASHBOARD_PASSWORD to be set",
        )
    verify_dashboard_password(expected_password, password)


@router.get("/service-control", response_model=ServiceControlStatus)
async def get_service_control_status(
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> ServiceControlStatus:
    _require_service_control_access(request, password)
    return await _service_controller(request).get_status()


@router.post("/service-control/{service}/restart", response_model=ServiceRestartResult)
async def restart_service(
    service: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> ServiceRestartResult:
    normalized = service.lower()
    if normalized not in ALLOWED_SERVICES:
        raise HTTPException(status_code=404, detail=f"Unsupported service: {service}")
    _require_service_control_access(request, password)
    try:
        return await _service_controller(request).restart(normalized)
    except ServiceControlError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
