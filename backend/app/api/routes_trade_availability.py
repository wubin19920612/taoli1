from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.market import MarketType
from app.models.trade_availability import (
    TradeAvailabilityResult,
    TradeAvailabilityWatch,
    TradeAvailabilityWatchCreate,
)
from app.services.trade_availability import (
    TradeAvailabilityService,
    TradeAvailabilityWatchRepository,
)

router = APIRouter(prefix="/trade-status")


def _service(request: Request) -> TradeAvailabilityService:
    service = getattr(request.app.state, "trade_availability_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="交易可用性诊断服务尚未就绪")
    return service


def _repository(request: Request) -> TradeAvailabilityWatchRepository:
    repository = getattr(request.app.state, "trade_availability_watch_repo", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="交易可用性监控仓库尚未就绪")
    return repository


def _verify_write(request: Request, password: str | None) -> None:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)


@router.get("/watches/list", response_model=list[TradeAvailabilityWatch])
async def list_watches(request: Request) -> list[TradeAvailabilityWatch]:
    return await _repository(request).list()


@router.post("/watches", response_model=TradeAvailabilityWatch)
async def create_watch(
    payload: TradeAvailabilityWatchCreate,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> TradeAvailabilityWatch:
    _verify_write(request, password)
    exchange = payload.exchange.strip().lower()
    raw_symbol = payload.raw_symbol.strip()
    dex = payload.dex.strip().lower() if payload.dex and payload.dex.strip() else None
    result = await _service(request).fetch_status(
        payload.symbol,
        exchange=exchange,
        market_type=payload.market_type,
        raw_symbol=raw_symbol,
        dex=dex,
    )
    market = next(
        (
            item
            for item in result.markets
            if item.exchange == exchange
            and item.market_type == payload.market_type
            and item.raw_symbol.upper() == raw_symbol.upper()
            and (item.dex or "") == (dex or "")
        ),
        None,
    )
    if market is None:
        raise HTTPException(status_code=404, detail="未找到指定交易所原始市场")
    repository = _repository(request)
    existing = next(
        (
            item
            for item in await repository.list()
            if item.exchange == exchange
            and item.market_type == payload.market_type
            and item.raw_symbol.upper() == raw_symbol.upper()
            and (item.dex or "") == (dex or "")
        ),
        None,
    )
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "symbol": market.symbol,
        "exchange": market.exchange,
        "market_type": market.market_type,
        "raw_symbol": market.raw_symbol,
        "dex": market.dex,
        "monitor_buy": payload.monitor_buy,
        "monitor_sell": payload.monitor_sell,
        "last_buy_state": market.buy_open.state,
        "last_sell_state": market.sell_open.state,
        "last_checked_at": now,
        "created_at": existing.created_at if existing else now,
        "updated_at": now,
    }
    if existing is not None:
        values["id"] = existing.id
    return await repository.upsert(TradeAvailabilityWatch(**values))


@router.delete("/watches/{watch_id}")
async def delete_watch(
    watch_id: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> dict[str, str]:
    _verify_write(request, password)
    await _repository(request).delete(watch_id)
    return {"status": "deleted"}


@router.get("/{symbol}", response_model=TradeAvailabilityResult)
async def get_trade_status(
    symbol: str,
    request: Request,
    exchange: str | None = None,
    market_type: MarketType | None = None,
    raw_symbol: str | None = None,
    dex: str | None = None,
) -> TradeAvailabilityResult:
    try:
        return await _service(request).fetch_status(
            symbol,
            exchange=exchange,
            market_type=market_type,
            raw_symbol=raw_symbol,
            dex=dex,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
