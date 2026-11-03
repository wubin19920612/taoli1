from fastapi import APIRouter, HTTPException, Request

from app.models.service_control import ServiceControlStatus, ServiceRestartResult
from app.services.service_control import ALLOWED_SERVICES, ServiceControlError

router = APIRouter(prefix="/admin")


def _service_controller(request: Request):
    controller = getattr(request.app.state, "service_controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Service control is not ready")
    return controller


def _require_service_control_access(request: Request) -> None:
    expected_password = request.app.state.settings.dashboard_password
    if not expected_password:
        raise HTTPException(
            status_code=403,
            detail="Service control requires DASHBOARD_PASSWORD to be set",
        )


@router.get("/service-control", response_model=ServiceControlStatus)
async def get_service_control_status(
    request: Request,
) -> ServiceControlStatus:
    _require_service_control_access(request)
    return await _service_controller(request).get_status()


@router.post("/service-control/{service}/restart", response_model=ServiceRestartResult)
async def restart_service(
    service: str,
    request: Request,
) -> ServiceRestartResult:
    normalized = service.lower()
    if normalized not in ALLOWED_SERVICES:
        raise HTTPException(status_code=404, detail=f"Unsupported service: {service}")
    _require_service_control_access(request)
    try:
        return await _service_controller(request).restart(normalized)
    except ServiceControlError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
