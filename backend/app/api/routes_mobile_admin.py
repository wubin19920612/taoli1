from fastapi import APIRouter, Depends, Request

from app.core.security import require_authentication
from app.models.mobile import MobileCardControlSwitchUpdate


async def require_admin(request: Request):
    await require_authentication(request, allow_development_bypass=False, use_initial_session=True)


router = APIRouter(prefix="/settings/mobile-card-control", dependencies=[Depends(require_admin)])


@router.get("")
async def get_switch(request: Request):
    return await request.app.state.mobile_card_control.switch_status()


@router.put("")
async def set_switch(payload: MobileCardControlSwitchUpdate, request: Request):
    return await request.app.state.mobile_card_control.set_runtime_switch(payload.enabled, "web")
