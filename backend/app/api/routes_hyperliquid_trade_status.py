from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.hyperliquid_trade_status import (
    HyperliquidTradeStatusResult,
    HyperliquidTradeStatusWatch,
    HyperliquidTradeStatusWatchCreate,
)
from app.services.hyperliquid_trade_status import (
    HyperliquidTradeStatusError,
    HyperliquidTradeStatusService,
    HyperliquidTradeStatusWatchRepository,
    hyperliquid_dex_from_raw_symbol,
    normalize_hyperliquid_dex,
)

router = APIRouter(prefix="/hyperliquid/trade-status")


def _service(request: Request) -> HyperliquidTradeStatusService:
    service = getattr(request.app.state, "hyperliquid_trade_status_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Hyperliquid trade status service is not ready")
    return service


def _repository(request: Request) -> HyperliquidTradeStatusWatchRepository:
    repository = getattr(request.app.state, "hyperliquid_trade_status_watch_repo", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Hyperliquid trade status watch repository is not ready")
    return repository


def _verify_write(request: Request, password: str | None) -> None:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)


@router.get("/{symbol}", response_model=HyperliquidTradeStatusResult)
async def get_trade_status(
    symbol: str,
    request: Request,
    dex: str | None = None,
    raw_symbol: str | None = None,
) -> HyperliquidTradeStatusResult:
    try:
        return await _service(request).fetch_status(symbol, dex=dex, raw_symbol=raw_symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HyperliquidTradeStatusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/watches/list", response_model=list[HyperliquidTradeStatusWatch])
async def list_watches(request: Request) -> list[HyperliquidTradeStatusWatch]:
    return await _repository(request).list()


@router.post("/watches", response_model=HyperliquidTradeStatusWatch)
async def create_watch(
    payload: HyperliquidTradeStatusWatchCreate,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> HyperliquidTradeStatusWatch:
    _verify_write(request, password)
    dex = normalize_hyperliquid_dex(payload.dex or hyperliquid_dex_from_raw_symbol(payload.raw_symbol))
    result = await _service(request).fetch_status(
        payload.symbol,
        dex=dex,
        raw_symbol=payload.raw_symbol,
    )
    market = next(
        (
            item
            for item in result.markets
            if item.dex == dex and item.raw_symbol.upper() == payload.raw_symbol.upper()
        ),
        None,
    )
    if market is None:
        raise HTTPException(status_code=404, detail="未找到指定 Hyperliquid 原始市场")
    now = datetime.now(UTC)
    existing = next(
        (
            item
            for item in await _repository(request).list()
            if item.dex == dex and item.raw_symbol.upper() == payload.raw_symbol.upper()
        ),
        None,
    )
    watch_values: dict[str, object] = {
        "symbol": market.symbol,
        "dex": dex,
        "raw_symbol": market.raw_symbol,
        "monitor_buy": payload.monitor_buy,
        "monitor_sell": payload.monitor_sell,
        "last_buy_state": market.buy_open.state,
        "last_sell_state": market.sell_open.state,
        "last_checked_at": now,
        "created_at": existing.created_at if existing else now,
        "updated_at": now,
    }
    if existing:
        watch_values["id"] = existing.id
    watch = HyperliquidTradeStatusWatch(**watch_values)
    return await _repository(request).upsert(watch)


@router.delete("/watches/{watch_id}")
async def delete_watch(
    watch_id: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> dict[str, str]:
    _verify_write(request, password)
    await _repository(request).delete(watch_id)
    return {"status": "deleted"}
