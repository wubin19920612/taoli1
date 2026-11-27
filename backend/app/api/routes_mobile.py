import asyncio
import sqlite3
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from pathlib import Path as FilePath
from time import monotonic
from typing import Literal
from uuid import UUID

from anyio import open_file
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api import (
    routes_mobile_app,
    routes_mobile_funding,
    routes_mobile_instruments,
    routes_mobile_opportunities,
    routes_mobile_pair,
    routes_mobile_premium,
    routes_mobile_price_watch,
)
from app.api.routes_astro import list_astro_pairs
from app.api.routes_pair_spread import _resolved_leg, _symbol_alias_resolver
from app.core.security import browser_label, client_ip, require_session
from app.models.mobile import (
    CARD_PARAM_LABELS,
    EQN_NEWS_CHANNELS,
    CardControlUpdate,
    CardHistory,
    CardParamsResult,
    CardParamsUpdate,
    CardPinUpdate,
    DeviceBinding,
    DeviceProof,
    EqnNewsSettings,
    HistoryPoint,
    MobileAlert,
    MobileRangeBacktestRequest,
    MobileRangeBacktestResult,
    MobileSummary,
    OpsDevice,
    PushRegistration,
    PushToken,
    UnlockCode,
    UnlockSettings,
)
from app.models.mobile_inbox import MAX_IMAGES, InboxItem
from app.models.pair_range_backtest import PairRangeBacktestRequest
from app.models.pair_spread import PairSpreadLegQuery
from app.models.settings import FeeSettings
from app.services.mobile_inbox import CHUNK_SIZE, INVALID_FORM, InboxError
from app.services.mobile_summary import (
    condition,
    legs_for,
    number,
    positive,
    ratio_mode,
    reference,
    spread_pct,
    summary,
    text_field,
)
from app.services.pair_spread_query import PairSpreadQueryError, PairSpreadQueryService

router = APIRouter(prefix="/mobile")
router.include_router(routes_mobile_app.router)
router.include_router(routes_mobile_funding.router)
router.include_router(routes_mobile_instruments.router)
router.include_router(routes_mobile_opportunities.router)
router.include_router(routes_mobile_pair.router)
router.include_router(routes_mobile_premium.router)
router.include_router(routes_mobile_price_watch.router)
RANGE_TIMEOUT_SECONDS = 60


class _MobileInputRoute(APIRoute):
    validation_message: str

    def invalid_input(self, request, exc):
        return HTTPException(422, self.validation_message)

    def get_route_handler(self):
        handle = super().get_route_handler()

        async def handle_request(request):
            try:
                return await handle(request)
            except RequestValidationError as exc:
                # FastAPI's default 422 includes the rejected input.
                raise self.invalid_input(request, exc) from None

        return handle_request


class _PushRoute(_MobileInputRoute):
    validation_message = "推送参数无效，请检查设备令牌、通知类型和阈值"


class _PinRoute(_MobileInputRoute):
    validation_message = "置顶参数无效，请检查卡片编号和置顶状态"


push = APIRouter(prefix="/push", dependencies=[Depends(require_session)], route_class=_PushRoute)


@push.post("/register")
async def register_push(payload: PushRegistration, request: Request):
    await request.app.state.mobile_push_repo.register(
        payload.token.get_secret_value(), request.state.auth_session_hash,
        payload.kinds.model_dump(), payload.threshold_pp,
    )
    return {"ok": True}


@push.delete("/register")
async def delete_push(payload: PushToken, request: Request):
    await request.app.state.mobile_push_repo.remove(payload.token.get_secret_value(), request.state.auth_session_hash)
    return {"ok": True}


@push.post("/test")
async def test_push(payload: PushToken, request: Request):
    result = await request.app.state.mobile_push.test(payload.token.get_secret_value(), request.state.auth_session_hash)
    return {"sent": result.sent, "reason": result.reason}


router.include_router(push)

pins = APIRouter(prefix="/pins", dependencies=[Depends(require_session)], route_class=_PinRoute)


@pins.get("")
async def list_pins(request: Request) -> dict[str, dict[str, float]]:
    return {"pins": await request.app.state.mobile_pins_repo.list()}


@pins.post("")
async def update_pin(payload: CardPinUpdate, request: Request) -> dict[str, dict[str, float]]:
    repo = request.app.state.mobile_pins_repo
    if payload.pinned:
        if not await repo.pin(payload.card_id):
            raise HTTPException(409, "置顶数量已达上限")
    else:
        await repo.unpin(payload.card_id)
    return {"pins": await repo.list()}


router.include_router(pins)


class _InboxRoute(_MobileInputRoute):
    validation_message = INVALID_FORM

    def get_route_handler(self):
        handle = super().get_route_handler()

        async def handle_request(request):
            try:
                return await handle(request)
            except InboxError as exc:
                raise HTTPException(exc.status, str(exc)) from None

        return handle_request


@asynccontextmanager
async def _inbox_lifespan(app: FastAPI):
    await app.state.mobile_inbox.cleanup_startup()
    yield


class _InboxImageResponse(Response):
    def __init__(self, path: FilePath, media_type: str):
        super().__init__(media_type=media_type)
        self.path = path

    async def __call__(self, scope, receive, send) -> None:
        try:
            source = await open_file(self.path, "rb")
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            response = JSONResponse({"detail": "收件箱图片已清理"}, status_code=410)
            await response(scope, receive, send)
            return

        async def chunks():
            while chunk := await source.read(CHUNK_SIZE):
                yield chunk

        async with source:
            response = StreamingResponse(chunks(), media_type=self.media_type,
                                         headers={"X-Content-Type-Options": "nosniff"})
            await response(scope, receive, send)


inbox = APIRouter(prefix="/inbox", dependencies=[Depends(require_session)], route_class=_InboxRoute,
                  lifespan=_inbox_lifespan)


@inbox.post("")
async def create_inbox_item(request: Request) -> InboxItem:
    return await request.app.state.mobile_inbox.receive(
        request.headers.get("content-type", ""), request.stream(), request.state.auth_session_hash,
    )


@inbox.get("")
async def list_inbox_items(request: Request, limit: int = Query(50, ge=1, le=200),
                           before: datetime | None = None) -> list[InboxItem]:
    return await request.app.state.mobile_inbox.list_items(limit, before)


@inbox.get("/{item_id}/images/{number}")
async def get_inbox_image(request: Request, item_id: UUID, number: int = Path(ge=1, le=MAX_IMAGES)):
    path, media_type = await request.app.state.mobile_inbox.image(item_id, number)
    return _InboxImageResponse(path, media_type)


@inbox.delete("/{item_id}")
async def delete_inbox_item(request: Request, item_id: UUID) -> dict[str, bool]:
    await request.app.state.mobile_inbox.delete(item_id)
    return {"ok": True}


router.include_router(inbox)

eqn_news = APIRouter(prefix="/eqn-news", dependencies=[Depends(require_session)])


@eqn_news.get("/settings")
async def get_eqn_news_settings(request: Request):
    cursors = await request.app.state.settings_repo.get_eqn_news_cursors()
    return {
        "channels": list(cursors),
        "available": [{"key": key, "name_zh": name} for key, name in EQN_NEWS_CHANNELS.items()],
    }


@eqn_news.post("/settings")
async def set_eqn_news_settings(payload: EqnNewsSettings, request: Request):
    await request.app.state.settings_repo.set_eqn_news_settings(payload)
    return payload


router.include_router(eqn_news)


class _CardControlRoute(_MobileInputRoute):
    validation_message = "手机改卡参数无效，请检查输入"

    def invalid_input(self, request, exc):
        if request.url.path == "/api/mobile/cards/params":
            error = exc.errors()[0]
            if error["type"] == "missing_card_expected":
                return HTTPException(422, error["msg"])
            path = error["loc"][1:]
            if path and path[0] in ("changes", "expected"):
                group = "修改项" if path[0] == "changes" else "预期值"
                if len(path) > 1:
                    label = CARD_PARAM_LABELS.get(path[1])
                    if label:
                        return HTTPException(422, f"{label}的{group}必须是有限数字")
                    return HTTPException(422, f"{group}含有不支持的字段")
                return HTTPException(422, "请至少提供一个修改项" if group == "修改项" else "请提供预期值对象")
            if path == ("card_id",):
                return HTTPException(422, "卡片编号无效")
            return HTTPException(422, "改卡请求字段或格式无效")
        if request.url.path == "/api/mobile/ops/unlock" and isinstance(exc.body, dict):
            challenge_id = exc.body.get("challenge_id")
            if isinstance(challenge_id, str) and hasattr(request.state, "auth_session_hash"):
                request.app.state.mobile_card_control.consume_challenge(challenge_id)
        return super().invalid_input(request, exc)

    def get_route_handler(self):
        handle = super().get_route_handler()

        async def handle_request(request):
            # Authenticate before accessing service state, including for malformed requests.
            await require_session(request)
            if request.method == "POST":
                service = getattr(request.app.state, "mobile_card_control", None)
                if service is None or not await service.control_enabled():
                    raise HTTPException(503, "手机改卡未启用")
            try:
                return await handle(request)
            except (sqlite3.Error, ValueError):
                raise HTTPException(503, "手机改卡服务暂不可用") from None

        return handle_request


controls = APIRouter(route_class=_CardControlRoute)


@router.post("/ops/kill", dependencies=[Depends(require_session)])
async def kill_card_control(request: Request):
    return await request.app.state.mobile_card_control.kill(request.state.auth_session_hash, browser_label(request))


@controls.get("/ops/status")
async def card_control_status(request: Request):
    return await request.app.state.mobile_card_control.status(request.state.auth_session_hash)


@controls.post("/ops/devices")
async def bind_ops_device(payload: DeviceBinding, request: Request):
    return await request.app.state.mobile_card_control.bind(client_ip(request), payload)


@controls.post("/ops/devices/revoke")
async def revoke_ops_device(payload: OpsDevice, request: Request):
    await request.app.state.mobile_card_control.revoke(request.state.auth_session_hash, payload.device_id)
    return {"ok": True}


@controls.post("/ops/challenge")
async def issue_device_challenge(payload: OpsDevice, request: Request):
    return await request.app.state.mobile_card_control.challenge(request.state.auth_session_hash, payload.device_id)


@controls.post("/ops/unlock")
async def unlock_card_control(payload: UnlockCode | DeviceProof, request: Request):
    return await request.app.state.mobile_card_control.unlock(request.state.auth_session_hash, client_ip(request), payload)


@controls.get("/ops/settings", response_model=UnlockSettings)
async def get_ops_settings(request: Request):
    return {"unlock_minutes": await request.app.state.mobile_card_control.repo.unlock_minutes()}


@controls.post("/ops/settings", response_model=UnlockSettings)
async def update_ops_settings(payload: UnlockSettings, request: Request):
    service = request.app.state.mobile_card_control
    service.require_unlock(request.state.auth_session_hash)
    await service.repo.set_unlock_minutes(payload.unlock_minutes)
    return payload


@controls.post("/cards/control")
async def control_card(payload: CardControlUpdate, request: Request):
    return await request.app.state.mobile_card_control.control(request.state.auth_session_hash, payload)


@controls.post("/cards/params", response_model=CardParamsResult)
async def update_card_params(payload: CardParamsUpdate, request: Request):
    return await request.app.state.mobile_card_control.update_params(request, request.state.auth_session_hash, payload)


router.include_router(controls)


@router.get("/summary", response_model=MobileSummary)
async def mobile_summary(request: Request):
    return await summary(request, await list_astro_pairs(request))


@router.get("/cards/by-id/{card_id}/history", response_model=CardHistory)
async def card_history_by_id(card_id: str, request: Request, hours: int = Query(24, ge=1, le=72)):
    pair = await _pair_by_id(request, card_id)
    if pair is None:
        raise HTTPException(404, "找不到卡片")
    return await _history_for_pair(request, pair, hours)


async def _pair_by_id(request: Request, card_id: str) -> dict | None:
    for pair in await list_astro_pairs(request):
        if isinstance(pair, dict) and pair.get("id") == card_id:
            return pair
    return None


@router.get("/cards/{name}/history", response_model=CardHistory)
async def card_history(
    name: str, request: Request, hours: int = Query(24, ge=1, le=72),
    card_type: str | None = Query(None, alias="type"),
):
    pairs = [
        pair for pair in await list_astro_pairs(request)
        if isinstance(pair, dict) and pair.get("name") == name
        and (card_type is None or text_field(pair, "type", "FF") == card_type)
    ]
    if not pairs:
        raise HTTPException(404, "找不到卡片")
    if len(pairs) != 1:
        types = sorted({text_field(pair, "type", "FF") for pair in pairs})
        raise HTTPException(409, detail={"message": "卡片名称重复，无法确定两腿", "types": types})
    return await _history_for_pair(request, pairs[0], hours)


async def _resolved_card_legs(request: Request, pair: dict):
    route = legs_for(pair)
    if not route:
        raise HTTPException(422, "无法从卡片名称识别两腿标的")
    resolver = await _symbol_alias_resolver(request)
    try:
        return [
            _resolved_leg(
                resolver,
                exchange=leg.exchange,
                symbol=leg.symbol,
                market_type=leg.market_type,
                dex=(leg.dex or "main") if leg.exchange == "hyperliquid" else None,
            )
            for leg in route
        ]
    except ValidationError as exc:
        raise HTTPException(422, "不支持该卡片的历史行情") from exc


async def _history_for_pair(request: Request, pair: dict, hours: int) -> CardHistory:
    (a, alias_a), (b, alias_b) = await _resolved_card_legs(request, pair)
    factory = request.app.state.pair_spread_query_service_factory or PairSpreadQueryService
    service = factory()
    try:
        history = await service.query(a, b, hours=hours, interval_seconds=60, include_current=False)
    except PairSpreadQueryError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        await service.aclose()

    # Only aligned candle closes exist historically. Both sides use this
    # reference series; the live summary separately prices the executable sides.
    scale_a = alias_a.price_multiplier
    scale_b = reference(pair) * scale_a if ratio_mode(pair) else alias_b.price_multiplier
    points = []
    for point in history.points:
        spread = spread_pct(point.leg1_close * scale_a, point.leg2_close * scale_b)
        points.append(HistoryPoint(t=point.bucket_at, open_spread=spread, close_spread=spread))
    return CardHistory(
        points=points,
        open_condition=condition(pair, "openPosition"),
        close_condition=condition(pair, "closePosition"),
    )


class _RangeRoute(_MobileInputRoute):
    validation_message = "区间无效"


class _RangeLimits:
    def __init__(self):
        self.active: set[str] = set()
        self.starts: dict[str, deque[float]] = {}

    @contextmanager
    def reserve(self, session: str):
        if session in self.active:
            raise HTTPException(429, "上一次估算还在进行")
        now = monotonic()
        cutoff = now - 60
        self.starts = {key: times for key, times in self.starts.items() if times[-1] > cutoff}
        starts = self.starts.setdefault(session, deque())
        while starts and starts[0] <= cutoff:
            starts.popleft()
        if len(starts) >= 6:
            raise HTTPException(429, "每分钟最多估算六次，请稍后再试")
        starts.append(now)
        self.active.add(session)
        try:
            yield
        finally:
            self.active.remove(session)


def _position_notional(pair: dict) -> float:
    # Same gross entry notional as mobile summary, including partially priced positions.
    total = 0.0
    for quantity, price in (("aExPosition", "avgOpenAExPrice"), ("bExPosition", "avgOpenBExPrice")):
        total += abs(number(pair.get(quantity)) or 0) * (positive(pair.get(price)) or 0)
    return total or 1000.0


def _range_leg_label(venue: str, leg: PairSpreadLegQuery) -> str:
    symbol = leg.symbol.removesuffix("USDT")
    return f"{venue} {leg.dex}:{symbol}" if leg.dex else f"{venue} {symbol}"


backtests = APIRouter(dependencies=[Depends(require_session)], route_class=_RangeRoute)


@backtests.post("/range-backtest", response_model=MobileRangeBacktestResult)
async def mobile_range_backtest(payload: MobileRangeBacktestRequest, request: Request):
    state = request.app.state
    limits = getattr(state, "mobile_range_limits", None)
    if limits is None:
        state.mobile_range_limits = limits = _RangeLimits()
    with limits.reserve(request.state.auth_session_hash):
        pair = await _pair_by_id(request, payload.card_id)
        if pair is None:
            raise HTTPException(404, "未找到卡片")
        (leg1, _), (leg2, _) = await _resolved_card_legs(request, pair)
        try:
            query = PairRangeBacktestRequest(
                leg1=leg1, leg2=leg2, direction="long1_short2", start_at=payload.start_at, end_at=payload.end_at,
                notional_usd=payload.notional_usd if payload.notional_usd is not None else _position_notional(pair),
            )
        except ValidationError:
            raise HTTPException(422, "区间无效") from None
        fees = getattr(getattr(state, "market_collector", None), "fee_settings", None) or FeeSettings()
        factory = state.pair_spread_query_service_factory or PairSpreadQueryService
        service = factory()
        try:
            async with asyncio.timeout(RANGE_TIMEOUT_SECONDS):
                result = await service.query_range_backtest(query, fee_settings=fees)
        except TimeoutError:
            raise HTTPException(504, "区间估算超时") from None
        except PairSpreadQueryError as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            await service.aclose()
        return MobileRangeBacktestResult(
            **result.model_dump(), notional_usd=query.notional_usd, direction=query.direction,
            leg1_label=_range_leg_label(pair["buyEx"], leg1), leg2_label=_range_leg_label(pair["sellEx"], leg2),
        )


router.include_router(backtests)


@router.get("/alerts", response_model=list[MobileAlert])
async def alerts(
    request: Request,
    since: float = Query(0, ge=0, allow_inf_nan=False),
    limit: int = Query(100, ge=1, le=200),
    group: Literal["alerts", "news", "all"] = "alerts",
):
    return await request.app.state.mobile_alert_repo.list(since, limit, group=group)
