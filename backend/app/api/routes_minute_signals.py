from fastapi import APIRouter, HTTPException, Query, Request

from app.services.minute_signal_scan import MinuteSignalScanService

router = APIRouter(prefix="/minute-signals")


@router.get("/scan")
async def scan_minute_signals(
    request: Request,
    symbol: str = Query(default="AKEUSDT", min_length=3, max_length=40),
    alpha_symbol: str = Query(default="ALPHA_331USDT", min_length=3, max_length=60),
    hours: int = Query(default=4, ge=1, le=24),
) -> dict:
    service_factory = getattr(request.app.state, "minute_signal_scan_service_factory", None)
    service = service_factory() if service_factory is not None else MinuteSignalScanService()
    try:
        return await service.scan_symbol(
            alpha_symbol=alpha_symbol.strip().upper(),
            futures_symbol=symbol.strip().upper(),
            hours=hours,
        )
    except Exception as exc:  # noqa: BLE001 - expose a useful upstream failure to the dashboard.
        raise HTTPException(status_code=502, detail=f"Minute signal scan failed: {exc}") from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()
