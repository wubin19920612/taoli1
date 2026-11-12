"""Offline app factory for the real Uvicorn CLI audit regression."""
import json

import httpx
from auth_helpers import TEST_TOTP_SECRET, IsolatedSessionLookup
from fastapi import Depends, FastAPI

from app.api import routes_live_exec
from app.core.config import Settings
from app.core.security import require_authentication


def create_app():
    app = FastAPI()
    app.state.settings = Settings(environment="production", database_url="sqlite:///:memory:",
        dashboard_password="test-password", dashboard_totp_secret=TEST_TOTP_SECRET,
        crossex_exec_url="http://exec.test:8795", crossex_exec_api_key="synthetic-read-token",
        crossex_exec_admin_token="synthetic-admin-token")
    app.state.auth_store = IsolatedSessionLookup()
    app.include_router(routes_live_exec.router, prefix="/api", dependencies=[Depends(require_authentication)])

    @app.get("/ready")
    async def ready():
        return {"ok": True}

    real_client = httpx.AsyncClient

    def upstream(request):
        if json.loads(request.content)["order_usd_max"] == 31:
            return httpx.Response(403, json={"error": "admin_required", "detail": "synthetic-admin-token"})
        return httpx.Response(200, json={"version": 3})

    routes_live_exec.httpx.AsyncClient = lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(upstream))
    return app
