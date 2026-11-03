import hmac
import ipaddress
import time

from fastapi import HTTPException, Request

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


async def require_dashboard_password(request: Request) -> None:
    """Retained import name for the central route matrix; no password headers."""
    if (request.method, request.url.path) in PUBLIC_API_ROUTES:
        return
    await require_authentication(request)


async def require_authentication(request: Request, *, session_only=False) -> None:
    settings = request.app.state.settings
    if not session_only and development_bypass(settings):
        return
    if not auth_configured(settings):
        raise HTTPException(401, "Authentication required")
    api_token = settings.dashboard_api_token
    if (not session_only and len(api_token) == 64
            and not constant_time_equal(api_token, settings.dashboard_password)
            and constant_time_equal(api_token, request.headers.get("x-api-token", ""))):
        return
    store = getattr(request.app.state, "auth_store", None)
    try:
        current = await store.authenticate(request.cookies.get(COOKIE_NAME), time.time()) if store else None
    except Exception:
        raise HTTPException(503, "Authentication unavailable") from None
    if not current:
        raise HTTPException(401, "Authentication required")
    request.state.auth_session_hash = current


async def require_session(request: Request) -> None:
    await require_authentication(request, session_only=True)
