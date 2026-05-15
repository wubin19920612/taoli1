import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


async def _event_stream(request: Request):
    last_payload = ""
    while True:
        if await request.is_disconnected():
            break
        store = request.app.state.snapshot_store
        payload = json.dumps(
            {
                "opportunities": [
                    item.model_dump(mode="json") for item in store.get_opportunities()[:200]
                ],
                "markets_count": len(store.get_markets()),
                "exchange_errors": store.get_exchange_errors(),
            },
            separators=(",", ":"),
        )
        if payload != last_payload:
            last_payload = payload
            yield f"event: snapshot\ndata: {payload}\n\n"
        await asyncio.sleep(2)


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    return StreamingResponse(_event_stream(request), media_type="text/event-stream")
