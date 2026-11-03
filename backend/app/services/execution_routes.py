"""Read-only execution catalogue. No order routing or account capability claims."""

import asyncio
import json
import logging
import math
import re
from datetime import UTC, datetime
from time import monotonic

import httpx

from app.services.gate_crossex_hyperliquid import SYMBOLS_URL

EXCHANGES = {name: name.upper() for name in (
    "binance", "okx", "bybit", "gate", "aster", "hyperliquid", "lighter", "kraken", "deribit",
)}
LIQUIDITY_TIERS = {"gt50k", "5k-50k", "500-5k", "lt500", "unknown"}
logger = logging.getLogger(__name__)


def validate_shadow_settings(settings):
    if not math.isfinite(settings.shadow_min_open_pct) or settings.shadow_min_open_pct < 0:
        raise ValueError("SHADOW_MIN_OPEN_PCT must be finite and nonnegative")
    if type(settings.shadow_max_open) is not int or not 2 <= settings.shadow_max_open <= 3000:
        raise ValueError("SHADOW_MAX_OPEN must be between 2 and 3000 (variant rows)")
    if type(settings.crossex_catalog_refresh_seconds) is not int or settings.crossex_catalog_refresh_seconds <= 0:
        raise ValueError("CROSSEX_CATALOG_REFRESH_SECONDS must be positive")
    values = json.loads(settings.shadow_slippage_bps)
    if not isinstance(values, dict) or set(values) != LIQUIDITY_TIERS or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
        for v in values.values()
    ):
        raise ValueError("SHADOW_SLIPPAGE_BPS requires all five finite nonnegative tiers")


def parse_catalog(records):
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError("Invalid CrossEx symbol list")
    result = set()
    for row in records:
        ex, business = row.get("exchange_type"), row.get("business_type")
        if row.get("state") != "live" or ex not in EXCHANGES.values() or business != "FUTURE":
            continue
        symbol, tag = row.get("symbol"), row.get("tag", "")
        if not isinstance(symbol, str) or not isinstance(tag, str):
            continue
        # Same tag semantics as GateCrossExHyperliquidCatalog, kept independent
        # of its per-symbol Astro cache and fail-closed retry behaviour.
        if ex == "HYPERLIQUID" and "tag" not in row:
            continue
        prefix = f"{ex}_{business}_"
        if not symbol.startswith(prefix):
            continue
        parts = symbol[len(prefix):].rsplit("_", 1)
        if len(parts) == 2 and parts[0] and parts[1] in {"USDT", "USDC", "USD"}:
            result.add((ex, business, parts[0], tag.strip().lower()))
    return result


def raw_base(exchange, raw_symbol, dex=None):
    """Strip venue syntax/quote only; never use normalized ticker or aliases."""
    if not raw_symbol:
        return None
    raw = raw_symbol.strip()
    if exchange == "hyperliquid":
        if raw.count(":") > 1:
            return None
        if ":" in raw:
            prefix, raw = raw.split(":", 1)
            if dex and prefix.lower() != dex.lower():
                return None
        return raw.upper()
    if ":" in raw:  # CCXT settlement suffix is not a base.
        raw, settlement = raw.split(":", 1)
        if settlement not in {"USDT", "USDC", "USD"}:
            return None
    raw = re.sub(r"[-_]SWAP$", "", raw)
    for quote in ("USDT", "USDC", "USD"):
        if raw.endswith(quote):
            return raw[:-len(quote)].rstrip("-_/ ") or None
    return raw if exchange == "lighter" else None


class ExecutionRoutes:
    def __init__(self, native="hyperliquid,lighter,bitget", refresh_seconds=1800, fetch=None, clock=monotonic):
        self.native = frozenset(x.strip().lower() for x in native.split(",") if x.strip())
        self.refresh_seconds = refresh_seconds
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.entries = set()
        self.updated_at = None
        self.retry_at = 0.0
        self.errors = 0
        self._route_cache = {}
        self._cache_entries = self.entries

    @staticmethod
    async def _fetch():
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(SYMBOLS_URL)
            response.raise_for_status()
            return response.json()

    async def refresh(self):
        if self.clock() < self.retry_at:
            return
        try:
            entries = parse_catalog(await self.fetch())
        except Exception:
            self.errors += 1
            self.retry_at = self.clock() + 900
            logger.warning("shadow CrossEx catalogue refresh failed; retaining previous entries")
            return
        self.entries = entries
        self._route_cache.clear()
        self._cache_entries = entries
        self.updated_at = datetime.now(UTC).isoformat()
        self.retry_at = self.clock() + self.refresh_seconds

    async def run(self, stop):
        while not stop.is_set():
            await self.refresh()
            try:
                await asyncio.wait_for(stop.wait(), max(0.1, self.retry_at - self.clock()))
            except TimeoutError:
                pass

    def routes(self, exchange, market_type, raw_symbol, dex=None):
        if self._cache_entries is not self.entries or len(self._route_cache) >= 10000:
            self._route_cache.clear()
            self._cache_entries = self.entries
        key = (exchange, market_type, raw_symbol, dex)
        cached = self._route_cache.get(key)
        if cached is not None:
            return cached
        exchange = exchange.lower()
        result = ["native"] if exchange in self.native else []
        base = raw_base(exchange, raw_symbol, dex)
        tag = ""
        if exchange == "hyperliquid":
            tag = (dex or (raw_symbol.split(":", 1)[0] if raw_symbol and ":" in raw_symbol else "")).lower()
            tag = "" if tag == "main" else tag
        if market_type == "future" and base and (EXCHANGES.get(exchange), "FUTURE", base, tag) in self.entries:
            result.append("crossex")
        self._route_cache[key] = result
        return result

    def fields(self, opportunity):
        buy = self.routes(opportunity.buy_exchange, opportunity.buy_market_type, opportunity.buy_raw_symbol, opportunity.buy_dex)
        sell = self.routes(opportunity.sell_exchange, opportunity.sell_market_type, opportunity.sell_raw_symbol, opportunity.sell_dex)
        return {"buy_routes": buy, "sell_routes": sell, "executable": bool(buy and sell)}
