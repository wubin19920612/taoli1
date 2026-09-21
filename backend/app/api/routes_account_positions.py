from fastapi import APIRouter, HTTPException, Request

from app.models.account_position import AccountPositionSnapshot
from app.services.account_positions import AccountPositionService


router = APIRouter(prefix="/account-positions")


def _service(request: Request) -> AccountPositionService:
    service = getattr(request.app.state, "account_position_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Account position service is not ready")
    return service


@router.get("", response_model=AccountPositionSnapshot)
async def get_account_positions(request: Request) -> AccountPositionSnapshot:
    return await _service(request).snapshot()
