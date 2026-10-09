from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .paper_models import FundingSettlement
from .route_engine import RouteLeg

D = Decimal


def _decimal(value: Any) -> Decimal:
    try:
        parsed = D(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid public funding number") from exc
    if not parsed.is_finite():
        raise ValueError("non-finite public funding number")
    return parsed


class PublicFundingHistory:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _get(self, url: str, params: dict[str, Any]) -> Any:
        for attempt in range(3):
            response = await self.client.get(url, params=params)
            if response.status_code in (418, 429) and attempt < 2:
                retry_after = response.headers.get("Retry-After", "")
                delay = min(30, int(retry_after)) if retry_after.isdigit() else 2 ** attempt
                await asyncio.sleep(max(1, delay))
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("public funding retry limit reached")

    async def settlements(
        self, leg: RouteLeg, after: datetime, through: datetime,
    ) -> list[FundingSettlement]:
        after = after.astimezone(UTC)
        through = min(through.astimezone(UTC), after + timedelta(days=1))
        if through <= after:
            return []
        params = {
            "symbol": leg.raw_symbol,
            "startTime": int(after.timestamp() * 1000) + 1,
            "endTime": int(through.timestamp() * 1000),
        }
        if leg.exchange == "binance":
            rows = await self._get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                {**params, "limit": 100},
            )
            if not isinstance(rows, list):
                raise ValueError("Binance funding history response invalid")
            result = []
            for row in rows:
                if row.get("symbol") != leg.raw_symbol:
                    raise ValueError("Binance funding history market mismatch")
                if row.get("markPrice") is None:
                    raise ValueError("Binance historical settlement mark unavailable")
                at = datetime.fromtimestamp(int(row["fundingTime"]) / 1000, UTC)
                if after < at <= through:
                    result.append(FundingSettlement(
                        exchange=leg.exchange, market_key=leg.key, settled_at=at,
                        rate=_decimal(row["fundingRate"]),
                        mark_price=_decimal(row["markPrice"]), mark_kind="actual",
                        source="binance_fapi_public_funding_history",
                    ))
            return sorted(result, key=lambda item: item.settled_at)
        if leg.exchange == "bybit":
            payload = await self._get(
                "https://api.bybit.com/v5/market/funding/history",
                {"category": "linear", **params, "limit": 100},
            )
            if payload.get("retCode") != 0:
                raise ValueError("Bybit funding history unavailable")
            rows = payload.get("result", {}).get("list", [])
            if not isinstance(rows, list):
                raise ValueError("Bybit funding history response invalid")
            result = []
            for row in rows:
                if row.get("symbol") != leg.raw_symbol:
                    raise ValueError("Bybit funding history market mismatch")
                at = datetime.fromtimestamp(int(row["fundingRateTimestamp"]) / 1000, UTC)
                if not after < at <= through:
                    continue
                if through < at + timedelta(minutes=1):
                    continue
                mark = await self._bybit_mark_proxy(leg.raw_symbol, at)
                result.append(FundingSettlement(
                    exchange=leg.exchange, market_key=leg.key, settled_at=at,
                    rate=_decimal(row["fundingRate"]), mark_price=mark,
                    mark_kind="one_minute_proxy",
                    source="bybit_public_funding_history_and_mark_kline_close",
                ))
            return sorted(result, key=lambda item: item.settled_at)
        raise ValueError("unsupported funding exchange")

    async def _bybit_mark_proxy(self, symbol: str, at: datetime) -> Decimal:
        minute = at.replace(second=0, microsecond=0)
        start = int(minute.timestamp() * 1000)
        payload = await self._get(
            "https://api.bybit.com/v5/market/mark-price-kline",
            {
                "category": "linear", "symbol": symbol, "interval": "1",
                "start": start, "end": start + 60_000, "limit": 2,
            },
        )
        if payload.get("retCode") != 0 or payload.get("result", {}).get("symbol") != symbol:
            raise ValueError("Bybit historical mark market mismatch")
        for row in payload.get("result", {}).get("list", []):
            if int(row[0]) == start:
                return _decimal(row[4])
        raise ValueError("Bybit one-minute mark proxy unavailable")
