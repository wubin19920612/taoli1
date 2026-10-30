import math

from fastapi import APIRouter, Request

router = APIRouter(prefix="/arb-mvp")


def target_credentials(request: Request, target: str) -> tuple[str, str]:
    settings = request.app.state.settings
    if target == "arb_paper":
        return settings.arb_paper_url, settings.arb_paper_api_key
    if target == "arb_live":
        return settings.arb_live_url, settings.arb_live_api_key
    return "", ""


async def default_max_position(request: Request) -> float:
    repo = getattr(request.app.state, "settings_repo", None)
    try:
        value = await repo.get_arb_mvp_default_max_position_usd()
        return value if math.isfinite(value) and value > 0 else 100.0
    except Exception:  # A missing/unreadable setting uses the specified initial default.
        return 100.0


@router.get("/status")
async def get_arb_mvp_status(request: Request) -> dict:
    return {"paper": {"configured": all(target_credentials(request, "arb_paper"))},
            "live": {"configured": all(target_credentials(request, "arb_live"))},
            "default_max_position_usd": await default_max_position(request)}
