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
    text_field,
)
from app.services.pair_spread_query import PairSpreadQueryError, PairSpreadQueryService

router = APIRouter(prefix="/mobile")


@router.get("/summary", response_model=MobileSummary)
async def mobile_summary(request: Request):
    return await summary(request, await list_astro_pairs(request))


@router.get("/cards/by-id/{card_id}/history", response_model=CardHistory)
async def card_history_by_id(card_id: str, request: Request, hours: int = Query(24, ge=1, le=72)):
    for pair in await list_astro_pairs(request):
        if isinstance(pair, dict) and pair.get("id") == card_id:
            return await _history_for_pair(request, pair, hours)
    raise HTTPException(404, "找不到卡片")


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


async def _history_for_pair(request: Request, pair: dict, hours: int) -> CardHistory:
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
                dex=(leg.dex or "main") if leg.exchange == "hyperliquid" else None,
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
