from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/features")
async def features(request: Request) -> dict[str, list[str]]:
    return {"disabled": sorted(request.app.state.settings.disabled_features)}
