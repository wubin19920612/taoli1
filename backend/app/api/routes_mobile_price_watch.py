import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api.mobile_query_utils import MobileQueryLimits
from app.core.security import require_session
from app.models.mobile import PriceWatchConfig, PriceWatchQuotes, PriceWatchSymbols, PriceWatchUpdate

logger = logging.getLogger(__name__)


class PriceWatchRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def handle_request(request):
            try:
                return await handle(request)
            except RequestValidationError:
                raise HTTPException(422, "币价监控参数无效，请检查关注币种和提醒条件") from None
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning("手机币价监控失败 (%s)", type(exc).__name__)
                raise HTTPException(503, "币价数据暂不可用") from None

        return handle_request


router = APIRouter(prefix="/price-watch", dependencies=[Depends(require_session)], route_class=PriceWatchRoute)


def _limits(request, operation):
    state = request.app.state
    if not hasattr(state, "price_watch_limits"):
        state.price_watch_limits = {
            name: MobileQueryLimits(
                set(), logger=logger, label="币价监控", log_upstream=logger.warning,
                min_start_interval_seconds=interval,
            ) for name, interval in (("save", 1), ("quotes", 2))
        }
    return state.price_watch_limits[operation]


@router.get("", response_model=PriceWatchConfig)
async def get_config(request: Request):
    return await request.app.state.mobile_price_watch.get()


@router.post("", response_model=PriceWatchConfig)
async def save_config(payload: PriceWatchUpdate, request: Request):
    _limits(request, "save").start(request.state.auth_session_hash, "save")
    return await request.app.state.mobile_price_watch.save(payload)


@router.get("/quotes", response_model=PriceWatchQuotes)
async def get_quotes(request: Request, symbols: str | None = None):
    requested = None
    if symbols is not None:
        try:
            requested = PriceWatchSymbols(symbols=symbols.split(",")).symbols
        except ValidationError:
            raise HTTPException(422, "币种无效，最多查询 20 个币") from None
    with _limits(request, "quotes").query(request.state.auth_session_hash):
        return await request.app.state.mobile_price_watch.quotes(requested)
