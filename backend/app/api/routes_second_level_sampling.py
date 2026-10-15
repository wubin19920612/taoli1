from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.models.fat_finger_backtest import FatFingerBacktestRequest, FatFingerBacktestResult
from app.models.pair_spread import normalize_pair_spread_symbol
from app.models.second_level_sampling import (
    SUPPORTED_SECOND_LEVEL_EXCHANGES,
    SecondLevelIndexComponentSample,
    SecondLevelMarketSample,
    SecondLevelSamplingConfig,
    SecondLevelSamplingStatus,
)
from app.services.second_level_sampler import SecondLevelSampler
from app.services.fat_finger_backtest import run_fat_finger_backtest

router = APIRouter(prefix="/second-level-sampling")


def _sampler(request: Request) -> SecondLevelSampler:
    sampler = getattr(request.app.state, "second_level_sampler", None)
    if sampler is None:
        raise HTTPException(status_code=503, detail="Second-level sampler is not ready")
    return sampler


@router.get("/exchanges", response_model=list[str])
async def list_supported_second_level_exchanges() -> list[str]:
    return list(SUPPORTED_SECOND_LEVEL_EXCHANGES)


@router.get("/config", response_model=SecondLevelSamplingConfig)
async def get_second_level_sampling_config(request: Request) -> SecondLevelSamplingConfig:
    return _sampler(request).config


@router.put("/config", response_model=SecondLevelSamplingConfig)
async def update_second_level_sampling_config(
    config: SecondLevelSamplingConfig,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> SecondLevelSamplingConfig:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    return await _sampler(request).apply_config(config)


@router.post("/start", response_model=SecondLevelSamplingStatus)
async def start_second_level_sampling(
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> SecondLevelSamplingStatus:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    sampler = _sampler(request)
    config = sampler.config.model_copy(update={"enabled": True})
    saved = await sampler.apply_config(config)
    await sampler.start(saved)
    return await sampler.status()


@router.post("/stop", response_model=SecondLevelSamplingStatus)
async def stop_second_level_sampling(
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> SecondLevelSamplingStatus:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)
    sampler = _sampler(request)
    await sampler.apply_config(sampler.config.model_copy(update={"enabled": False}))
    return await sampler.status()


@router.get("/status", response_model=SecondLevelSamplingStatus)
async def get_second_level_sampling_status(request: Request) -> SecondLevelSamplingStatus:
    return await _sampler(request).status()


@router.get("/samples", response_model=list[SecondLevelMarketSample])
async def list_second_level_samples(
    request: Request,
    exchange: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    minutes: int = Query(default=60, ge=1, le=60 * 24 * 30),
    limit: int = Query(default=1000, ge=1, le=20_000),
) -> list[SecondLevelMarketSample]:
    sampler = _sampler(request)
    repo = sampler.repo
    normalized_exchange = exchange.strip().lower() if exchange else None
    if normalized_exchange and normalized_exchange not in SUPPORTED_SECOND_LEVEL_EXCHANGES:
        raise HTTPException(status_code=422, detail=f"Unsupported exchange: {exchange}")
    normalized_symbols = (
        await sampler.resolve_display_symbols(
            normalize_pair_spread_symbol(symbol),
            exchanges=[normalized_exchange] if normalized_exchange else None,
        )
        if symbol
        else None
    )
    return await repo.list_samples(
        exchange=normalized_exchange,
        symbols=normalized_symbols,
        since=datetime.now(UTC) - timedelta(minutes=minutes),
        limit=limit,
    )


@router.get("/component-samples", response_model=list[SecondLevelIndexComponentSample])
async def list_second_level_index_component_samples(
    request: Request,
    target_exchange: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    component_source: str | None = Query(default=None),
    minutes: int = Query(default=60, ge=1, le=60 * 24 * 30),
    limit: int = Query(default=1000, ge=1, le=20_000),
) -> list[SecondLevelIndexComponentSample]:
    sampler = _sampler(request)
    normalized_exchange = target_exchange.strip().lower() if target_exchange else None
    if normalized_exchange and normalized_exchange not in SUPPORTED_SECOND_LEVEL_EXCHANGES:
        raise HTTPException(status_code=422, detail=f"Unsupported exchange: {target_exchange}")
    normalized_symbols = (
        await sampler.resolve_display_symbols(
            normalize_pair_spread_symbol(symbol),
            exchanges=[normalized_exchange] if normalized_exchange else None,
        )
        if symbol
        else None
    )
    normalized_source = component_source.strip().lower() if component_source else None
    return await sampler.repo.list_component_samples(
        target_exchange=normalized_exchange,
        symbols=normalized_symbols,
        component_source=normalized_source,
        since=datetime.now(UTC) - timedelta(minutes=minutes),
        limit=limit,
    )


@router.post("/fat-finger-backtest", response_model=FatFingerBacktestResult)
async def run_second_level_fat_finger_backtest(
    config: FatFingerBacktestRequest,
    request: Request,
) -> FatFingerBacktestResult:
    sampler = _sampler(request)
    display_symbols = await sampler.resolve_display_symbols(config.symbol)
    rows = await sampler.repo.list_samples(
        symbols=display_symbols,
        since=datetime.now(UTC) - timedelta(hours=config.hours),
        limit=config.sample_limit + 1,
    )
    samples_truncated = len(rows) > config.sample_limit
    if samples_truncated:
        rows = rows[: config.sample_limit]
    result = run_fat_finger_backtest(rows, config)
    result.samples_truncated = samples_truncated
    if samples_truncated:
        result.warnings.insert(
            0,
            f"原始样本超过 {config.sample_limit:,} 条，仅回放最近部分；请缩短周期或提高样本上限后再判断策略表现。",
        )
    return result
