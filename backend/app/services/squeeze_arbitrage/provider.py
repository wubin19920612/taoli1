from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from .models import HOUR, HourCandle, PositionSample, market_key


class BinanceSqueezeProvider:
    base_url = "https://fapi.binance.com"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=3))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        for attempt in range(3):
            response = await self.client.get(f"{self.base_url}{path}", params=params)
            if response.status_code in (429, 418) and attempt < 2:
                retry_after = response.headers.get("Retry-After", "")
                delay = min(30.0, max(1.0, float(retry_after))) if retry_after.isdigit() else 2 ** attempt
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("Binance request retry limit reached")

    async def verified_symbols(self, requested: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        payload = await self._get("/fapi/v1/exchangeInfo", {})
        rows = payload.get("symbols", []) if isinstance(payload, dict) else []
        allowed = set(requested)
        return {
            row["symbol"]: row for row in rows
            if isinstance(row, dict)
            and row.get("symbol") in allowed
            and row.get("status") == "TRADING"
            and row.get("contractType") == "PERPETUAL"
            and row.get("quoteAsset") == "USDT"
        }

    async def fetch_candles(
        self, raw_symbol: str, bucket_at: datetime
    ) -> list[HourCandle]:
        start_open = bucket_at - 172 * HOUR
        payload = await self._get(
            "/fapi/v1/klines",
            {
                "symbol": raw_symbol,
                "interval": "1h",
                "startTime": int(start_open.timestamp() * 1000),
                "endTime": int((bucket_at - HOUR).timestamp() * 1000),
                "limit": 200,
            },
        )
        if not isinstance(payload, list):
            raise TypeError("Binance hourly candle response is not a list")
        received_at = datetime.now(UTC)
        key = market_key(raw_symbol)
        result: list[HourCandle] = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 8:
                continue
            event_time = datetime.fromtimestamp(int(row[0]) / 1000, UTC) + HOUR
            if event_time > bucket_at or event_time > received_at:
                continue
            result.append(HourCandle(
                key, event_time, float(row[4]), float(row[7]), received_at, received_at
            ))
        return result

    async def fetch_positioning(
        self, raw_symbol: str, bucket_at: datetime
    ) -> list[PositionSample]:
        params = {"symbol": raw_symbol, "period": "1h", "limit": 30}
        oi_rows, ratio_rows = await asyncio.gather(
            self._get("/futures/data/openInterestHist", params),
            self._get("/futures/data/globalLongShortAccountRatio", params),
        )
        if not isinstance(oi_rows, list) or not isinstance(ratio_rows, list):
            raise TypeError("Binance positioning response is not a list")
        received_at = datetime.now(UTC)
        ratios: dict[datetime, tuple[datetime, float]] = {}
        for row in ratio_rows:
            if isinstance(row, dict) and row.get("timestamp") and row.get("longShortRatio"):
                at = datetime.fromtimestamp(int(row["timestamp"]) / 1000, UTC)
                ratios[at.replace(minute=0, second=0, microsecond=0)] = (
                    at, float(row["longShortRatio"])
                )
        key = market_key(raw_symbol)
        result: list[PositionSample] = []
        for row in oi_rows:
            if not isinstance(row, dict):
                continue
            try:
                event_time = datetime.fromtimestamp(int(row["timestamp"]) / 1000, UTC)
                raw_oi = float(row["sumOpenInterest"])
                usd = float(row["sumOpenInterestValue"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if event_time > bucket_at or raw_oi < 0 or usd < 0:
                continue
            ratio = ratios.get(event_time.replace(minute=0, second=0, microsecond=0))
            result.append(PositionSample(
                key, event_time, raw_oi, "binance_usdm_contract", usd,
                ratio[1] if ratio is not None and ratio[1] > 0 else None,
                received_at, received_at,
                account_ratio_event_time=ratio[0] if ratio else None,
            ))
        return result


def latest_completed_hour(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
