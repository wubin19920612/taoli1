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
        self._cache: dict[str, tuple[bool, float]] = {}
        self._lock = asyncio.Lock()
        self._retry_after = 0.0

    async def _fetch_symbol(self, symbols: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(SYMBOLS_URL, params={"symbols": symbols})
            response.raise_for_status()
            return response.json()

    async def _is_listed(self, dex: str, coin: str) -> bool:
        legacy_symbol = f"HYPERLIQUID_FUTURE_{coin}_USDC"
        prefixed_symbol = f"HYPERLIQUID_FUTURE_{dex}:{coin}_USDC" if dex != "main" else None
        symbols = f"{prefixed_symbol},{legacy_symbol}" if prefixed_symbol else legacy_symbol
        async with self._lock:
            cached = self._cache.get(symbols)
            if cached is not None and self.clock() < cached[1]:
                return cached[0]
            if self.clock() < self._retry_after:
                return False
            try:
                records = await self.fetch_symbol(symbols)
                if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                    raise ValueError("Invalid CrossEx symbol list")
                live_records = [
                    row
                    for row in records
                    if row.get("exchange_type") == "HYPERLIQUID"
                    and row.get("business_type") == "FUTURE"
                    and row.get("state") == "live"
                    and isinstance(row.get("tag"), str)
                ]
                prefixed_dexes = {
                    row["tag"].strip().lower()
                    for row in live_records
                    if prefixed_symbol is not None and row.get("symbol") == prefixed_symbol
                }
                if prefixed_dexes:
                    # A live prefixed name decides alone; conflicting tags keep native HL.
                    supported = prefixed_dexes == {dex}
                else:
                    legacy_dexes = {
                        row["tag"].strip().lower() or "main"
                        for row in live_records
                        if row.get("symbol") == legacy_symbol
                    }
                    supported = legacy_dexes == {dex}
                ttl = self.cache_seconds
            except Exception:  # noqa: BLE001 - an unverified market must use native HL.
                supported, ttl = False, self.retry_seconds
                self._retry_after = self.clock() + ttl
            self._cache[symbols] = (supported, self.clock() + ttl)
            return supported

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
        return await self._is_listed(dex, coin.upper())

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
