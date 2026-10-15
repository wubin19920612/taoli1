from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.market_identity import BindingMutation, MarketKey, MarketValidationSettings

router = APIRouter(prefix="/market-identities")


def service(request):
    result = getattr(request.app.state, "market_validation", None)
    if result is None:
        raise HTTPException(status_code=503, detail="市场核验服务未就绪")
    return result


def authorize(request, password):
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)


@router.get("")
async def bindings(
    request: Request,
    search: str = "",
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
):
    validator = service(request)
    rows = await validator.repository.list_bindings()
    now = datetime.now(UTC)
    from app.models.market_identity import utc

    rows = [
        r.model_copy(update={"identity_status": "expired"})
        if r.identity_status == "verified" and r.valid_until and utc(r.valid_until) <= now
        else r
        for r in rows
    ]
    if search.strip():
        term = search.strip().casefold()
        rows = [
            r
            for r in rows
            if term
            in f"{r.market.exchange} {r.market.raw_symbol} {r.asset_id} {r.display_name}".casefold()
        ]
    if status:
        rows = [r for r in rows if r.identity_status == status]
    catalog = getattr(request.app.state, "market_identity_catalog", None)
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "coverage": validator.coverage(),
        "settings": validator.settings,
        "catalog": catalog.status if catalog else None,
    }


@router.get("/settings")
async def settings(request: Request):
    return service(request).settings


@router.put("/settings")
async def update_settings(
    value: MarketValidationSettings,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
):
    authorize(request, password)
    validator = service(request)
    await validator.repository.save_settings(value)
    await validator.refresh()
    _refresh_store(validator)
    return value


def _refresh_store(validator):
    validator.store.set_all_markets(
        [validator.annotate(row) for row in validator.store.get_all_markets()]
    )
    validator.store.set_markets([validator.annotate(row) for row in validator.store.get_markets()])
    # Invalidate old opportunity capabilities immediately. The collector will
    # repopulate using the new registry rather than serving stale privileges.
    validator.store.set_opportunities([])


@router.post("/binding-preview")
async def binding_preview(
    value: BindingMutation,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
):
    authorize(request, password)
    validator = service(request)
    key = value.binding.market
    affected = [
        o
        for o in validator.store.get_opportunities()
        if (o.buy_exchange, o.buy_market_type, o.buy_raw_symbol)
        == (key.exchange, key.market_type, key.raw_symbol)
        or (o.sell_exchange, o.sell_market_type, o.sell_raw_symbol)
        == (key.exchange, key.market_type, key.raw_symbol)
    ]
    return {
        "market": key,
        "affected_opportunities": len(affected),
        "next_revision": value.expected_revision + 1,
        "message": "提交后旧资格立即失效；已有外部卡片不会自动修改。",
    }


@router.put("/binding")
async def update_binding(
    value: BindingMutation,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
):
    authorize(request, password)
    validator = service(request)
    try:
        saved = await validator.repository.save_binding(value)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await validator.refresh()
    _refresh_store(validator)
    return saved


@router.get("/audit")
async def audit(request: Request):
    return await service(request).repository.audit_events()


@router.get("/incidents")
async def incidents(request: Request):
    return await service(request).repository.incidents()


class ReviewRequest(BaseModel):
    buy: MarketKey
    sell: MarketKey


@router.post("/review")
async def review(
    value: ReviewRequest,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
):
    authorize(request, password)
    validator = service(request)
    rows = validator.store.get_all_markets()
    legs = []
    for key in (value.buy, value.sell):
        matches = [row for row in rows if MarketKey.from_snapshot(row) == key]
        if not matches:
            raise HTTPException(status_code=404, detail="没有精确市场行情")
        legs.append(max(matches, key=lambda row: row.timestamp))
    risk = await request.app.state.settings_repo.get_risk_settings()
    try:
        result, _, _ = await validator.review(*legs, risk, twice=True)
        return result
    except (ValueError, RuntimeError, TimeoutError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=409, detail=f"盘口复核失败：{exc}") from exc


@router.get("/card-audit")
async def card_audit(request: Request, password: str | None = Depends(dashboard_password_header)):
    authorize(request, password)
    validator = service(request)
    client = getattr(request.app.state, "astro_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Astro 连接未就绪")
    try:
        cards = await client.list_pairs()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="读取 Astro 卡片失败") from exc
    return validator.audit_cards(cards)
