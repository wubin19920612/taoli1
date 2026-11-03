from fastapi import APIRouter, HTTPException, Request, Response, status

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
    return service


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AccountConnectionNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("", response_model=AccountConnectionOverview)
async def list_account_connections(
    request: Request,
) -> AccountConnectionOverview:
    return await _service(request).overview()


@router.post("", response_model=AccountConnectionView, status_code=status.HTTP_201_CREATED)
async def create_account_connection(
    payload: AccountConnectionWrite,
    request: Request,
) -> AccountConnectionView:
    try:
        return await _service(request).create(payload)
    except (AccountConnectionConfigurationError, AccountConnectionNotFoundError) as exc:
        raise _http_error(exc) from exc


@router.post("/test", response_model=AccountConnectionTestResult)
async def test_draft_account_connection(
    payload: AccountConnectionWrite,
    request: Request,
) -> AccountConnectionTestResult:
    try:
        return await _service(request).test_draft(payload)
    except (AccountConnectionConfigurationError, AccountConnectionNotFoundError) as exc:
        raise _http_error(exc) from exc


@router.put("/{connection_id}", response_model=AccountConnectionView)
async def update_account_connection(
    connection_id: str,
    payload: AccountConnectionUpdate,
    request: Request,
) -> AccountConnectionView:
    try:
        return await _service(request).update(connection_id, payload)
    except (AccountConnectionConfigurationError, AccountConnectionNotFoundError) as exc:
        raise _http_error(exc) from exc


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account_connection(
    connection_id: str,
    request: Request,
) -> Response:
    try:
        await _service(request).delete(connection_id)
    except (AccountConnectionConfigurationError, AccountConnectionNotFoundError) as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{connection_id}/test", response_model=AccountConnectionTestResult)
async def test_saved_account_connection(
    connection_id: str,
    request: Request,
) -> AccountConnectionTestResult:
    try:
        return await _service(request).test_saved(connection_id)
    except (AccountConnectionConfigurationError, AccountConnectionNotFoundError) as exc:
        raise _http_error(exc) from exc
