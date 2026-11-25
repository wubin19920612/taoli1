from asyncio import gather, timeout
from contextlib import contextmanager
from datetime import datetime
from itertools import groupby
from operator import attrgetter
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from app.api import routes_pair_spread as web
from app.core.security import require_session
from app.exchanges.base import utc_now
from app.models.market import MarketType
from app.models.mobile import (
    MobilePairSpreadCurrent,
    MobilePairSpreadFunding,
    MobilePairSpreadPoint,
    MobilePairSpreadResult,
    MobilePairSpreadStats,
)
from app.models.pair_spread import (
    PAIR_SPREAD_MIN_HOURS,
    PairSpreadPoint,
    PairSpreadPreset,
    PairSpreadQueryResult,
    PairSpreadValueStats,
)
from app.services.pair_spread_query import (
    PairSpreadQueryError,
    PairSpreadQueryService,
    _scale_current_leg as scale_leg_by_divisor,
    _warnings_text,
)
from app.services.symbol_aliases import _scale_current_leg as scale_leg_for_alias

router = APIRouter(prefix="/pair-spread", dependencies=[Depends(require_session)])
QUERY_TIMEOUT_SECONDS = 60
MIN_START_INTERVAL_SECONDS = 3
MAX_POINTS = 800

router.add_api_route("/exchanges", web.list_pair_spread_exchanges, methods=["GET"])
router.add_api_route(
    "/presets", web.list_pair_spread_presets, methods=["GET"], response_model=list[PairSpreadPreset],
)


class _PairSpreadLimits:
    def __init__(self):
        self._active_queries: set[str] = set()
        self._starts: dict[tuple[str, str], float] = {}
        self._cleanup_at = 0.0

    def start(self, session: str, operation: str) -> None:
        now = monotonic()
        cutoff = now - MIN_START_INTERVAL_SECONDS
        if now >= self._cleanup_at:
            self._starts = {key: start for key, start in self._starts.items() if start > cutoff}
            self._cleanup_at = now + MIN_START_INTERVAL_SECONDS
        key = (session, operation)
        if self._starts.get(key, cutoff) > cutoff:
            raise HTTPException(429, "请求过于频繁，请至少间隔 3 秒")
        self._starts[key] = now

    @contextmanager
    def query(self, session: str):
        if session in self._active_queries:
            raise HTTPException(429, "上一个查询还在进行")
        self.start(session, "query")
        self._active_queries.add(session)
        try:
            yield
        finally:
            self._active_queries.remove(session)


def _limits(request: Request) -> _PairSpreadLimits:
    state = request.app.state
    limits = getattr(state, "mobile_pair_spread_limits", None)
    if limits is None:
        state.mobile_pair_spread_limits = limits = _PairSpreadLimits()
    return limits


def _downsample(points: list[PairSpreadPoint]) -> list[PairSpreadPoint]:
    if len(points) <= MAX_POINTS:
        return points

    points = sorted(points, key=attrgetter("bucket_at"))
    start = points[0].bucket_at
    span = max((points[-1].bucket_at - start).total_seconds(), 1e-6)

    # Reserve two slots for the endpoints, then take two extrema per time bucket.
    bucket_count = (MAX_POINTS - 2) // 2

    def bucket(index: int) -> int:
        elapsed = (points[index].bucket_at - start).total_seconds()
        return min(bucket_count - 1, int(elapsed / span * bucket_count))

    def spread(index: int) -> float:
        return points[index].spread_pct

    selected = {0, len(points) - 1}
    for _, group in groupby(range(len(points)), key=bucket):
        indices = list(group)
        selected.add(min(indices, key=spread))
        selected.add(max(indices, key=spread))
    return [points[index] for index in sorted(selected)]


def _mobile_stats(stats: PairSpreadValueStats) -> MobilePairSpreadStats:
    return MobilePairSpreadStats(min=stats.min, max=stats.max, mean=stats.mean, last=stats.current)


def _mobile_result(result: PairSpreadQueryResult) -> MobilePairSpreadResult:
    return MobilePairSpreadResult(
        leg1=result.leg1,
        leg2=result.leg2,
        leg2_multiplier=result.leg2_multiplier,
        hours=result.hours,
        interval_seconds=result.interval_seconds,
        observed_at=result.observed_at,
        complete=result.complete,
        warnings=result.warnings,
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
            funding_history=result.funding_history,
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
    with _limits(request).query(request.state.auth_session_hash):
        try:
            async with timeout(QUERY_TIMEOUT_SECONDS):
                result = await web.query_pair_spread(
                    request,
                    leg1_exchange=leg1_exchange,
                    leg1_symbol=leg1_symbol,
                    leg1_market_type=leg1_market_type,
                    leg1_dex=leg1_dex,
                    leg2_exchange=leg2_exchange,
                    leg2_symbol=leg2_symbol,
                    leg2_market_type=leg2_market_type,
                    leg2_dex=leg2_dex,
                    hours=hours,
                    interval_minutes=interval_minutes,
                    interval_seconds=interval_seconds,
                    leg2_multiplier=leg2_multiplier,
                    end_at=end_at,
                    include_current=include_current,
                )
        except TimeoutError:
            raise HTTPException(504, "查询超时，可缩短时长或加大周期") from None
        return _mobile_result(result)


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
    _limits(request).start(request.state.auth_session_hash, "current")
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

    factory = getattr(request.app.state, "pair_spread_query_service_factory", None) or PairSpreadQueryService
    service = factory()
    try:
        observed_at = utc_now()
        warnings: list[str] = []
        current1, current2 = await gather(
            service._fetch_current_with_warning(leg1, warnings),
            service._fetch_current_with_warning(leg2, warnings),
        )
        if current1 is None or current2 is None:
            suffix = f": {_warnings_text(warnings)}" if warnings else ""
            raise PairSpreadQueryError(f"没有拿到可用于实时采样的当前价格{suffix}")

        snapshot = service._build_current_snapshot(
            scale_leg_for_alias(current1, alias1),
            scale_leg_for_alias(scale_leg_by_divisor(current2, leg2_multiplier), alias2),
            observed_at,
        )
        return MobilePairSpreadCurrent.model_validate(snapshot)
    except PairSpreadQueryError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()
