import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha1
from itertools import combinations
from math import isfinite
from time import monotonic

from app.models.astro_preadd import (
    AstroPreaddCandidate,
    AstroPreaddPreview,
    AstroPreaddRunResult,
    AstroPreaddSettings,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.services.astro_alerts import AstroAlertService
from app.services.data_filters import filter_markets, ignored_exchange_set
from app.services.market_labels import astro_exchange_route_variants

logger = logging.getLogger(__name__)


def _funding_signal(market: MarketSnapshot) -> tuple[float | None, str]:
    if not market.funding_interval_hours or market.funding_interval_hours <= 0:
        return None, "missing"
    value = market.funding_next_rate_pct
    if value is not None and isfinite(value):
        return value, "predicted"
    value = market.funding_rate_pct
    return (value, "current") if value is not None and isfinite(value) else (None, "missing")


def _premium_proxy(market: MarketSnapshot) -> float | None:
    if market.mark_price is None or market.index_price is None or market.index_price <= 0:
        return None
    value = (market.mark_price / market.index_price - 1) * 100
    return value if isfinite(value) else None


def _candidate_id(symbol: str, buy: str, sell: str) -> str:
    return sha1(f"{symbol}:{buy}->{sell}".encode()).hexdigest()[:16]


def find_preadd_candidates(
    markets: list[MarketSnapshot],
    settings: AstroPreaddSettings,
    *,
    now: datetime | None = None,
) -> AstroPreaddPreview:
    current = now or datetime.now(UTC)
    grouped: dict[str, dict[str, MarketSnapshot]] = defaultdict(dict)
    for market in markets:
        if market.market_type != MarketType.FUTURE or market.exchange not in settings.exchanges:
            continue
        observed = market.timestamp.replace(tzinfo=UTC) if market.timestamp.tzinfo is None else market.timestamp
        if (current - observed).total_seconds() > settings.stale_after_seconds:
            continue
        if market.bid >= market.ask:
            continue
        previous = grouped[market.symbol].get(market.exchange)
        if previous is None or market.timestamp > previous.timestamp:
            grouped[market.symbol][market.exchange] = market

    items: list[tuple[float, AstroPreaddCandidate]] = []
    warnings: list[str] = []
    for symbol, by_exchange in grouped.items():
        for left, right in combinations(by_exchange.values(), 2):
            if not astro_exchange_route_variants(left.exchange, right.exchange):
                continue
            mids = ((left.bid + left.ask) / 2, (right.bid + right.ask) / 2)
            if max(mids) / min(mids) > 3:
                if len(warnings) < 10:
                    warnings.append(f"{symbol} {left.exchange}/{right.exchange} 价格倍率未校准，未预建")
                continue
            signals: list[tuple[float, int, MarketSnapshot, str, float, str]] = []
            for market in (left, right):
                funding, source = _funding_signal(market)
                if funding is not None and abs(funding) >= settings.funding_threshold_pct:
                    signals.append((
                        abs(funding) / settings.funding_threshold_pct, 2 if source == "predicted" else 1,
                        market, "funding", funding, source,
                    ))
                premium = _premium_proxy(market)
                if premium is not None and abs(premium) >= settings.premium_threshold_pct:
                    signals.append((
                        abs(premium) / settings.premium_threshold_pct, 0,
                        market, "premium_proxy", premium, source,
                    ))
            if not signals:
                continue
            directions = {
                market.exchange if value < 0 else (right if market is left else left).exchange
                for _, _, market, _, value, _ in signals
            }
            if len(directions) != 1:
                if len(warnings) < 10:
                    warnings.append(f"{symbol} {left.exchange}/{right.exchange} 信号方向冲突，未预建")
                continue
            strength, _, signal_market, kind, value, source = max(
                signals, key=lambda signal: (signal[0], signal[1])
            )
            buy = next(market for market in (left, right) if market.exchange in directions)
            sell = right if buy is left else left
            spread = 2 * (sell.bid - buy.ask) / (sell.bid + buy.ask) * 100
            items.append((strength, AstroPreaddCandidate(
                id=_candidate_id(symbol, buy.exchange, sell.exchange),
                symbol=symbol,
                buy_exchange=buy.exchange,
                sell_exchange=sell.exchange,
                signal_exchange=signal_market.exchange,
                signal_type=kind,
                signal_value_pct=value,
                funding_source=source,
                funding_interval_hours=signal_market.funding_interval_hours if kind == "funding" else None,
                live_spread_pct=spread,
                observed_at=min(left.timestamp, right.timestamp),
            )))
    items.sort(key=lambda item: item[0], reverse=True)
    return AstroPreaddPreview(
        items=[item for _, item in items[:50]],
        total_matches=len(items),
        warnings=warnings,
    )


class AstroPreaddService:
    def __init__(self, store, settings_repo, astro_service: AstroAlertService):
        self.store = store
        self.settings_repo = settings_repo
        self.astro_service = astro_service
        self._lock = asyncio.Lock()

    async def run_loop(self, stop_event: asyncio.Event) -> None:
        last_run = 0.0
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10)
            except TimeoutError:
                pass
            if stop_event.is_set():
                return
            try:
                settings = await self.settings_repo.get_astro_preadd_settings()
                if settings.enabled and monotonic() - last_run >= settings.scan_interval_seconds:
                    last_run = monotonic()
                    result = await self.run()
                    if result.attempted:
                        logger.info(
                            "Astro preadd: attempted=%s created=%s failed=%s",
                            result.attempted, result.created, result.failed,
                        )
            except Exception:  # Monitoring must survive a failed cycle.
                logger.exception("Astro preadd cycle failed")

    async def preview(self, settings: AstroPreaddSettings | None = None) -> AstroPreaddPreview:
        config = settings or await self.settings_repo.get_astro_preadd_settings()
        risk = await self.settings_repo.get_risk_settings()
        preview = find_preadd_candidates(
            filter_markets(self.store.get_all_markets(), risk), config
        )
        ignored = sorted(set(config.exchanges) & ignored_exchange_set(risk))
        if ignored:
            preview.warnings.insert(0, f"全局忽略交易所未参与预建：{'、'.join(ignored)}")
        return preview

    async def run(self, candidate_ids: list[str] | None = None) -> AstroPreaddRunResult:
        async with self._lock:
            config = await self.settings_repo.get_astro_preadd_settings()
            preview = await self.preview(config)
            selected = (
                [item for item in preview.items if item.id in set(candidate_ids)]
                if candidate_ids is not None else preview.items
            )
            selected = selected[:config.max_routes_per_run]
            result = AstroPreaddRunResult(warnings=list(preview.warnings))
            if not selected:
                result.warnings.append("没有满足条件且两边行情新鲜的交易对")
                return result
            if self.astro_service.settings.astro_dry_run_only:
                result.warnings.append("Astro dry-run 开启，未写入卡片")
                return result
            card_settings = await self.settings_repo.get_astro_card_settings()
            for item in selected:
                fresh = await self.preview(config)
                if item.id not in {candidate.id for candidate in fresh.items}:
                    result.skipped += 1
                    result.results.append(f"{item.symbol} 信号已消失或交易所被忽略，未预建")
                    continue
                by_market: dict[tuple[str, str], MarketSnapshot] = {}
                for market in self.store.get_all_markets():
                    if market.market_type != MarketType.FUTURE:
                        continue
                    key = (market.symbol, market.exchange)
                    previous = by_market.get(key)
                    if previous is None or market.timestamp > previous.timestamp:
                        by_market[key] = market
                buy = by_market.get((item.symbol, item.buy_exchange))
                sell = by_market.get((item.symbol, item.sell_exchange))
                if buy is None or sell is None:
                    result.skipped += 1
                    result.results.append(f"{item.symbol} 行情已变化，未预建")
                    continue
                observed = datetime.now(UTC)
                if any((observed - market.timestamp).total_seconds() > config.stale_after_seconds for market in (buy, sell)):
                    result.skipped += 1
                    result.results.append(f"{item.symbol} 行情已过期，未预建")
                    continue
                opportunity = Opportunity(
                    id=f"astro-preadd:{item.id}", type=OpportunityType.FF, symbol=item.symbol,
                    buy_exchange=buy.exchange, buy_market_type=MarketType.FUTURE,
                    buy_raw_symbol=buy.raw_symbol, sell_exchange=sell.exchange,
                    sell_market_type=MarketType.FUTURE, sell_raw_symbol=sell.raw_symbol,
                    open_spread_pct=config.open_spread_threshold_pct,
                    close_spread_pct=0,
                    fee_adjusted_open_pct=config.open_spread_threshold_pct,
                    spread_width_pct=config.open_spread_threshold_pct,
                    buy_bid=buy.bid, buy_ask=buy.ask, sell_bid=sell.bid, sell_ask=sell.ask,
                    buy_volume_24h_usdt=buy.volume_24h_usdt,
                    sell_volume_24h_usdt=sell.volume_24h_usdt,
                    risk_labels=[], last_seen_at=observed,
                )
                action = await self.astro_service.handle_preadd(opportunity, card_settings)
                result.attempted += 1
                result.created += action.status == "created"
                result.skipped += action.status == "skipped"
                result.failed += action.status == "failed"
                result.results.append(f"{item.symbol} {buy.exchange}->{sell.exchange}: {action.message}")
                if action.status == "failed":
                    break
            return result
