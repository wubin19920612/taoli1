import logging
from operator import attrgetter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from app.api import mobile_query_utils as query_utils
from app.api import routes_premium_index as web
from app.core.security import require_session
from app.models.market import MarketType
from app.models.mobile import (
    MobilePremiumIndexCurrent,
    MobilePremiumIndexPoint,
    MobilePremiumIndexResult,
    MobilePremiumIndexStats,
)
from app.models.premium_index import (
    PREMIUM_INDEX_INTERVAL_OPTIONS,
    PREMIUM_INDEX_MIN_HOURS,
    PremiumIndexMarketQuery,
    PremiumIndexQueryResult,
)
from app.services.mobile_summary import EXCHANGE_LABELS
from app.services.premium_index_query import PremiumIndexQueryService
from app.services.symbol_aliases import (
    ResolvedSymbolAlias,
    apply_premium_index_current_alias,
    apply_premium_index_symbol_alias,
)

router = APIRouter(prefix="/premium-index", dependencies=[Depends(require_session)])
logger = logging.getLogger(__name__)

_WARNING_MESSAGES = (
    ("查询窗口超过", "查询窗口较长，已自动调整历史周期。"),
    ("最早可用数据为", "历史数据覆盖不足，已按可获取数据展示。"),
    ("已自动改查最近", "所选时段没有历史溢价指数，已自动缩短查询时段。"),
    ("没有公开分钟级历史 premium 接口", "Hyperliquid 分钟级历史溢价指数为估算值。"),
    ("已从当前点开始绘图", "没有历史溢价指数，已从当前点开始绘图。"),
    ("溢价指数历史失败", "部分历史溢价指数获取失败。"),
)

router.add_api_route(
    "/exchanges", web.list_premium_index_exchanges, methods=["GET"], response_model=list[str],
)


class MobilePremiumIndexCurrentResponse(MobilePremiumIndexCurrent):
    dex: str | None


def _log_upstream(message: str) -> None:
    logger.warning("Mobile premium index upstream: %r", query_utils.redact_upstream(message))


def _limits(request: Request) -> query_utils.MobileQueryLimits:
    state = request.app.state
    limits = getattr(state, "mobile_premium_index_limits", None)
    if limits is None:
        state.mobile_premium_index_cleanup_tasks = set()
        limits = state.mobile_premium_index_limits = query_utils.MobileQueryLimits(
            state.mobile_premium_index_cleanup_tasks, logger=logger, label="溢价指数查询",
            log_upstream=_log_upstream,
        )
    return limits


async def _requested_market(
    exchange: str = Query(...),
    symbol: str = Query(...),
    dex: str | None = Query(default=None),
) -> PremiumIndexMarketQuery:
    if exchange.strip().lower() == "hyperliquid" and symbol.count(":") > 1:
        raise HTTPException(422, "Hyperliquid 标的只允许一个 DEX 前缀")
    try:
        return PremiumIndexMarketQuery(exchange=exchange, symbol=symbol, dex=dex)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from None


async def _resolved_market(
    request: Request, market: PremiumIndexMarketQuery,
) -> tuple[PremiumIndexMarketQuery, ResolvedSymbolAlias]:
    try:
        resolver = await web._symbol_alias_resolver(request)
        alias = resolver.resolve(
            exchange=market.exchange, symbol=market.symbol, market_type=MarketType.FUTURE, dex=market.dex,
        )
    except ValueError:
        raise HTTPException(422, "无法解析溢价指数标的") from None
    return market.model_copy(update={"symbol": alias.raw_symbol, "dex": alias.dex}), alias


def _upstream_failure(exchange: str, exc: Exception) -> HTTPException:
    _log_upstream(f"{type(exc).__name__}: {exc}")
    return HTTPException(502, f"交易所数据获取失败（{EXCHANGE_LABELS[exchange]}）")


def _mobile_warnings(warnings: list[str]) -> list[str]:
    messages: dict[str, None] = {}
    for warning in warnings:
        _log_upstream(warning)
        message = next(
            (message for marker, message in _WARNING_MESSAGES if marker in warning),
            "部分交易所数据暂不可用。",
        )
        messages[message] = None
    return list(messages)


def _mobile_result(result: PremiumIndexQueryResult, alias: ResolvedSymbolAlias) -> MobilePremiumIndexResult:
    sampled = query_utils.downsample(
        result.points, timestamp=attrgetter("bucket_at"), value=attrgetter("premium_pct"),
    )
    # Alias scaling changes prices only. Keep the service's statistics over the full series.
    aliased = apply_premium_index_symbol_alias(result.model_copy(update={"points": sampled}), alias=alias)
    return MobilePremiumIndexResult(
        exchange=aliased.exchange,
        symbol=aliased.symbol,
        dex=aliased.dex,
        hours=aliased.hours,
        interval_minutes=aliased.interval_minutes,
        observed_at=aliased.observed_at,
        raw_point_count=len(result.points),
        premium_pct=MobilePremiumIndexStats.model_validate(aliased.premium_pct),
        current=MobilePremiumIndexCurrent.model_validate(aliased.current) if aliased.current else None,
        points=[
            MobilePremiumIndexPoint(t=p.bucket_at, pct=p.premium_pct, mark=p.mark_price, index=p.index_price)
            for p in aliased.points
        ],
        warnings=_mobile_warnings(aliased.warnings),
    )


@router.get("/query")
async def query_premium_index(
    request: Request,
    requested_market: Annotated[PremiumIndexMarketQuery, Depends(_requested_market)],
    hours: int = Query(default=24, ge=PREMIUM_INDEX_MIN_HOURS),
    interval_minutes: int = Query(default=1),
) -> MobilePremiumIndexResult:
    if interval_minutes not in PREMIUM_INDEX_INTERVAL_OPTIONS:
        allowed = ", ".join(str(value) for value in PREMIUM_INDEX_INTERVAL_OPTIONS)
        raise HTTPException(422, f"interval_minutes must be one of: {allowed}")

    async def fetch(service: PremiumIndexQueryService) -> MobilePremiumIndexResult:
        market, alias = await _resolved_market(request, requested_market)
        try:
            result = await service.query(market, hours=hours, interval_minutes=interval_minutes)
        except Exception as exc:  # noqa: BLE001 - the service can propagate raw exchange errors.
            raise _upstream_failure(market.exchange, exc) from None
        return _mobile_result(result, alias)

    factory = (
        getattr(request.app.state, "premium_index_query_service_factory", None)
        or PremiumIndexQueryService
    )
    limits = _limits(request)
    with limits.query(request.state.auth_session_hash) as started:
        return await limits.run(request, "query", started, factory, fetch)


@router.get("/current")
async def get_current_premium_index(
    request: Request,
    requested_market: Annotated[PremiumIndexMarketQuery, Depends(_requested_market)],
) -> MobilePremiumIndexCurrentResponse:
    async def fetch(service: PremiumIndexQueryService) -> MobilePremiumIndexCurrentResponse:
        market, alias = await _resolved_market(request, requested_market)
        try:
            current = await service.current(market)
        except Exception as exc:  # noqa: BLE001 - the service can propagate raw exchange errors.
            raise _upstream_failure(market.exchange, exc) from None
        aliased = apply_premium_index_current_alias(current, alias=alias)
        return MobilePremiumIndexCurrentResponse.model_validate(aliased)

    factory = (
        getattr(request.app.state, "premium_index_query_service_factory", None)
        or PremiumIndexQueryService
    )
    limits = _limits(request)
    started = limits.start(request.state.auth_session_hash, "current")
    return await limits.run(request, "current", started, factory, fetch)
