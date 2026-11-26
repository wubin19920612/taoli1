import asyncio
import logging
from asyncio import wait
from collections.abc import Callable, Coroutine
from datetime import datetime
from operator import attrgetter
from time import monotonic
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from pydantic import ValidationError

from app.api import mobile_query_utils as query_utils
from app.api import routes_pair_spread as web
from app.api.mobile_query_utils import (
    CLOSE_TIMEOUT_SECONDS,
    MAX_UPSTREAM_TASKS,
    QUERY_TIMEOUT_SECONDS,
)
from app.core.security import require_session
from app.exchanges.base import utc_now
from app.models.market import MarketType
from app.models.mobile import (
    MobilePairSpreadCurrent,
    MobilePairSpreadFunding,
    MobilePairSpreadFundingPoint,
    MobilePairSpreadPoint,
    MobilePairSpreadResult,
    MobilePairSpreadStats,
)
from app.models.pair_spread import (
    PAIR_SPREAD_INTERVAL_OPTIONS,
    PAIR_SPREAD_MAX_INTERVAL_SECONDS,
    PAIR_SPREAD_MIN_HOURS,
    PAIR_SPREAD_MIN_INTERVAL_SECONDS,
    PairSpreadFundingPoint,
    PairSpreadLegQuery,
    PairSpreadPoint,
    PairSpreadPreset,
    PairSpreadQueryResult,
    PairSpreadValueStats,
)
from app.services.mobile_summary import EXCHANGE_LABELS
from app.services.pair_spread_query import (
    REALTIME_CACHE_MISS_WARNING,
    REALTIME_CACHE_RESTART_WARNING,
    PairSpreadQueryError,
    PairSpreadQueryService,
)
from app.services.pair_spread_query import (
    _scale_current_leg as scale_leg_by_divisor,
)
from app.services.symbol_aliases import _scale_current_leg as scale_leg_for_alias
from app.services.symbol_aliases import apply_pair_spread_symbol_aliases

router = APIRouter(prefix="/pair-spread", dependencies=[Depends(require_session)])
logger = logging.getLogger(__name__)
CLEANUP_SHUTDOWN_SECONDS = 5
MAX_FUNDING_HISTORY = 200
_Result = TypeVar("_Result")

_SAFE_WARNINGS = {
    REALTIME_CACHE_MISS_WARNING,
    REALTIME_CACHE_RESTART_WARNING,
    "盘口中间价不完整，本次快照未写入实时价差曲线。",
}
_WARNING_MESSAGES = (
    ("查询窗口超过", "查询窗口较长，已自动调整历史周期。"),
    ("Hyperliquid 官方接口只提供最近", "Hyperliquid 历史K线数量有限，可加大周期以查询更长时段。"),
    ("没有拿到可对齐K线，已自动改查", "所选时段没有可对齐的历史K线，已自动缩短查询时段。"),
    ("最早可对齐K线为", "历史K线覆盖不足，已按可获取数据展示。"),
    ("分钟K线失败", "部分交易所的历史K线获取失败。"),
    ("当前价格/资金失败", "部分交易所的当前价格或资金费率获取失败。"),
    ("历史资金费率失败", "部分交易所的历史资金费率获取失败。"),
    ("已降级为实时采样", "历史持仓量不可用，已改为实时采样。"),
)

router.add_api_route("/exchanges", web.list_pair_spread_exchanges, methods=["GET"])
router.add_api_route(
    "/presets", web.list_pair_spread_presets, methods=["GET"], response_model=list[PairSpreadPreset],
)


def _log_upstream(message: str) -> None:
    # Log diagnostics without URLs, credential fields or multiline log injection.
    logger.warning("Mobile pair spread upstream: %r", query_utils.redact_upstream(message))


def _mobile_warnings(warnings: list[str]) -> list[str]:
    messages: dict[str, None] = {}
    for warning in warnings:
        if warning in _SAFE_WARNINGS:
            messages[warning] = None
        else:
            _log_upstream(warning)
            message = next(
                (message for marker, message in _WARNING_MESSAGES if marker in warning),
                "部分交易所数据暂不可用。",
            )
            messages[message] = None
    return list(messages)


def _upstream_failure(*exchanges: str) -> HTTPException:
    names = "、".join(dict.fromkeys(EXCHANGE_LABELS[exchange] for exchange in exchanges))
    return HTTPException(502, f"交易所数据获取失败（{names}）")


async def stop_cleanup_tasks(app: FastAPI) -> None:
    await asyncio.gather(
        query_utils.stop_cleanup_tasks(
            getattr(app.state, "mobile_pair_spread_cleanup_tasks", ()),
            timeout=CLEANUP_SHUTDOWN_SECONDS, logger=logger, label="价差查询",
        ),
        query_utils.stop_cleanup_tasks(
            getattr(app.state, "mobile_premium_index_cleanup_tasks", ()),
            timeout=CLEANUP_SHUTDOWN_SECONDS, logger=logger, label="溢价指数查询",
        ),
    )


class _PairSpreadLimits(query_utils.MobileQueryLimits):
    def __init__(self, cleanup_tasks: set[asyncio.Task]):
        super().__init__(
            cleanup_tasks, logger=logger, label="价差查询", log_upstream=_log_upstream,
            clock=lambda: monotonic(),
        )

    async def run_upstream(
        self,
        request: Request,
        name: str,
        started: float,
        operation: Callable[[PairSpreadQueryService], Coroutine[Any, Any, _Result]],
    ) -> _Result:
        factory = getattr(request.app.state, "pair_spread_query_service_factory", None) or PairSpreadQueryService
        return await self.run(
            request, name, started, factory, operation, wait_for=wait,
            query_timeout=QUERY_TIMEOUT_SECONDS, close_timeout=CLOSE_TIMEOUT_SECONDS,
            max_upstream_tasks=MAX_UPSTREAM_TASKS,
        )


def _limits(request: Request) -> _PairSpreadLimits:
    state = request.app.state
    limits = getattr(state, "mobile_pair_spread_limits", None)
    if limits is None:
        state.mobile_pair_spread_cleanup_tasks = set()
        state.mobile_pair_spread_limits = limits = _PairSpreadLimits(state.mobile_pair_spread_cleanup_tasks)
    return limits


def _downsample(points: list[PairSpreadPoint]) -> list[PairSpreadPoint]:
    return query_utils.downsample(points, timestamp=attrgetter("bucket_at"), value=attrgetter("spread_pct"))


def _funding_history(
    funding: dict[Literal["leg1", "leg2"], list[PairSpreadFundingPoint]],
) -> list[MobilePairSpreadFundingPoint]:
    tagged = [(leg, point) for leg, points in funding.items() for point in points]
    tagged.sort(key=lambda entry: entry[1].funding_time)
    return [
        MobilePairSpreadFundingPoint(funding_time=point.funding_time, leg=leg, funding_rate_pct=point.funding_rate_pct)
        for leg, point in tagged[-MAX_FUNDING_HISTORY:]
    ]


def _mobile_stats(stats: PairSpreadValueStats) -> MobilePairSpreadStats:
    return MobilePairSpreadStats(min=stats.min, max=stats.max, mean=stats.mean, last=stats.current)


def _mobile_result(
    result: PairSpreadQueryResult,
    funding_history: list[MobilePairSpreadFundingPoint],
) -> MobilePairSpreadResult:
    return MobilePairSpreadResult(
        leg1=result.leg1,
        leg2=result.leg2,
        leg2_multiplier=result.leg2_multiplier,
        hours=result.hours,
        interval_seconds=result.interval_seconds,
        observed_at=result.observed_at,
        complete=result.complete,
        warnings=_mobile_warnings(result.warnings),
        spread_pct=_mobile_stats(result.spread_pct),
        spread_abs=_mobile_stats(result.spread_abs),
        current=result.current,
        points=[
            MobilePairSpreadPoint(t=p.bucket_at, a=p.leg1_close, b=p.leg2_close, pct=p.spread_pct)
            for p in _downsample(result.points)
        ],
        funding=MobilePairSpreadFunding(
            leg1_rate_pct=result.current.leg1.funding_rate_pct if result.current else None,
            leg2_rate_pct=result.current.leg2.funding_rate_pct if result.current else None,
            funding_history=funding_history,
        ),
        raw_point_count=len(result.points),
    )


@router.get("/query")
async def query_pair_spread(
    request: Request,
    leg1_exchange: str = Query(...),
    leg1_symbol: str = Query(...),
    leg1_market_type: MarketType = Query(default=MarketType.FUTURE),
    leg1_dex: str | None = Query(default=None),
    leg2_exchange: str = Query(...),
    leg2_symbol: str = Query(...),
    leg2_market_type: MarketType = Query(default=MarketType.FUTURE),
    leg2_dex: str | None = Query(default=None),
    hours: int = Query(default=72, ge=PAIR_SPREAD_MIN_HOURS),
    interval_minutes: int = Query(default=1),
    interval_seconds: int | None = Query(default=None),
    leg2_multiplier: float = Query(default=1.0, gt=0),
    end_at: datetime | None = Query(default=None),
    include_current: bool = Query(default=True),
) -> MobilePairSpreadResult:
    async def fetch(service: PairSpreadQueryService) -> MobilePairSpreadResult:
        # Match the web route's interval validation before calling the shared service.
        if interval_seconds is None and interval_minutes not in PAIR_SPREAD_INTERVAL_OPTIONS:
            allowed = ", ".join(str(value) for value in PAIR_SPREAD_INTERVAL_OPTIONS)
            raise HTTPException(422, f"interval_minutes must be one of: {allowed}")
        resolved_interval = interval_seconds if interval_seconds is not None else interval_minutes * 60
        if not PAIR_SPREAD_MIN_INTERVAL_SECONDS <= resolved_interval <= PAIR_SPREAD_MAX_INTERVAL_SECONDS:
            raise HTTPException(
                422,
                f"interval_seconds must be between {PAIR_SPREAD_MIN_INTERVAL_SECONDS} "
                f"and {PAIR_SPREAD_MAX_INTERVAL_SECONDS}",
            )
        resolver = await web._symbol_alias_resolver(request)
        try:
            leg1, alias1 = web._resolved_leg(
                resolver, exchange=leg1_exchange, symbol=leg1_symbol,
                market_type=leg1_market_type, dex=leg1_dex,
            )
            leg2, alias2 = web._resolved_leg(
                resolver, exchange=leg2_exchange, symbol=leg2_symbol,
                market_type=leg2_market_type, dex=leg2_dex,
            )
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc

        # The service merges and sorts the two histories before returning. Capture
        # them at the fetch boundary, where each request leg is still distinct.
        funding: dict[Literal["leg1", "leg2"], list[PairSpreadFundingPoint]] = {"leg1": [], "leg2": []}
        fetch_funding = service._fetch_funding_with_warning

        async def collect_funding(
            leg: PairSpreadLegQuery, start: datetime, end: datetime, warnings: list[str],
        ) -> list[PairSpreadFundingPoint]:
            points = await fetch_funding(leg, start, end, warnings)
            if leg is leg1:
                funding["leg1"].extend(points)
            elif leg is leg2:
                funding["leg2"].extend(points)
            return points

        service._fetch_funding_with_warning = collect_funding
        try:
            result = await service.query(
                leg1, leg2, hours=hours, interval_minutes=interval_minutes,
                interval_seconds=resolved_interval, leg2_multiplier=leg2_multiplier,
                now=end_at, include_current=include_current,
            )
        except PairSpreadQueryError as exc:
            _log_upstream(str(exc))
            raise _upstream_failure(leg1.exchange, leg2.exchange) from None
        finally:
            service._fetch_funding_with_warning = fetch_funding
        display_result = apply_pair_spread_symbol_aliases(
            result.model_copy(update={"funding_history": []}), leg1_alias=alias1, leg2_alias=alias2,
        )
        return _mobile_result(display_result, _funding_history(funding))

    limits = _limits(request)
    with limits.query(request.state.auth_session_hash) as started:
        return await limits.run_upstream(request, "query", started, fetch)


@router.get("/current")
async def current_pair_spread(
    request: Request,
    leg1_exchange: str = Query(...),
    leg1_symbol: str = Query(...),
    leg1_market_type: MarketType = Query(default=MarketType.FUTURE),
    leg1_dex: str | None = Query(default=None),
    leg2_exchange: str = Query(...),
    leg2_symbol: str = Query(...),
    leg2_market_type: MarketType = Query(default=MarketType.FUTURE),
    leg2_dex: str | None = Query(default=None),
    leg2_multiplier: float = Query(default=1.0, gt=0, allow_inf_nan=False),
) -> MobilePairSpreadCurrent:
    limits = _limits(request)
    started = limits.start(request.state.auth_session_hash, "current")
    resolver = await web._symbol_alias_resolver(request)
    try:
        leg1, alias1 = web._resolved_leg(
            resolver, exchange=leg1_exchange, symbol=leg1_symbol,
            market_type=leg1_market_type, dex=leg1_dex,
        )
        leg2, alias2 = web._resolved_leg(
            resolver, exchange=leg2_exchange, symbol=leg2_symbol,
            market_type=leg2_market_type, dex=leg2_dex,
        )
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc

    async def fetch(service: PairSpreadQueryService) -> MobilePairSpreadCurrent:
        observed_at = utc_now()
        warnings: list[str] = []
        try:
            current1, current2 = await asyncio.gather(
                service._fetch_current_with_warning(leg1, warnings),
                service._fetch_current_with_warning(leg2, warnings),
            )
        except PairSpreadQueryError as exc:
            _log_upstream(str(exc))
            raise _upstream_failure(leg1.exchange, leg2.exchange) from None
        finally:
            for warning in warnings:
                _log_upstream(warning)
        if current1 is None or current2 is None:
            failed = [
                leg.exchange
                for leg, current in ((leg1, current1), (leg2, current2))
                if current is None
            ]
            raise _upstream_failure(*failed)

        snapshot = service._build_current_snapshot(
            scale_leg_for_alias(current1, alias1),
            scale_leg_for_alias(scale_leg_by_divisor(current2, leg2_multiplier), alias2),
            observed_at,
        )
        return MobilePairSpreadCurrent.model_validate(snapshot)

    return await limits.run_upstream(request, "current", started, fetch)
