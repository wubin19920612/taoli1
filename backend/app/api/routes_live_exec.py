"""Fixed-path reader and session-only administration for crossex-exec."""
import asyncio
import hashlib
import json
import logging
import math
import re
import sys
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.core.security import require_session

router = APIRouter(prefix="/live-exec")
UPSTREAM_PATHS = {
    "summary": "/api/v1/summary",
    "positions": "/api/v1/positions",
    "trades": "/api/v1/trades",
    "stats": "/api/v1/stats",
    "config": "/api/v1/config",
    "config/audit": "/api/v1/config/audit",
    "universe": "/api/v1/universe",
    "mappings": "/api/v1/mappings",
    "external-positions": "/api/v1/external-positions",
}
# Uvicorn's default CLI config only enables its own loggers. Audit records must
# reach container stdout even when the root logger remains at WARNING.
logger = logging.getLogger("taoli1.audit.live_exec")
logger.setLevel(logging.INFO)
logger.propagate = False
if not any(handler.name == "live_exec_admin_stdout" for handler in logger.handlers):
    _audit_handler = logging.StreamHandler(sys.stdout)
    _audit_handler.name = "live_exec_admin_stdout"
    _audit_handler.setLevel(logging.INFO)
    _audit_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(_audit_handler)
# Round 8 whitelist/ranges from codex/crossex-exec-r8 b24f5af settings.py.
# No executor code is imported. The upstream remains the final authority.
CONFIG_RULES = {
    "min_quote_volume_usd": (100000, None, False),
    "min_venues_with_volume": (2, None, True),
    "sampling_min_quote_volume_usd": (0, None, False),
    "order_usd_max": (5, 150, False),
    "depth_fraction": (.01, .5, False),
    "oi_fraction": (.00001, .005, False),
    "max_total_open_usd": (0, 600, False),
    "max_open_pairs": (0, 3, True),
    "max_equity_drop_usd": (10, 150, False),
    "daily_loss_stop_usd": (1, 20, False),
    "total_loss_stop_usd": (1, 100, False),
    "min_excess_bps": (15, None, False),
    "max_book_skew_ms": (0, 300, False),
    "max_book_age_seconds": (0, 1, False),
    "max_funding_diff_bps_h": (0, .5, False),
    "max_funding_rate_8h": (0, .0005, False),
    "max_baseline_bps": (0, 10, False),
    "max_baseline_mad_bps": (0, 10, False),
    "max_normal_deviation_bps": (0, 10, False),
    "max_reference_age_seconds": (0, 90, False),
    "min_outlier_share": (.7, 1, False),
    "min_venues": (3, 6, True),
}
MAX_BODY = 8192
UPSTREAM_ERRORS = {
    "invalid_update": "更新无效：请核对参数范围、类别与所选合约，或刷新已过期的映射",
    "invalid_query": "请求格式或参数无效",
    "invalid_request": "请求无效",
    "unauthorized": "实盘服务认证失败",
    "admin_required": "实盘服务拒绝管理权限",
    "not_found": "实盘服务未找到该资源",
    "method_not_allowed": "实盘服务不支持该操作",
    "temporarily_unavailable": "实盘服务暂不可用，请稍后刷新核实",
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


@router.get("/config")
async def get_config(request: Request) -> dict:
    validate_query(request, set())
    return await proxy(request, "config")


@router.get("/config/audit")
async def get_config_audit(request: Request) -> dict:
    validate_query(request, set())
    return await proxy(request, "config/audit")


@router.get("/universe")
async def get_universe(request: Request) -> dict:
    validate_query(request, set())
    return await proxy(request, "universe")


@router.get("/mappings")
async def get_mappings(request: Request) -> dict:
    validate_query(request, set())
    return await proxy(request, "mappings")


@router.get("/external-positions")
async def get_external_positions(request: Request) -> dict:
    validate_query(request, set())
    return await proxy(request, "external-positions")


def _origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path
            or parsed.query or parsed.fragment or parsed.username is not None
            or parsed.password is not None):
        raise ValueError("invalid_origin")
    return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


async def require_admin_session(request: Request) -> None:
    # Unlike the router's general authentication, never allow a machine token or dev bypass.
    await require_session(request)
    try:
        # Production login cookies already require HTTPS. TLS terminates before the
        # frontend nginx, and uvicorn uses --no-proxy-headers: use the public HTTPS
        # origin with the preserved Host, never client-supplied Forwarded headers.
        scheme = "https" if request.app.state.settings.environment.strip().lower() == "production" else request.url.scheme
        if (len(request.headers.getlist("origin")) != 1
                or request.headers.getlist("x-live-exec-csrf") != ["1"]
                or len(request.headers.getlist("host")) != 1
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
                or _origin(request.headers["origin"]) != _origin(f"{scheme}://{request.headers['host']}")):
            raise ValueError("csrf")
    except ValueError:
        raise HTTPException(403, "请求来源校验失败，请从本站实盘页面操作") from None


def _strict_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


async def _body(request: Request) -> dict:
    lengths = request.headers.getlist("content-length")
    if (request.headers.getlist("content-type") != ["application/json"]
            or "transfer-encoding" in request.headers or len(lengths) != 1
            or not re.fullmatch(r"[0-9]{1,5}", lengths[0])):
        raise HTTPException(400, "请求体须为具有明确长度的 JSON 对象")
    length = int(lengths[0])
    if length > MAX_BODY:
        raise HTTPException(413, "请求体超过 8192 字节限制")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BODY:
            raise HTTPException(413, "请求体超过 8192 字节限制")
        raw.extend(chunk)
    try:
        if not raw or len(raw) != length:
            raise ValueError("invalid_length")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object,
                           parse_constant=reject_json_constant)
        if not isinstance(value, dict):
            raise TypeError("object_required")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(400, "JSON 请求体无效，不允许重复字段或非有限数值") from None
    return value


def _validate_update(resource: str, action: str, body: dict) -> None:
    if resource == "external-positions":
        # Six-part raw identity, matching crossex-exec bfb2861 external.checked_identity.
        # Ownership, catalog freshness and remote positions remain upstream decisions.
        identity = body.get("identity")
        if (body.keys() != {"identity", "expected_status", "reason"}
                or not isinstance(identity, dict)
                or identity.keys() != {"route", "exchange_type", "business", "symbol", "quote", "dex"}):
            raise HTTPException(400, "必须提供完整外仓身份、expected_status 和登记说明")
        symbol = identity.get("symbol")
        match = re.fullmatch(r"([A-Z][A-Z0-9]*)_FUTURE_([^|\s_]+)_(USDT|USDC)", symbol) if isinstance(symbol, str) and len(symbol) <= 160 else None
        if not match:
            raise HTTPException(400, "外仓原始合约身份无效")
        exchange, raw, quote = match.groups()
        dex = raw.split(":")[0] if ":" in raw else ""
        if ((":" in raw and (exchange != "HYPERLIQUID" or raw.count(":") != 1 or not all(raw.split(":"))))
                or any(ord(char) < 32 for char in raw)
                or identity != {"route": "crossex", "exchange_type": exchange, "business": "FUTURE",
                                "symbol": symbol, "quote": quote, "dex": dex}):
            raise HTTPException(400, "外仓身份各字段必须与原始合约精确一致")
        expected, reason = body.get("expected_status"), body.get("reason")
        if (not isinstance(expected, str) or not re.fullmatch(r"x1:[0-9a-f]{64}", expected)
                or not isinstance(reason, str) or not reason.strip() or len(reason) > 200
                or any(ord(char) < 32 for char in reason)):
            raise HTTPException(400, "外仓并发状态或登记说明无效（说明须为 1 至 200 字符且无控制字符）")
        return
    if resource == "universe":
        expected = body.get("expected_status")
        if not isinstance(expected, str) or not re.fullmatch(r"u1:[0-9a-f]{64}", expected):
            raise HTTPException(400, "必须提供交易范围的 expected_status")
    elif resource == "mappings":
        multiplier = body.get("expected_multiplier")
        if (body.keys() != {"expected_status", "expected_multiplier"}
                or body.get("expected_status") not in ("pending", "confirmed", "rejected")
                or not isinstance(multiplier, str) or not re.fullmatch(r"[0-9.eE+\-]{1,80}", multiplier)):
            raise HTTPException(400, "必须提供映射的 expected_status 和原始 expected_multiplier 字符串")
        return
    if resource == "config":
        if type(body.get("expected_version")) is not int or body["expected_version"] < 0:
            raise HTTPException(400, "必须提供非负整数 expected_version")
        changes = body.keys() - {"expected_version"}
        if not changes or changes - CONFIG_RULES.keys():
            raise HTTPException(400, "参数不能为空且必须在允许的字段白名单内")
        for key in changes:
            value = body[key]
            low, high, integer = CONFIG_RULES[key]
            try:
                valid = (type(value) in (int, float) and math.isfinite(value)
                         and (not integer or type(value) is int)
                         and value >= low and (high is None or value <= high))
            except OverflowError:
                valid = False
            if not valid:
                raise HTTPException(400, f"参数 {key} 类型或范围无效")
    elif action == "classify":
        category = body.get("category")
        markets = body.get("markets")
        if (body.keys() - {"category", "markets", "underlying_market", "expected_status"}
                or category not in ("crypto", "listed_equity", "pre_ipo", "commodity")
                or not isinstance(markets, list) or not markets
                or any(not isinstance(key, str) or not 1 <= len(key) <= 256
                       or not re.fullmatch(r"[A-Za-z0-9_.:-]+\|(?:future|spot)\|[A-Za-z0-9_.:/-]+", key)
                       for key in markets)
                or len(set(markets)) != len(markets)):
            raise HTTPException(400, "类别或精确合约列表无效")
        if category == "listed_equity":
            if body.get("underlying_market") not in ("US", "KRX"):
                raise HTTPException(400, "股票必须选择 US 或 KRX 标的市场")
        elif "underlying_market" in body:
            raise HTTPException(400, "非股票类别不得提交标的市场")
    elif body.keys() != {"expected_status"}:
        raise HTTPException(400, "此操作仅接受 expected_status")


def _audit_parameters(resource: str, identity: str, body: dict) -> str:
    # Log meaningful numeric changes, and fingerprints/counts for untrusted identifiers.
    # No arbitrary strings, request headers, session cookies, or upstream bodies enter logs.
    if resource == "external-positions":
        identity = json.dumps(body["identity"], sort_keys=True, separators=(",", ":"))
    if resource == "config":
        value = body
    else:
        value = {"target_sha256": hashlib.sha256(identity.encode()).hexdigest()[:16]}
        if "category" in body:
            value.update(category=body["category"], underlying_market=body.get("underlying_market"),
                         markets_count=len(body["markets"]),
                         markets_sha256=hashlib.sha256(json.dumps(body["markets"]).encode()).hexdigest()[:16])
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


async def admin_proxy(request: Request, resource: str, identity: str = "", action: str = "") -> dict | Response:
    path = f"/api/v1/{resource}" + (f"/{identity}" if identity else "") + (f"/{action}" if action else "")
    interface = f"{request.method} /{resource}" + ("/{id}" if identity else "") + (f"/{action}" if action else "")
    parameters = '{"validation":"rejected"}'
    status = 500
    try:
        validate_query(request, set())
        body = await _body(request)
        _validate_update(resource, action, body)
        parameters = _audit_parameters(resource, identity, body)
        settings = request.app.state.settings
        token = settings.crossex_exec_admin_token
        if not token or not settings.crossex_exec_url:
            raise HTTPException(503, "实盘管理接口未配置，无法写入")
        if token == settings.crossex_exec_api_key:
            raise HTTPException(503, "实盘管理令牌配置无效，必须独立于只读令牌")
        try:
            parsed = urlsplit(settings.crossex_exec_url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment):
                raise ValueError("invalid_configuration")
        except ValueError:
            raise HTTPException(503, "实盘管理服务地址配置无效") from None
        async with asyncio.timeout(3.0):
            async with httpx.AsyncClient(timeout=3.0, trust_env=False, follow_redirects=False) as client:
                response = await client.request(request.method, settings.crossex_exec_url.rstrip("/") + path,
                                                headers={"X-Api-Token": token}, json=body)
        if response.status_code == 409:
            status = 409
            # Preserve the upstream concurrency contract, without copying its headers.
            return Response(content=response.content, status_code=409, media_type="application/json")
        if response.status_code != 200:
            status = response.status_code if 400 <= response.status_code <= 599 else 502
            try:
                error = response.json().get("error")
                message = UPSTREAM_ERRORS.get(error, "实盘服务响应异常，请刷新核实操作结果") if isinstance(error, str) else None
            except (ValueError, AttributeError, RecursionError):
                message = None
            raise HTTPException(status, message or "实盘服务响应异常，请刷新核实操作结果")
        data = json.loads(response.content, parse_constant=reject_json_constant)
        status = 200
        return {"available": True, "data": data}
    except HTTPException as error:
        status = error.status_code
        raise
    except (httpx.TimeoutException, TimeoutError):
        status = 504
        raise HTTPException(status, "实盘服务请求超时，结果可能已生效，请刷新核实后再操作") from None
    except (httpx.RequestError, httpx.InvalidURL):
        status = 502
        raise HTTPException(status, "实盘服务连接失败，请刷新核实操作结果") from None
    except (ValueError, UnicodeError, RecursionError):
        status = 502
        raise HTTPException(status, "实盘服务返回格式异常，请刷新核实操作结果") from None
    finally:
        logger.info("live_exec_admin actor=session:%s interface=%s parameters=%s status=%s",
                    request.state.auth_session_hash[:16], interface, parameters, status)


@router.put("/config", dependencies=[Depends(require_admin_session)], response_model=None)
async def put_config(request: Request) -> dict | Response:
    return await admin_proxy(request, "config")


@router.post("/universe/{base}/{action}", dependencies=[Depends(require_admin_session)], response_model=None)
async def post_universe(request: Request, base: str, action: str) -> dict | Response:
    if not re.fullmatch(r"[A-Z0-9]{1,40}", base) or action not in {"exclude", "unexclude", "classify"}:
        raise HTTPException(400, "交易范围操作或币种无效")
    return await admin_proxy(request, "universe", base, action)


@router.post("/mappings/{identity}/{action}", dependencies=[Depends(require_admin_session)], response_model=None)
async def post_mapping(request: Request, identity: str, action: str) -> dict | Response:
    if not re.fullmatch(r"[0-9a-f]{24}", identity) or action not in {"confirm", "reject"}:
        raise HTTPException(400, "映射操作或标识无效")
    return await admin_proxy(request, "mappings", identity, action)


@router.post("/external-positions/{action}", dependencies=[Depends(require_admin_session)], response_model=None)
async def post_external_positions(request: Request, action: str) -> dict | Response:
    if action not in {"register", "unregister"}:
        raise HTTPException(400, "外仓登记操作无效")
    return await admin_proxy(request, "external-positions", action=action)
