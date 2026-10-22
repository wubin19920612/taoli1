"""Signal-to-card bridge. Native Bybit FS only; never enable or update a card."""

import asyncio
from datetime import UTC, datetime

from app.models.market import MarketSnapshot, MarketType
from app.services.astro_planner import AstroPairPlanner, AstroPlannerConfig
from app.services.data_filters import ignored_exchange_set, symbol_is_excluded
from app.services.spread_engine import build_directional_opportunity


def card_plan(row):
    prices = row.prices
    common = {
        "symbol": row.symbol,
        "raw_symbol": row.symbol,
        "base": row.base,
        "exchange": "bybit",
        "timestamp": row.observed_at,
    }
    future = MarketSnapshot(
        **common,
        market_type=MarketType.FUTURE,
        bid=prices["future_sell"],
        ask=prices["future_buy"],
        funding_rate_pct=row.funding_pct,
        funding_interval_hours=int(row.interval_hours),
        funding_next_time=row.next_funding_at,
        volume_24h_usdt=row.future_volume_usdt,
    )
    spot = MarketSnapshot(
        **common,
        market_type=MarketType.SPOT,
        bid=prices["spot_sell"],
        ask=prices["spot_buy"],
        volume_24h_usdt=row.spot_volume_usdt,
    )
    opportunity = build_directional_opportunity(
        future,
        spot,
        mode="SF",
        buy_fee_pct=row.future_fee_pct,
        sell_fee_pct=row.spot_fee_pct,
        safety_slippage_pct=0,
    )
    # Reuse the existing FS mapping and precision rules. Strict signal validation
    # is performed by this bridge before permitting the planner's reverse-SF path.
    plan = AstroPairPlanner(AstroPlannerConfig()).plan(opportunity, allow_manual_override=True)
    if not plan.can_submit or not plan.pair or plan.pair["type"] != "FS":
        raise ValueError("fs_plan_unavailable")
    return plan.pair


class FsAstroBridge:
    def __init__(self, client, repository, risk_loader, *, dry_run_only):
        self.client, self.repository, self.risk_loader = client, repository, risk_loader
        self.dry_run_only = dry_run_only
        self.lock = asyncio.Lock()

    async def process(self, row, recheck, still_current):
        async with self.lock:
            write_started = False
            try:
                if not row.signal_ready or not still_current():
                    return "signal_changed"
                risk = await self.risk_loader()
                if symbol_is_excluded(row.symbol, risk) or "bybit" in ignored_exchange_set(risk):
                    return "risk_blocked"
                pairs = await self.client.list_pairs()
                exact = [
                    p
                    for p in pairs
                    if p.get("name") == row.base
                    and p.get("type") == "FS"
                    and p.get("buyEx") == p.get("sellEx") == "bybit"
                ]
                if exact:
                    if await self.repository.astro_attempt(row.symbol) == "pending":
                        await self.repository.record_astro_attempt(row.symbol, "reconciled")
                    return "existing"
                # A pending write may have reached Astro. Never blindly retry it,
                # including after restart; an absent list entry is not proof of failure.
                attempt = await self.repository.astro_attempt(row.symbol)
                if attempt:
                    return "uncertain" if attempt == "pending" else "recorded"
                fresh = await recheck()
                if (
                    not still_current()
                    or not fresh.signal_ready
                    or fresh.identity_details != row.identity_details
                    or fresh.valid_until is None
                    or fresh.valid_until <= datetime.now(UTC)
                ):
                    return "signal_changed"
                pair = card_plan(fresh)
                if pair["status"] is not False or pair["disableOpen"] is not True:
                    return "invalid_plan"
                if self.dry_run_only:
                    return "dry_run"
                risk = await self.risk_loader()
                if symbol_is_excluded(row.symbol, risk) or "bybit" in ignored_exchange_set(risk):
                    return "risk_blocked"
                await self.repository.record_astro_attempt(row.symbol, "pending")
                write_started = True
                if not still_current() or fresh.valid_until <= datetime.now(UTC):
                    return "uncertain"
                await self.client.add_pair(pair)
                await self.repository.record_astro_attempt(row.symbol, "created")
                return "created"
            except Exception:  # noqa: BLE001 - no credentials or vendor messages in reports.
                return "uncertain" if write_started else "query_failed"
