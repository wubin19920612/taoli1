import logging

from fastapi import APIRouter, HTTPException, Query, Request

from app.models.settings import MinuteSignalSettings
from app.services.minute_signal_scan import (
    MinuteSignalAlertEngine,
    MinuteSignalScanService,
    build_minute_signal_alert_message,
)

router = APIRouter(prefix="/minute-signals")
logger = logging.getLogger(__name__)


async def _minute_signal_settings(request: Request) -> MinuteSignalSettings:
    repo = getattr(request.app.state, "settings_repo", None)
    get_settings = getattr(repo, "get_minute_signal_settings", None)
    if get_settings is None:
        return MinuteSignalSettings()
    return await get_settings()


async def _send_scan_all_alert_if_needed(
    request: Request,
    result: dict,
    *,
    alert_cooldown_seconds: int,
) -> None:
    notifier = getattr(request.app.state, "feishu_notifier", None)
    webhook_url = getattr(getattr(notifier, "config", None), "webhook_url", "")
    if notifier is None or not webhook_url:
        return
    engine = getattr(request.app.state, "minute_signal_alert_engine", None)
    if engine is None:
        engine = MinuteSignalAlertEngine()
        request.app.state.minute_signal_alert_engine = engine
    matches = engine.evaluate(result, alert_cooldown_seconds=alert_cooldown_seconds)
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
    hours: int | None = Query(default=None, ge=1, le=24),
) -> dict:
    settings = await _minute_signal_settings(request)
    service_factory = getattr(request.app.state, "minute_signal_scan_service_factory", None)
    service = service_factory() if service_factory is not None else MinuteSignalScanService()
    try:
        return await service.scan_symbol(
            alpha_symbol=alpha_symbol.strip().upper(),
            futures_symbol=symbol.strip().upper(),
            hours=hours if hours is not None else settings.hours,
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
    hours: int | None = Query(default=None, ge=1, le=24),
    max_symbols: int | None = Query(default=None, ge=5, le=100),
    min_volume_24h_usdt: float | None = Query(default=None, ge=0),
    alert_cooldown_minutes: int | None = Query(default=None, ge=1, le=10_080),
    max_entry_basis_bps: float | None = Query(default=None, ge=-10_000, le=10_000),
    require_negative_premium_when_spot_above: bool | None = Query(default=None),
    max_premium_when_spot_above_bps: float | None = Query(default=None, le=0),
) -> dict:
    settings = await _minute_signal_settings(request)
    scan_hours = hours if hours is not None else settings.hours
    scan_max_symbols = max_symbols if max_symbols is not None else settings.max_symbols
    scan_min_volume = (
        min_volume_24h_usdt
        if min_volume_24h_usdt is not None
        else settings.min_volume_24h_usdt
    )
    scan_alert_cooldown = (
        alert_cooldown_minutes
        if alert_cooldown_minutes is not None
        else settings.alert_cooldown_minutes
    )
    scan_max_entry_basis = (
        max_entry_basis_bps
        if max_entry_basis_bps is not None
        else settings.max_entry_basis_bps
    )
    scan_require_premium = (
        require_negative_premium_when_spot_above
        if require_negative_premium_when_spot_above is not None
        else settings.require_negative_premium_when_spot_above
    )
    scan_max_premium = (
        max_premium_when_spot_above_bps
        if max_premium_when_spot_above_bps is not None
        else settings.max_premium_when_spot_above_bps
    )
    service_factory = getattr(request.app.state, "minute_signal_scan_service_factory", None)
    service = service_factory() if service_factory is not None else MinuteSignalScanService()
    try:
        result = await service.scan_all(
            hours=scan_hours,
            max_symbols=scan_max_symbols,
            min_volume_24h_usdt=scan_min_volume,
            max_entry_basis_bps=scan_max_entry_basis,
            require_negative_premium_when_spot_above=scan_require_premium,
            max_premium_when_spot_above_bps=scan_max_premium,
        )
        result["alert_cooldown_minutes"] = scan_alert_cooldown
        await _send_scan_all_alert_if_needed(
            request,
            result,
            alert_cooldown_seconds=scan_alert_cooldown * 60,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - surface discovery failures to the dashboard.
        raise HTTPException(status_code=502, detail=f"全市场分钟信号扫描失败: {exc}") from exc
    finally:
        close = getattr(service, "aclose", None)
        if close is not None:
            await close()
