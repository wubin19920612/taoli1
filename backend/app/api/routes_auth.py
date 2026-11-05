import json
import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response

from app.core.security import (
    COOKIE_NAME, auth_configured, browser_label, client_ip, constant_time_equal, require_session,
)

router = APIRouter(prefix="/auth")
logger = logging.getLogger(__name__)
LOGIN_ERROR = "密码或验证码错误"


async def notify_login(notifier, event, ip, browser, now):
    try:
        await notifier.send_text(
            f"taoli1 {event}\nIP: {ip}\n浏览器: {browser}\n时间: {datetime.fromtimestamp(now, UTC).isoformat()}"
        )
    except Exception:
        logger.warning("Authentication notification could not be delivered")


@router.post("/login")
async def login(request: Request, response: Response, tasks: BackgroundTasks):
    settings = request.app.state.settings
    # Bounded manual parsing prevents validation errors echoing credentials.
    payload = None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            break
    if len(body) <= 8192:
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            pass
    password = payload.get("password") if isinstance(payload, dict) else None
    code = payload.get("code") if isinstance(payload, dict) else None
    valid_shape = isinstance(password, str) and isinstance(code, str)
    if not auth_configured(settings):
        raise HTTPException(401, LOGIN_ERROR)
    ip, browser, now = client_ip(request), browser_label(request), time.time()
    password_ok = constant_time_equal(settings.dashboard_password, password if valid_shape else "")
    try:
        result = await request.app.state.auth_store.login(
            password_ok=password_ok and valid_shape, secret=settings.dashboard_totp_secret,
            code=code if valid_shape else "", ip=ip, browser=browser, now=now,
            days=settings.dashboard_session_days,
        )
    except Exception:
        raise HTTPException(503, "Authentication unavailable") from None
    if result.rate_limited:
        if result.notify_rate_limit:
            await notify_login(request.app.state.feishu_notifier, "登录全局限速", ip, browser, now)
        raise HTTPException(429, "登录尝试过于频繁，请稍后重试", headers={"Retry-After": "60"})
    if result.ip_locked:
        # HTTPException responses do not run injected BackgroundTasks.
        await notify_login(request.app.state.feishu_notifier, "登录锁定", ip, browser, now)
    if not result.token:
        raise HTTPException(401, LOGIN_ERROR)
    tasks.add_task(notify_login, request.app.state.feishu_notifier, "登录成功", ip, browser, now)
    response.set_cookie(COOKIE_NAME, result.token, max_age=settings.dashboard_session_days * 86400,
                        httponly=True, secure=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@router.get("/sessions", dependencies=[Depends(require_session)])
async def sessions(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.auth_store.sessions(request.state.auth_session_hash, time.time())


async def _logout(request, response, all_sessions=False):
    await request.app.state.auth_store.revoke(request.state.auth_session_hash, time.time(), all_sessions=all_sessions)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@router.post("/logout", dependencies=[Depends(require_session)])
async def logout(request: Request, response: Response):
    return await _logout(request, response)


@router.post("/sessions/revoke-all", dependencies=[Depends(require_session)])
async def revoke_all(request: Request, response: Response):
    return await _logout(request, response, True)
