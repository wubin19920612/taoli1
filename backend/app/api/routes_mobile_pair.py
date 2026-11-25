import asyncio
import logging
import re
from asyncio import wait
from collections.abc import Callable, Coroutine
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from itertools import groupby
from operator import attrgetter
from time import monotonic
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from app.api import routes_pair_spread as web
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
    HYPERLIQUID_MAIN_DEX,
    PAIR_SPREAD_INTERVAL_OPTIONS,
    PAIR_SPREAD_MAX_INTERVAL_SECONDS,
    PAIR_SPREAD_MIN_HOURS,
    PAIR_SPREAD_MIN_INTERVAL_SECONDS,
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
    _scale_current_leg as scale_leg_by_divisor,
)
from app.services.symbol_aliases import _scale_current_leg as scale_leg_for_alias
from app.services.symbol_aliases import apply_pair_spread_symbol_aliases

router = APIRouter(prefix="/pair-spread", dependencies=[Depends(require_session)])
logger = logging.getLogger(__name__)
QUERY_TIMEOUT_SECONDS = 60
CLOSE_TIMEOUT_SECONDS = 10
MAX_UPSTREAM_TASKS = 32
MIN_START_INTERVAL_SECONDS = 3
MAX_POINTS = 800
MAX_FUNDING_HISTORY = 200
_Result = TypeVar("_Result")

_SENSITIVE_UPSTREAM_TEXT = re.compile(
    r"""(?ix)
    -----BEGIN\s+[^\r\n-]*PRIVATE\s+KEY-----[\s\S]*
    |(?:[a-z][a-z0-9+.-]*://|(?<!\w)/[^/\s]|\?)[^\s'"<>]*
    |\b(?:bearer|basic)\s+[^\s,;'"<>]+
    |\b(?:[\w-]*(?:key|token|secret|signature|password|passphrase|cookie|authorization|webhook)|sig)
        ['"]?\s*[:=][\s\S]*
    """
)
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
    logger.warning("Mobile pair spread upstream: %r", _SENSITIVE_UPSTREAM_TEXT.sub("[已隐藏]", message)[:1000])


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


async def _close_service(service: PairSpreadQueryService) -> None:
    close = getattr(service, "aclose", None)
    if close is not None:
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
                await close()
        except (Exception, asyncio.CancelledError) as exc:
            _log_upstream(f"清理交易所连接失败: {type(exc).__name__}: {exc}")


async def _wait_for_disconnect(request: Request) -> None:
    # Both endpoints are GETs with no request body consumed by the handler.
    while (await request.receive())["type"] != "http.disconnect":
        pass


class _PairSpreadLimits:
    def __init__(self):
        self._active_queries: set[str] = set()
        self._starts: dict[tuple[str, str], float] = {}
        self._cleanup_at = 0.0
        self._upstream_tasks: set[asyncio.Task] = set()

    def start(self, session: str, operation: str) -> float:
        now = monotonic()
        cutoff = now - MIN_START_INTERVAL_SECONDS
        if now >= self._cleanup_at:
            self._starts = {key: start for key, start in self._starts.items() if start > cutoff}
            self._cleanup_at = now + MIN_START_INTERVAL_SECONDS
        key = (session, operation)
        if self._starts.get(key, cutoff) > cutoff:
            raise HTTPException(429, "请求过于频繁，请至少间隔 3 秒")
        self._starts[key] = now
        return now

    @contextmanager
    def query(self, session: str):
        if session in self._active_queries:
            raise HTTPException(429, "上一个查询还在进行")
        started = self.start(session, "query")
        self._active_queries.add(session)
        try:
            yield started
        finally:
            self._active_queries.remove(session)

    def _finish_upstream(self, service: PairSpreadQueryService, task: asyncio.Task) -> None:
        # Retrieve late errors even when the caller has already returned a 504.
        if not task.cancelled():
            task.exception()
        self._upstream_tasks.remove(task)
        cleanup = asyncio.create_task(_close_service(service))
        self._upstream_tasks.add(cleanup)
        cleanup.add_done_callback(self._upstream_tasks.discard)

    async def run_upstream(
        self,
        request: Request,
        name: str,
        started: float,
        operation: Callable[[PairSpreadQueryService], Coroutine[Any, Any, _Result]],
    ) -> _Result:
        # Count running and cancelled operations until their cleanup finishes.
        if len(self._upstream_tasks) >= MAX_UPSTREAM_TASKS:
            raise HTTPException(503, "交易所查询繁忙，请稍后重试")
        factory = getattr(request.app.state, "pair_spread_query_service_factory", None) or PairSpreadQueryService
        service = factory()
        task = asyncio.create_task(operation(service))
        self._upstream_tasks.add(task)
        task.add_done_callback(partial(self._finish_upstream, service))
        key = (request.state.auth_session_hash, name)
        disconnected = asyncio.create_task(_wait_for_disconnect(request))
        try:
            done, _ = await wait({task, disconnected}, timeout=QUERY_TIMEOUT_SECONDS,
                                 return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done:
                disconnected.result()
                if self._starts.get(key) == started:
                    self._starts.pop(key)
                raise HTTPException(499, "客户端已断开连接")
            if task not in done:
                raise HTTPException(504, "查询超时，可缩短时长或加大周期")
            return task.result()
        except asyncio.CancelledError:
            if self._starts.get(key) == started:
                self._starts.pop(key)
            raise
        finally:
            if not task.done():
                task.cancel()
            if not disconnected.done():
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)


def _limits(request: Request) -> _PairSpreadLimits:
    state = request.app.state
    limits = getattr(state, "mobile_pair_spread_limits", None)
    if limits is None:
        state.mobile_pair_spread_limits = limits = _PairSpreadLimits()
    return limits


def _downsample(points: list[PairSpreadPoint]) -> list[PairSpreadPoint]:
    if not points:
        return []
    timestamp = attrgetter("bucket_at")
    spread = attrgetter("spread_pct")

    def magnitude(point: PairSpreadPoint) -> float:
        return abs(point.spread_pct)

    points = sorted(points, key=timestamp)
    if len(points) <= MAX_POINTS:
        selected = {at: max(group, key=magnitude) for at, group in groupby(points, key=timestamp)}
    else:
        first, last = points[0], points[-1]
        start = first.bucket_at
        span = max((last.bucket_at - start).total_seconds(), 1e-6)
        # Reserve slots for both endpoints and both global extrema.
        bucket_count = (MAX_POINTS - 4) // 2

        def bucket(point: PairSpreadPoint) -> int:
            elapsed = (point.bucket_at - start).total_seconds()
            return min(bucket_count - 1, int(elapsed / span * bucket_count))

        selected = {first.bucket_at: first, last.bucket_at: last}
        for _, group in groupby(points, key=bucket):
            members = list(group)
            extrema = (min(members, key=spread), max(members, key=spread))
            for point in sorted(extrema, key=magnitude):
                selected[point.bucket_at] = point

    # Prefer distinct timestamps for tied extrema. If both can only be represented
    # at one timestamp, keep the larger absolute spread.
    low = min(points, key=spread)
    high = max(points, key=lambda point: (point.spread_pct, point.bucket_at != low.bucket_at))
    if low.bucket_at == high.bucket_at:
        low = min(points, key=lambda point: (point.spread_pct, point.bucket_at == high.bucket_at))
    for point in sorted((low, high), key=magnitude):
        selected[point.bucket_at] = point
    return [selected[at] for at in sorted(selected)]


def _funding_history(result: PairSpreadQueryResult) -> list[MobilePairSpreadFundingPoint]:
    leg_names: tuple[Literal["leg1"], Literal["leg2"]] = ("leg1", "leg2")
    legs: dict[tuple[str, str | None, str], Literal["leg1", "leg2"]] = {}
    for name, leg in zip(leg_names, (result.leg1, result.leg2), strict=True):
        if leg.market_type == MarketType.FUTURE:
            dex = (leg.dex or HYPERLIQUID_MAIN_DEX) if leg.exchange == "hyperliquid" else leg.dex
            legs.setdefault((leg.exchange, dex, leg.symbol), name)
    history = []
    for point in reversed(sorted(result.funding_history, key=attrgetter("funding_time"))):
        dex = (point.dex or HYPERLIQUID_MAIN_DEX) if point.exchange == "hyperliquid" else point.dex
        leg = legs.get((point.exchange, dex, point.symbol))
        if leg is not None:
            history.append(MobilePairSpreadFundingPoint(
                funding_time=point.funding_time, leg=leg, funding_rate_pct=point.funding_rate_pct,
            ))
            if len(history) == MAX_FUNDING_HISTORY:
                break
    history.reverse()
    return history


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
        try:
            result = await service.query(
                leg1, leg2, hours=hours, interval_minutes=interval_minutes,
                interval_seconds=resolved_interval, leg2_multiplier=leg2_multiplier,
                now=end_at, include_current=include_current,
            )
        except PairSpreadQueryError as exc:
            _log_upstream(str(exc))
            raise _upstream_failure(leg1.exchange, leg2.exchange) from None
        funding_history = _funding_history(result)
        display_result = apply_pair_spread_symbol_aliases(
            result.model_copy(update={"funding_history": []}), leg1_alias=alias1, leg2_alias=alias2,
        )
        return _mobile_result(display_result, funding_history)

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
