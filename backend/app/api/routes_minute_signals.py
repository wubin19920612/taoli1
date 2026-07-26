import logging

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.minute_signal_scan import (
    MinuteSignalAlertEngine,
    MinuteSignalScanService,
    build_minute_signal_alert_message,
)

router = APIRouter(prefix="/minute-signals")
logger = logging.getLogger(__name__)


async def _send_scan_all_alert_if_needed(request: Request, result: dict) -> None:
    notifier = getattr(request.app.state, "feishu_notifier", None)
    webhook_url = getattr(getattr(notifier, "config", None), "webhook_url", "")
    if notifier is None or not webhook_url:
        return
    engine = getattr(request.app.state, "minute_signal_alert_engine", None)
    if engine is None:
        engine = MinuteSignalAlertEngine()
        request.app.state.minute_signal_alert_engine = engine
    matches = engine.evaluate(result)
    if not matches:
        return
    try:
        await notifier.send_text(build_minute_signal_alert_message(matches, result))
    except Exception as exc:  # noqa: BLE001 - keep the scan response usable when Feishu fails.
        for candidate in matches:
            engine.release_failed(candidate)
        logger.exception("minute signal Feishu notification failed")
        warnings = result.setdefault("warnings", [])
        if isinstance(warnings, list):
            warnings.append(f"飞书一分钟价差信号告警发送失败: {exc}")


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
        raise HTTPException(status_code=502, detail=f"分钟信号扫描失败: {exc}") from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()


@router.get("/scan-all")
async def scan_all_minute_signals(
    request: Request,
    hours: int = Query(default=4, ge=1, le=24),
    max_symbols: int = Query(default=30, ge=5, le=100),
    min_volume_24h_usdt: float = Query(default=100_000, ge=0),
) -> dict:
    service_factory = getattr(request.app.state, "minute_signal_scan_service_factory", None)
    service = service_factory() if service_factory is not None else MinuteSignalScanService()
    try:
        result = await service.scan_all(
            hours=hours,
            max_symbols=max_symbols,
            min_volume_24h_usdt=min_volume_24h_usdt,
        )
        await _send_scan_all_alert_if_needed(request, result)
        return result
    except Exception as exc:  # noqa: BLE001 - surface discovery failures to the dashboard.
        raise HTTPException(status_code=502, detail=f"全市场分钟信号扫描失败: {exc}") from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()
