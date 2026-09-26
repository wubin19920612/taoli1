from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app.core.security import verify_dashboard_password
from app.models.account_connection import (
    AccountConnectionOverview,
    AccountConnectionTestResult,
    AccountConnectionUpdate,
    AccountConnectionView,
    AccountConnectionWrite,
)
from app.services.account_connections import (
    AccountConnectionConfigurationError,
    AccountConnectionNotFoundError,
    AccountConnectionService,
)

router = APIRouter(prefix="/account-connections")


def _service(
    request: Request,
    provided_password: str | None,
) -> AccountConnectionService:
    service = getattr(request.app.state, "account_connection_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="账户连接服务尚未就绪")
    expected_password = request.app.state.settings.dashboard_password
    if not expected_password:
        raise HTTPException(
            status_code=503,
            detail="必须先配置非空的 DASHBOARD_PASSWORD 才能管理账户凭据",
        )
    verify_dashboard_password(expected_password, provided_password)
    return service


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AccountConnectionNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("", response_model=AccountConnectionOverview)
async def list_account_connections(
    request: Request,
    x_dashboard_password: str | None = Header(default=None),
) -> AccountConnectionOverview:
    return await _service(request, x_dashboard_password).overview()


@router.post("", response_model=AccountConnectionView, status_code=status.HTTP_201_CREATED)
async def create_account_connection(
    payload: AccountConnectionWrite,
    request: Request,
    x_dashboard_password: str | None = Header(default=None),
) -> AccountConnectionView:
    try:
        return await _service(request, x_dashboard_password).create(payload)
    except (AccountConnectionConfigurationError, AccountConnectionNotFoundError) as exc:
        raise _http_error(exc) from exc


@router.post("/test", response_model=AccountConnectionTestResult)
async def test_draft_account_connection(
    payload: AccountConnectionWrite,
    request: Request,
    x_dashboard_password: str | None = Header(default=None),
) -> AccountConnectionTestResult:
    try:
        return await _service(request, x_dashboard_password).test_draft(payload)
    except (AccountConnectionConfigurationError, AccountConnectionNotFoundError) as exc:
        raise _http_error(exc) from exc


@router.put("/{connection_id}", response_model=AccountConnectionView)
async def update_account_connection(
    connection_id: str,
    payload: AccountConnectionUpdate,
    request: Request,
    x_dashboard_password: str | None = Header(default=None),
) -> AccountConnectionView:
    try:
        return await _service(request, x_dashboard_password).update(connection_id, payload)
    except (AccountConnectionConfigurationError, AccountConnectionNotFoundError) as exc:
        raise _http_error(exc) from exc


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account_connection(
    connection_id: str,
    request: Request,
    x_dashboard_password: str | None = Header(default=None),
) -> Response:
    try:
        await _service(request, x_dashboard_password).delete(connection_id)
    except (AccountConnectionConfigurationError, AccountConnectionNotFoundError) as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{connection_id}/test", response_model=AccountConnectionTestResult)
async def test_saved_account_connection(
    connection_id: str,
    request: Request,
    x_dashboard_password: str | None = Header(default=None),
) -> AccountConnectionTestResult:
    try:
        return await _service(request, x_dashboard_password).test_saved(connection_id)
    except (AccountConnectionConfigurationError, AccountConnectionNotFoundError) as exc:
        raise _http_error(exc) from exc
