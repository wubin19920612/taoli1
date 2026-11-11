"""Read-only, fixed-path gateway to the crossex-exec data contract."""
import asyncio
import json
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/live-exec")
UPSTREAM_PATHS = {
    "summary": "/api/v1/summary",
    "positions": "/api/v1/positions",
    "trades": "/api/v1/trades",
    "stats": "/api/v1/stats",
}


def validate_query(request: Request, allowed: set[str]) -> None:
    keys = list(request.query_params.keys())
    if any(key not in allowed or len(request.query_params.getlist(key)) != 1 for key in keys):
        raise HTTPException(status_code=422, detail="invalid_query")


def reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


async def proxy(request: Request, resource: str, params: dict | None = None) -> dict:
    settings = request.app.state.settings
    base_url = settings.crossex_exec_url
    token = settings.crossex_exec_api_key
    if not base_url or not token:
        return {"available": False, "error": "not_configured"}

    # Configuration is trusted, but never put credentials or query strings into a URL/log.
    try:
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            return {"available": False, "error": "invalid_configuration"}
        async with asyncio.timeout(3.0):
            async with httpx.AsyncClient(timeout=3.0, trust_env=False, follow_redirects=False) as client:
                response = await client.get(
                    base_url.rstrip("/") + UPSTREAM_PATHS[resource],
                    headers={"X-Api-Token": token}, params=params,
                )
        if response.status_code != 200:
            return {"available": False, "error": f"upstream_http_{response.status_code}"}
        data = json.loads(response.content, parse_constant=reject_json_constant)
        return {"available": True, "data": data}
    except (httpx.TimeoutException, TimeoutError):
        reason = "timeout"
    except (httpx.RequestError, httpx.InvalidURL):
        reason = "unreachable"
    except (ValueError, UnicodeError):
        reason = "invalid_json"
    # Never log exception text, headers, or an upstream error body: they can contain the token.
    return {"available": False, "error": reason}


@router.get("/summary")
async def get_summary(request: Request) -> dict:
    validate_query(request, set())
    return await proxy(request, "summary")


@router.get("/positions")
async def get_positions(request: Request) -> dict:
    validate_query(request, set())
    return await proxy(request, "positions")


@router.get("/trades")
async def get_trades(
    request: Request,
    limit: int = Query(200, ge=1, le=500),
    before: str | None = Query(None, min_length=1, max_length=64),
) -> dict:
    validate_query(request, {"limit", "before"})
    params: dict[str, str | int] = {"limit": limit}
    if before is not None:
        try:
            timestamp = datetime.fromisoformat(before.replace("Z", "+00:00"))
            if "T" not in before or timestamp.utcoffset() != timedelta(0):
                raise ValueError("UTC ISO8601 required")
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid_before") from None
        params["before"] = before
    return await proxy(request, "trades", params)


@router.get("/stats")
async def get_stats(request: Request, days: int = Query(7, ge=1, le=90)) -> dict:
    validate_query(request, {"days"})
    return await proxy(request, "stats", {"days": days})
