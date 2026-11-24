import hmac
import ipaddress
import time

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from app.core.totp import decode_secret

COOKIE_NAME = "taoli1_session"
PUBLIC_API_ROUTES = frozenset({
    ("GET", "/api/health"), ("GET", "/api/health/live"),
    ("GET", "/api/health/ready"), ("GET", "/api/features"),
    ("POST", "/api/auth/login"),
})


def constant_time_equal(expected: str, provided: str) -> bool:
    return hmac.compare_digest(expected.encode("utf-8", errors="surrogatepass"),
                               provided.encode("utf-8", errors="surrogatepass"))


def auth_configured(settings) -> bool:
    if not settings.dashboard_password:
        return False
    try:
        decode_secret(settings.dashboard_totp_secret)
    except ValueError:
        return False
    return True


def development_bypass(settings) -> bool:
    return (settings.environment.strip().lower() in {"test", "local", "development"}
            and settings.dashboard_auth_disabled)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return "unknown"
    try:
        trusted = any(address in ipaddress.ip_network(item.strip(), strict=False)
                      for item in request.app.state.settings.trusted_proxy_ips.split(",") if item.strip())
    except ValueError:
        trusted = False
    if trusted:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return str(address)


def browser_label(request: Request) -> str:
    # Never persist or notify arbitrary header text (it can contain credentials).
    ua = request.headers.get("user-agent", "")[:2048]
    browser = next((label for marker, label in (
        ("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
        ("Safari/", "Safari"),) if marker in ua), "Unknown browser")
    platform = next((label for marker, label in (
        ("Android", "Android"), ("iPhone", "iOS"), ("iPad", "iOS"),
        ("Windows", "Windows"), ("Macintosh", "macOS"), ("Linux", "Linux"),
    ) if marker in ua), "Unknown platform")
    return f"{browser} / {platform}"


def _mobile_route(request: Request) -> bool:
    path = request.url.path
    if request.method == "GET":
        return path.startswith("/api/mobile/") or path == "/api/health" or path.startswith("/api/health/")
    return request.method == "POST" and path == "/api/auth/logout"


async def _read_session(request: Request):
    store = getattr(request.app.state, "auth_store", None)
    try:
        current = await store.authenticate(request.cookies.get(COOKIE_NAME), time.time()) if store else None
    except Exception:
        raise HTTPException(503, "Authentication unavailable") from None
    if current and current[1] != "full" and not _mobile_route(request):
        raise HTTPException(403, "手机只读会话无权访问")
    return current


class SessionScopeMiddleware:
    """Apply the same session guard to public docs, redirects and method mismatches."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            if COOKIE_NAME in request.cookies and not _mobile_route(request):
                try:
                    request.state.initial_session = await _read_session(request)
                except HTTPException as exc:
                    response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


async def require_dashboard_password(request: Request) -> None:
    """Retained import name for the central route matrix; no password headers."""
    if (request.method, request.url.path) not in PUBLIC_API_ROUTES:
        await require_authentication(request, use_initial_session=True)


async def require_authentication(request: Request, *, session_only=False, allow_development_bypass=True,
                                 use_initial_session=False) -> None:
    settings = request.app.state.settings
    if not session_only and allow_development_bypass and development_bypass(settings):
        return
    if not auth_configured(settings):
        raise HTTPException(401, "Authentication required")
    api_token = settings.dashboard_api_token
    if (not session_only and len(api_token) == 64
            and not constant_time_equal(api_token, settings.dashboard_password)
            and constant_time_equal(api_token, request.headers.get("x-api-token", ""))):
        return
    # SSE calls without this flag so each snapshot observes revocations.
    if use_initial_session and hasattr(request.state, "initial_session"):
        current = request.state.initial_session
    else:
        current = await _read_session(request)
    if not current:
        raise HTTPException(401, "Authentication required")
    request.state.auth_session_hash = current[0]


async def require_session(request: Request) -> None:
    await require_authentication(request, session_only=True, use_initial_session=True)
