from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from app.api.routes_astro import list_astro_pairs
from app.api.routes_pair_spread import _resolved_leg, _symbol_alias_resolver
from app.models.mobile import CardHistory, HistoryPoint, MobileAlert, MobileSummary
from app.services.mobile_summary import (
    condition,
    legs_for,
    ratio_mode,
    reference,
    spread_pct,
    summary,
)
from app.services.pair_spread_query import PairSpreadQueryError, PairSpreadQueryService

router = APIRouter(prefix="/mobile")


@router.get("/summary", response_model=MobileSummary)
async def mobile_summary(request: Request):
    return await summary(request, await list_astro_pairs(request))


@router.get("/cards/{name}/history", response_model=CardHistory)
async def card_history(name: str, request: Request, hours: int = Query(24, ge=1, le=72)):
    pairs = [pair for pair in await list_astro_pairs(request) if pair.get("name") == name]
    if not pairs:
        raise HTTPException(404, "找不到卡片")
    if len(pairs) != 1:
        raise HTTPException(409, "卡片名称重复，无法确定两腿")
    pair = pairs[0]
    route = legs_for(pair)
    if not route:
        raise HTTPException(422, "无法从卡片名称识别两腿标的")
    resolver = await _symbol_alias_resolver(request)
    try:
        (a, alias_a), (b, alias_b) = [
            _resolved_leg(
                resolver,
                exchange=leg.exchange,
                symbol=leg.symbol,
                market_type=leg.market_type,
                dex=leg.dex or None,
            )
            for leg in route
        ]
    except ValidationError as exc:
        raise HTTPException(422, "不支持该卡片的历史行情") from exc
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


@router.get("/alerts", response_model=list[MobileAlert])
async def alerts(
    request: Request,
    since: float = Query(0, ge=0, allow_inf_nan=False),
    limit: int = Query(100, ge=1, le=200),
):
    return await request.app.state.mobile_alert_repo.list(since, limit)
