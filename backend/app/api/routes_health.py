from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    store = request.app.state.snapshot_store
    return {
        "status": "ok",
        "markets": len(store.get_markets()),
        "opportunities": len(store.get_opportunities()),
        "exchange_errors": store.get_exchange_errors(),
    }
