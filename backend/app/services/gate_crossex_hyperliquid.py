import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic

import httpx

from app.models.opportunity import Opportunity

SYMBOLS_URL = "https://api.gateio.ws/api/v4/crossex/rule/symbols"


class GateCrossExHyperliquidCatalog:
    def __init__(
        self,
        fetch_symbol: Callable[[str], Awaitable[list[dict]]] | None = None,
        *,
        cache_seconds: float = 60,
        retry_seconds: float = 15,
        clock: Callable[[], float] = monotonic,
    ):
        self.fetch_symbol = fetch_symbol or self._fetch_symbol
        self.cache_seconds = cache_seconds
        self.retry_seconds = retry_seconds
        self.clock = clock
        self._cache: dict[str, tuple[str | None, float]] = {}
        self._lock = asyncio.Lock()
        self._retry_after = 0.0

    async def _fetch_symbol(self, symbol: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(SYMBOLS_URL, params={"symbols": symbol})
            response.raise_for_status()
            return response.json()

    async def _listed_dex(self, symbol: str) -> str | None:
        async with self._lock:
            cached = self._cache.get(symbol)
            if cached is not None and self.clock() < cached[1]:
                return cached[0]
            if self.clock() < self._retry_after:
                return None
            try:
                records = await self.fetch_symbol(symbol)
                if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                    raise ValueError("Invalid CrossEx symbol list")
                dexes = {
                    row["tag"].strip().lower() or "main"
                    for row in records
                    if row.get("symbol") == symbol
                    and row.get("exchange_type") == "HYPERLIQUID"
                    and row.get("business_type") == "FUTURE"
                    and row.get("state") == "live"
                    and isinstance(row.get("tag"), str)
                }
                dex = next(iter(dexes)) if len(dexes) == 1 else None
                ttl = self.cache_seconds
            except Exception:  # noqa: BLE001 - an unverified market must use native HL.
                dex, ttl = None, self.retry_seconds
                self._retry_after = self.clock() + ttl
            self._cache[symbol] = (dex, self.clock() + ttl)
            return dex

    async def supports_market(self, raw_symbol: str | None) -> bool:
        if not raw_symbol or raw_symbol.count(":") > 1:
            return False
        raw = raw_symbol.strip()
        if ":" in raw:
            dex, coin = raw.split(":", 1)
            dex = dex.strip().lower()
        else:
            dex, coin = "main", raw
        coin = coin.strip()
        if not dex or not coin:
            return False
        symbol = f"HYPERLIQUID_FUTURE_{coin.upper()}_USDC"
        return await self._listed_dex(symbol) == dex

    async def supports_opportunity(self, opportunity: Opportunity) -> bool:
        for exchange, raw_symbol in (
            (opportunity.buy_exchange, opportunity.buy_raw_symbol),
            (opportunity.sell_exchange, opportunity.sell_raw_symbol),
        ):
            if (
                exchange.strip().lower() in {"hyperliquid", "hyper", "hl"}
                and not await self.supports_market(raw_symbol)
            ):
                return False
        return True
