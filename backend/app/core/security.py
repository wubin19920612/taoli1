import hmac

from fastapi import Header, HTTPException, Request


# Exact method/path pairs only: health probes and pre-password menu initialization.
# /features exposes disabled feature names, not account data or credentials.
# Keep /health/detail and all business routes (including /stream) protected.
PUBLIC_API_ROUTES = frozenset({
    ("GET", "/api/health"),
    ("GET", "/api/health/live"),
    ("GET", "/api/health/ready"),
    ("GET", "/api/features"),
})


def verify_dashboard_password(expected_password: str, provided_password: str | None) -> None:
    if not expected_password:
        return
    if provided_password is None or not hmac.compare_digest(
        provided_password.encode("utf-8"), expected_password.encode("utf-8"),
    ):
        raise HTTPException(status_code=401, detail="Invalid dashboard password")


async def require_dashboard_password(request: Request) -> None:
    if (request.method, request.url.path) in PUBLIC_API_ROUTES:
        return
    verify_dashboard_password(
        request.app.state.settings.dashboard_password,
        request.headers.get("X-Dashboard-Password"),
    )


async def dashboard_password_header(x_dashboard_password: str | None = Header(default=None)) -> str | None:
    return x_dashboard_password
