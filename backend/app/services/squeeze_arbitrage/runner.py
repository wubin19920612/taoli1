from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from websockets.asyncio.client import connect as websocket_connect

from .discovery import (
    DEFAULT_EXCLUDED_SYMBOLS,
    DiscoveryTicker,
    early_candidate_reasons,
    early_candidate_score,
)
from .features import calculate_watch_features
from .liquidation import parse_force_order
from .models import HourCandle, PositionSample, WatchFeatures, market_key, utc_iso
from .provider import BinanceSqueezeProvider, latest_completed_hour
from .repository import SqueezeRepository

logger = logging.getLogger(__name__)
FORCE_ORDER_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"


@dataclass(frozen=True)
class SqueezeMonitorConfig:
    mode: Literal["auto", "fixed"] = "auto"
    symbols: tuple[str, ...] = ()
    excluded_symbols: tuple[str, ...] = DEFAULT_EXCLUDED_SYMBOLS
    screen_limit: int = 12
    poll_seconds: int = 300
    max_active: int = 30

    def __post_init__(self) -> None:
        if self.mode not in ("auto", "fixed"):
            raise ValueError("Squeeze monitor mode must be auto or fixed")
        if self.mode == "fixed" and not self.symbols:
            raise ValueError("Fixed squeeze monitor requires symbols")
        if len(self.symbols) > 5 or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Squeeze monitor allows at most five unique fixed symbols")
        if any(not re.fullmatch(r"[A-Z0-9]{1,30}USDT", symbol) for symbol in self.symbols + self.excluded_symbols):
            raise ValueError("Squeeze monitor symbols must be raw Binance USDT symbols")
        if not 1 <= self.screen_limit <= 20 or self.poll_seconds < 60 or not 1 <= self.max_active <= 30:
            raise ValueError("Squeeze monitor resource budget is invalid")


class SqueezeMonitor:
    def __init__(
        self, repo: SqueezeRepository, config: SqueezeMonitorConfig,
        provider: BinanceSqueezeProvider | None = None,
    ) -> None:
        self.repo = repo
        self.config = config
        self.provider = provider or BinanceSqueezeProvider()
        self._verified_symbols: set[str] = set()
        self._last_complete_bucket: datetime | None = None
        self.running = False

    async def aclose(self) -> None:
        await self.provider.aclose()

    async def scan_once(self, now: datetime | None = None) -> None:
        scan_started_at = now or datetime.now(UTC)
        bucket = latest_completed_hour(scan_started_at - timedelta(seconds=90))
        try:
            if self.config.mode == "auto":
                await self._scan_auto(bucket, scan_started_at, now)
                await self.repo.prune_samples(scan_started_at)
                return
            metadata = await self.provider.verified_symbols(self.config.symbols)
            self._verified_symbols = set(metadata)
            for symbol, details in metadata.items():
                await self.repo.save_market_identity(symbol, details, scan_started_at)
            missing = sorted(set(self.config.symbols) - self._verified_symbols)
            errors = [f"unverified_or_inactive_market:{symbol}" for symbol in missing]
            for symbol in self.config.symbols:
                if symbol not in self._verified_symbols:
                    continue
                candle_count = 0
                positioning_count = 0
                try:
                    candles = await self.provider.fetch_candles(symbol, bucket)
                    samples = await self.provider.fetch_positioning(symbol, bucket)
                    candle_count = len(candles)
                    positioning_count = len(samples)
                    await self.repo.save_market_data(candles, samples)
                    key = market_key(symbol)
                    saved_candles, saved_samples = await self.repo.load_market_data(key, bucket)
                    features = calculate_watch_features(
                        key, saved_candles, saved_samples,
                        bucket_at=bucket,
                        decision_at=now if now is not None else datetime.now(UTC),
                    )
                    if not await self.repo.save_features(features, max_active=self.config.max_active):
                        errors.append(f"watch_capacity:{symbol}")
                    await self.repo.record_scan_market(
                        key, bucket, scan_started_at, candle_count=candle_count,
                        positioning_count=positioning_count,
                        result_status=features.status,
                        error=";".join(features.reasons) or None,
                    )
                except Exception as exc:
                    logger.exception("squeeze scan failed for %s", symbol)
                    errors.append(f"{symbol}:{type(exc).__name__}:{exc}")
                    await self.repo.record_scan_market(
                        market_key(symbol), bucket, scan_started_at, candle_count=candle_count,
                        positioning_count=positioning_count,
                        result_status="error", error=f"{type(exc).__name__}:{exc}"[:500],
                    )
            await self.repo.save_discovery(
                bucket_at=bucket, selected_at=scan_started_at, mode="fixed",
                eligible_count=len(metadata), screened=[],
                selected=[{"raw_symbol": symbol, "selection_kind": "manual_configured"}
                          for symbol in self.config.symbols if symbol in self._verified_symbols],
            )
            if errors:
                await self.repo.set_scan_state(scan_started_at, error="; ".join(errors)[:1000])
            else:
                await self.repo.set_scan_state(
                    scan_started_at, bucket_at=bucket, symbols=sorted(self._verified_symbols)
                )
                self._last_complete_bucket = bucket
            await self.repo.prune_samples(scan_started_at)
        except Exception as exc:
            logger.exception("squeeze scan failed")
            if self.config.mode == "auto":
                self._verified_symbols.clear()
            await self.repo.set_scan_state(
                scan_started_at, error=f"{type(exc).__name__}:{exc}"[:1000]
            )

    async def _scan_auto(
        self, bucket: datetime, scan_started_at: datetime, now: datetime | None
    ) -> None:
        tickers, eligible_count = await self.provider.discover_tickers(
            excluded=set(self.config.excluded_symbols), limit=self.config.screen_limit
        )
        screened: list[dict[str, Any]] = []
        qualified: list[tuple[
            float, DiscoveryTicker, list[HourCandle], list[PositionSample],
            WatchFeatures, dict[str, Any],
        ]] = []
        errors: list[str] = []
        for ticker in tickers:
            symbol = ticker.raw_symbol
            key = market_key(symbol)
            candle_count = positioning_count = 0
            try:
                candles = await self.provider.fetch_candles(symbol, bucket)
                samples = await self.provider.fetch_positioning(symbol, bucket)
                candle_count, positioning_count = len(candles), len(samples)
                features = calculate_watch_features(
                    key, candles, samples, bucket_at=bucket,
                    decision_at=now if now is not None else datetime.now(UTC),
                )
                reasons = () if features.qualifies else early_candidate_reasons(features)
                record = {
                    "raw_symbol": symbol, "market_key": key,
                    "quote_volume_24h": ticker.quote_volume_24h,
                    "ticker_change_24h": ticker.price_change_24h,
                    "ticker_source_at": utc_iso(ticker.source_at),
                    "return_4h": features.return_4h,
                    "return_24h": features.return_24h,
                    "volume_ratio": features.volume_ratio,
                    "oi_current_growth": features.oi_current_growth,
                    "account_ratio": features.account_ratio,
                    "quality": features.status,
                    "reasons": list(reasons),
                    "selection_kind": "structure_event" if features.qualifies else "early_candidate",
                }
                screened.append(record)
                await self.repo.record_scan_market(
                    key, bucket, scan_started_at, candle_count=candle_count,
                    positioning_count=positioning_count,
                    result_status=features.status, error=";".join(reasons) or None,
                )
                if not reasons:
                    qualified.append((early_candidate_score(features), ticker, candles,
                                      samples, features, record))
            except Exception as exc:
                logger.exception("squeeze discovery sample failed for %s", symbol)
                error = f"{type(exc).__name__}:{exc}"[:500]
                errors.append(f"{symbol}:{error}")
                screened.append({"raw_symbol": symbol, "market_key": key,
                                 "quality": "error", "reasons": [error]})
                await self.repo.record_scan_market(
                    key, bucket, scan_started_at, candle_count=candle_count,
                    positioning_count=positioning_count,
                    result_status="error", error=error,
                )

        qualified.sort(key=lambda item: (item[4].qualifies, item[0], item[1].raw_symbol),
                       reverse=True)
        selected: list[dict[str, Any]] = []
        for _, ticker, candles, samples, features, record in qualified:
            if len(selected) == 5:
                break
            try:
                await self.repo.save_market_identity(
                    ticker.raw_symbol, ticker.metadata, scan_started_at
                )
                await self.repo.save_market_data(candles, samples)
                if not await self.repo.save_features(features, max_active=self.config.max_active):
                    errors.append(f"watch_capacity:{ticker.raw_symbol}")
                selected.append(record)
            except Exception as exc:
                logger.exception("squeeze discovery selection failed for %s", ticker.raw_symbol)
                errors.append(f"{ticker.raw_symbol}:{type(exc).__name__}:{exc}")

        await self.repo.save_discovery(
            bucket_at=bucket, selected_at=scan_started_at, mode="auto",
            eligible_count=eligible_count, screened=screened, selected=selected,
        )
        self._verified_symbols = {row["raw_symbol"] for row in selected}
        await self.repo.set_scan_state(
            scan_started_at, bucket_at=bucket,
            symbols=sorted(self._verified_symbols),
            error="; ".join(errors)[:1000] or None,
        )
        self._last_complete_bucket = bucket

    async def _run_scans(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            bucket = latest_completed_hour(datetime.now(UTC) - timedelta(seconds=90))
            if bucket != self._last_complete_bucket:
                await self.scan_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.config.poll_seconds)
            except TimeoutError:
                pass

    async def _run_liquidations(self, stop_event: asyncio.Event) -> None:
        backoff = 1
        while not stop_event.is_set():
            try:
                async with websocket_connect(FORCE_ORDER_URL, open_timeout=10, ping_interval=20) as ws:
                    backoff = 1
                    await self.repo.set_coverage("throttled_public_stream", datetime.now(UTC))
                    while not stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=35)
                        except TimeoutError:
                            continue
                        received_at = datetime.now(UTC)
                        update = parse_force_order(raw, received_at)
                        if update is not None and update.market_key.split("|")[2] in self._verified_symbols:
                            await self.repo.record_liquidation(update)
                        await self.repo.set_coverage(
                            "throttled_public_stream", received_at,
                            last_message_at=received_at,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect after protocol/network failure.
                logger.warning("squeeze liquidation stream disconnected: %s", exc)
                await self.repo.set_coverage(
                    "disconnected", datetime.now(UTC), error=f"{type(exc).__name__}:{exc}"[:500]
                )
            if not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 60)

    async def run(self, stop_event: asyncio.Event) -> None:
        self.running = True
        scans = asyncio.create_task(self._run_scans(stop_event), name="squeeze-hourly-scan")
        liquidations = asyncio.create_task(
            self._run_liquidations(stop_event), name="squeeze-force-order-stream"
        )
        try:
            await asyncio.gather(scans, liquidations)
        finally:
            self.running = False
            scans.cancel()
            liquidations.cancel()
            await asyncio.gather(scans, liquidations, return_exceptions=True)
            await self.repo.set_coverage("disconnected", datetime.now(UTC), error="worker_stopped")
