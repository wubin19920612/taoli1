import asyncio
import math
import re
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.models.instrument import InstrumentMarketCapCandidate, InstrumentMarketCapResult

COINGECKO_URL = "https://api.coingecko.com/api/v3"
SEARCH_TTL = timedelta(hours=6)
MARKET_CAP_TTL = timedelta(minutes=5)
MAX_CACHE_ENTRIES = 256


class InvalidMarketCapSelection(ValueError):
    pass


class InstrumentMarketCapService:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0, read=7.0),
            headers={"User-Agent": "taoli1-radar/0.1"},
        )
        self._owns_client = client is None
        self._search_cache: OrderedDict[str, tuple[datetime, list[InstrumentMarketCapCandidate]]] = OrderedDict()
        self._market_cache: OrderedDict[str, tuple[datetime, tuple[float | None, datetime | None]]] = OrderedDict()
        self._locks = [asyncio.Lock() for _ in range(32)]

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def lookup(self, base: str, coin_id: str | None = None) -> InstrumentMarketCapResult:
        normalized = base.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{1,30}", normalized):
            raise ValueError("Invalid base symbol")
        async with self._locks[hash(normalized) % len(self._locks)]:
            try:
                candidates = await self._candidates(normalized)
            except (httpx.HTTPError, ValueError, TypeError):
                return InstrumentMarketCapResult(base=normalized, status="source_error")
            if coin_id and coin_id not in {candidate.id for candidate in candidates}:
                raise InvalidMarketCapSelection("Selected coin does not match the queried symbol")
            if not candidates:
                return InstrumentMarketCapResult(base=normalized, status="not_found")
            if not coin_id and len(candidates) > 1:
                return InstrumentMarketCapResult(
                    base=normalized, status="ambiguous", candidates=candidates,
                )
            selected = coin_id or candidates[0].id
            try:
                market_cap, updated_at = await self._market_cap(selected)
            except (httpx.HTTPError, ValueError, TypeError):
                return InstrumentMarketCapResult(
                    base=normalized, status="source_error", candidates=candidates,
                    selected_id=selected,
                )
            return InstrumentMarketCapResult(
                base=normalized,
                status="available" if market_cap is not None else "unavailable",
                candidates=candidates,
                selected_id=selected,
                market_cap_usd=market_cap,
                updated_at=updated_at,
            )

    async def _candidates(self, base: str) -> list[InstrumentMarketCapCandidate]:
        now = datetime.now(UTC)
        cached = self._search_cache.get(base)
        if cached and now - cached[0] < SEARCH_TTL:
            self._search_cache.move_to_end(base)
            return cached[1]
        response = await self.client.get(f"{COINGECKO_URL}/search", params={"query": base})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("coins"), list):
            raise TypeError("Invalid CoinGecko search response")
        unique: dict[str, InstrumentMarketCapCandidate] = {}
        for row in payload["coins"]:
            if not isinstance(row, dict) or str(row.get("symbol") or "").strip().upper() != base:
                continue
            coin_id = row.get("id")
            if not isinstance(coin_id, str) or not coin_id.strip():
                continue
            rank = row.get("market_cap_rank")
            unique[coin_id] = InstrumentMarketCapCandidate(
                id=coin_id,
                name=str(row.get("name") or coin_id),
                symbol=base,
                market_cap_rank=rank if type(rank) is int and rank > 0 else None,
            )
        candidates = sorted(
            unique.values(),
            key=lambda coin: (coin.market_cap_rank is None, coin.market_cap_rank or 10**9, coin.name),
        )
        self._remember(self._search_cache, base, (now, candidates))
        return candidates

    async def _market_cap(self, coin_id: str) -> tuple[float | None, datetime | None]:
        now = datetime.now(UTC)
        cached = self._market_cache.get(coin_id)
        if cached and now - cached[0] < MARKET_CAP_TTL:
            self._market_cache.move_to_end(coin_id)
            return cached[1]
        response = await self.client.get(
            f"{COINGECKO_URL}/coins/markets",
            params={"vs_currency": "usd", "ids": coin_id, "per_page": 1, "page": 1},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise TypeError("Invalid CoinGecko market response")
        row = next((item for item in payload if isinstance(item, dict) and item.get("id") == coin_id), None)
        raw_cap: Any = row.get("market_cap") if row else None
        market_cap = float(raw_cap) if type(raw_cap) in (int, float) else None
        if market_cap is not None and (not math.isfinite(market_cap) or market_cap <= 0):
            market_cap = None
        raw_updated = row.get("last_updated") if row else None
        try:
            updated_at = datetime.fromisoformat(raw_updated) if isinstance(raw_updated, str) else None
        except ValueError:
            updated_at = None
        result = (market_cap, updated_at)
        self._remember(self._market_cache, coin_id, (now, result))
        return result

    @staticmethod
    def _remember(cache: OrderedDict, key: str, value: tuple) -> None:
        cache[key] = value
        cache.move_to_end(key)
        if len(cache) > MAX_CACHE_ENTRIES:
            cache.popitem(last=False)
