"""Bounded, rotating all-market premium monitoring, independent of Astro orders."""

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from itertools import permutations
from math import ceil, isfinite
from time import monotonic

from app.models.alert import AlertEvent
from app.models.market import MarketSnapshot, MarketType
from app.models.market_identity import MarketKey, utc
from app.models.premium_index import SUPPORTED_PREMIUM_INDEX_EXCHANGES
from app.models.settings import FeeSettings, RiskSettings
from app.services.data_filters import filter_markets
from app.services.market_sessions import is_market_snapshot_tradable
from app.services.pair_validation import validate_pair
from app.services.premium_index_query import PremiumIndexQueryService
from app.services.spread_engine import midpoint_spread_pct

logger = logging.getLogger(__name__)
BATCH_SIZE = 16
OFFICIAL_MAX_AGE_SECONDS = 90
MAX_SAMPLE_SKEW_SECONDS = 60


def eligible_groups(markets: list[MarketSnapshot], settings: RiskSettings, now: datetime):
    groups = defaultdict(dict)
    for market in filter_markets(markets, settings):
        if (
            market.market_type != MarketType.FUTURE
            or market.exchange not in SUPPORTED_PREMIUM_INDEX_EXCHANGES
            or market.identity_status != "verified"
            or market.unit_status != "verified"
            or not market.asset_id
            or not market.binding_valid_until
            or utc(market.binding_valid_until) <= now
        ):
            continue
        try:
            key = MarketKey.from_snapshot(market).key
        except ValueError:
            continue
        # Valuation contracts with different tracking definitions are distinct instruments.
        group = (
            market.asset_id,
            market.asset_kind or "",
            market.contract_kind,
            market.tracking_definition if market.contract_kind == "valuation" else "",
        )
        groups[group][key] = market
    return {
        key: list(rows.values())
        for key, rows in groups.items()
        if len({row.exchange for row in rows.values()}) >= 2
    }


def matching_points(rows, settings: RiskSettings, now: datetime):
    """Require fresh, overlapping observations from at least two actual exchanges."""
    hits = []
    for market, point in rows:
        max_age = (
            settings.stale_after_seconds
            if point.source == "mark_index_fallback"
            else OFFICIAL_MAX_AGE_SECONDS
        )
        if (
            isfinite(point.premium_pct)
            and abs(point.premium_pct) > settings.multi_exchange_premium_threshold_pct
            and 0 <= (now - utc(point.bucket_at)).total_seconds() <= max_age
        ):
            hits.append((market, point))
    # Find a valid time window instead of allowing an old outlier to suppress fresh peers.
    hits.sort(key=lambda row: utc(row[1].bucket_at))
    best = []
    for _, anchor in hits:
        window = [
            row
            for row in hits
            if 0
            <= (utc(row[1].bucket_at) - utc(anchor.bucket_at)).total_seconds()
            <= MAX_SAMPLE_SKEW_SECONDS
        ]
        if len({m.exchange for m, _ in window}) > len({m.exchange for m, _ in best}):
            best = window
    return best if len({market.exchange for market, _ in best}) >= 2 else []


def _compact_amount(value: float | None) -> str:
    if value is None or not isfinite(value) or value < 0:
        return "—"
    for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= scale:
            return f"{value / scale:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _market_label(market: MarketSnapshot) -> str:
    label = f"{market.exchange}:{market.raw_symbol}"
    if market.dex and not market.raw_symbol.lower().startswith(f"{market.dex.lower()}:"):
        label += f"({market.dex})"
    return label


def build_premium_alert_message(
    rows,
    settings: RiskSettings,
    *,
    now: datetime | None = None,
    fee_settings: FeeSettings | None = None,
) -> str:
    now = utc(now or datetime.now(UTC))
    fees = fee_settings or FeeSettings()
    markets = [market for market, _ in rows]
    routes = []
    for buy, sell in permutations(markets, 2):
        if buy.exchange == sell.exchange:
            continue
        if not all(is_market_snapshot_tradable(market, now) for market in (buy, sell)):
            continue
        validation = validate_pair(
            buy,
            sell,
            now=now,
            stale_after_seconds=settings.stale_after_seconds,
            huge_spread_pct=settings.huge_spread_pct,
        )
        if validation.capabilities.strategy_alert:
            opening, closing = midpoint_spread_pct(buy, sell)
            routes.append((opening, closing, buy, sell))
    route = max(routes, key=lambda item: item[0]) if routes else None
    ranked = sorted(rows, key=lambda row: (-abs(row[1].premium_pct), _market_label(row[0])))
    # Always show the selected route's two legs, then the strongest other premiums.
    selected_keys = {MarketKey.from_snapshot(m).key for m in route[2:]} if route else set()
    display = [row for row in ranked if MarketKey.from_snapshot(row[0]).key in selected_keys]
    display += [row for row in ranked if MarketKey.from_snapshot(row[0]).key not in selected_keys]
    display = display[:4]
    lines = [f"【溢价】{markets[0].symbol} · {len({m.exchange for m in markets})}所"]
    fallback = False
    for market, point in display:
        estimated = point.source == "mark_index_fallback"
        fallback |= estimated
        line = f"{_market_label(market)} P{point.premium_pct:+.2f}%{'*' if estimated else ''}"
        age = (now - utc(market.timestamp)).total_seconds()
        if not 0 <= age <= settings.stale_after_seconds:
            lines.append(line + "｜费率/成交额过期")
            continue
        rate = market.funding_next_rate_pct
        predicted = rate is not None or "funding_rate_pct" in market.estimated_fields
        if rate is None:
            rate = market.funding_rate_pct
        rate_text = f"{rate:+.4f}%" if rate is not None and isfinite(rate) else "—"
        interval = market.funding_interval_hours
        period = f"{interval:g}h" if interval is not None and interval > 0 else "周期未知"
        lines.append(
            line
            + f"｜{'预资费' if predicted else '资费'}{rate_text}/{period}"
            + f"｜24h额 {_compact_amount(market.volume_24h_usdt)} USDT"
        )
    if len(rows) > len(display):
        lines.append(f"另 {len(rows) - len(display)} 个市场命中")
    if route:
        opening, closing, buy, sell = route
        lines.append(
            f"多 {buy.exchange} → 空 {sell.exchange}｜开 {opening:+.3f}% / 平 {closing:+.3f}%"
        )
        # Disambiguate multiple same-exchange DEX markets in the chosen direction.
        if any(sum(m.exchange == leg.exchange for m in markets) > 1 for leg in (buy, sell)):
            lines[-1] = (
                f"多 {_market_label(buy)} → 空 {_market_label(sell)}"
                f"｜开 {opening:+.3f}% / 平 {closing:+.3f}%"
            )
        price_line = f"一档买 {buy.ask:.8g} / 卖 {sell.bid:.8g} USDT"
        if all(
            value is not None and isfinite(value) and value > 0
            for value in (buy.ask_size, sell.bid_size)
        ):
            depth = min(buy.ask * buy.ask_size, sell.bid * sell.bid_size)
            price_line += f"｜较薄档额 {_compact_amount(depth)}"
        lines.append(price_line)
        fee = 2 * fees.future_fee_pct
        lines.append(f"开仓双边费估 {fee:.3f}%｜扣费差 {opening - fee:+.3f}%（未计滑点）")
        multipliers = []
        for market in (buy, sell):
            if market.symbol_alias_price_multiplier != 1:
                multipliers.append(f"{market.exchange}价×{market.symbol_alias_price_multiplier:g}")
            if market.contract_size_multiplier is not None and market.contract_size_multiplier != 1:
                multipliers.append(f"{market.exchange}合约面值×{market.contract_size_multiplier:g}")
            if (market.original_quote_currency or market.quote) != "USDT":
                multipliers.append(f"{market.exchange}已折算USDT")
        if multipliers:
            lines.append("；".join(multipliers))
    else:
        lines.append("价差暂缺：盘口过期或未通过核验")
    max_age = max((now - utc(point.bucket_at)).total_seconds() for _, point in display)
    stamp = now.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
    lines.append(
        f"{stamp} 北京｜P样本≤{ceil(max(0, max_age))}秒" + ("｜*回退估算" if fallback else "")
    )
    return "\n".join(lines)


class MultiExchangePremiumMonitor:
    def __init__(self, app, service=None):
        self.app = app
        self.service = service or PremiumIndexQueryService()
        self._cursor = None
        self._next_allowed = {}
        self._locks = defaultdict(asyncio.Lock)
        self._request_at = defaultdict(float)
        self._exchange_retry_at = defaultdict(float)
        self._slots = asyncio.Semaphore(12)
        self.status = {
            "eligible_symbols": 0,
            "last_batch_symbols": 0,
            "last_batch_at": None,
            "last_sweep_seconds": None,
            "last_error_count": 0,
            "last_official_points": 0,
            "last_fallback_points": 0,
        }
        self._sweep_started = monotonic()

    async def _point(self, market):
        # At most two market queries/sec/exchange, with bounded global concurrency.
        async with self._slots:
            async with self._locks[market.exchange]:
                if monotonic() < self._exchange_retry_at[market.exchange]:
                    return None
                await asyncio.sleep(max(0, self._request_at[market.exchange] - monotonic()))
                self._request_at[market.exchange] = monotonic() + 0.5
            try:
                point = await asyncio.wait_for(
                    self.service.monitor_point(market, now=datetime.now(UTC)),
                    timeout=15,
                )
                if point is not None:
                    field = (
                        "last_fallback_points"
                        if point.source == "mark_index_fallback"
                        else "last_official_points"
                    )
                    self.status[field] += 1
                return (market, point) if point is not None else None
            except Exception as exc:
                self.status["last_error_count"] += 1
                if isinstance(exc, TimeoutError) or "429" in str(exc):
                    self._exchange_retry_at[market.exchange] = monotonic() + 60
                logger.debug(
                    "premium query failed for %s:%s",
                    market.exchange,
                    market.raw_symbol,
                    exc_info=True,
                )
                return None

    async def run_once(self):
        repo = self.app.state.settings_repo
        settings = await repo.get_risk_settings()
        if not settings.multi_exchange_premium_alert_enabled:
            return
        now = datetime.now(UTC)
        groups = eligible_groups(self.app.state.snapshot_store.get_markets(), settings, now)
        keys = sorted(groups)
        self.status["eligible_symbols"] = len(keys)
        remaining = [key for key in keys if self._cursor is None or key > self._cursor]
        if not remaining:
            self.status["last_sweep_seconds"] = round(monotonic() - self._sweep_started, 1)
            self._sweep_started = monotonic()
            self._cursor = None
            remaining = keys
        selected = remaining[:BATCH_SIZE]
        self.status["last_error_count"] = 0
        self.status["last_official_points"] = 0
        self.status["last_fallback_points"] = 0

        async def sample_group(key):
            results = await asyncio.gather(*(self._point(market) for market in groups[key]))
            await self._evaluate_results(results)

        # Evaluate each asset as soon as its legs finish; a slow unrelated market
        # must not age otherwise usable observations out of the freshness window.
        async with asyncio.TaskGroup() as tasks:
            for key in selected:
                tasks.create_task(sample_group(key))
        if selected:
            self._cursor = selected[-1]
        now = datetime.now(UTC)
        self._next_allowed = {
            key: until for key, until in self._next_allowed.items() if until > now
        }
        self.status.update(last_batch_symbols=len(selected), last_batch_at=now.isoformat())

    async def _evaluate_results(self, results):
        # A setting, blacklist or identity may have changed during network I/O.
        settings = await self.app.state.settings_repo.get_risk_settings()
        if not settings.multi_exchange_premium_alert_enabled:
            return
        now = datetime.now(UTC)
        current = eligible_groups(self.app.state.snapshot_store.get_markets(), settings, now)
        by_market = {
            MarketKey.from_snapshot(market).key: (key, market)
            for key, markets in current.items()
            for market in markets
        }
        observations = defaultdict(list)
        for result in results:
            if result is None:
                continue
            market, point = result
            live = by_market.get(MarketKey.from_snapshot(market).key)
            if live is None:
                continue
            key, latest = live
            if (latest.asset_id, latest.binding_revision, latest.binding_fingerprint) != (
                market.asset_id,
                market.binding_revision,
                market.binding_fingerprint,
            ):
                continue
            observations[key].append((latest, point))
        for key, rows in observations.items():
            hits = matching_points(rows, settings, now)
            if not hits or now < self._next_allowed.get(key, now):
                continue
            collector = getattr(self.app.state, "market_collector", None)
            message = build_premium_alert_message(
                hits,
                settings,
                now=now,
                fee_settings=getattr(collector, "fee_settings", None),
            )
            status = "sent"
            try:
                await self.app.state.feishu_notifier.send_text(message)
            except Exception:
                status = "failed"
                logger.exception("multi-exchange premium notification failed")
            self._next_allowed[key] = now + timedelta(
                seconds=settings.multi_exchange_premium_cooldown_seconds
                if status == "sent"
                else 60,
            )
            await self.app.state.alert_event_repo.create(
                AlertEvent(
                    rule_id="multi-exchange-premium",
                    opportunity_id="premium:" + ":".join(key),
                    symbol=hits[0][0].symbol,
                    status=status,
                    message=message,
                    created_at=now,
                )
            )

    async def run_loop(self, stop_event):
        try:
            while not stop_event.is_set():
                try:
                    notifier = self.app.state.feishu_notifier
                    if self.app.state.feishu_live_send_enabled and getattr(
                        getattr(notifier, "config", None), "webhook_url", ""
                    ):
                        await self.run_once()
                except Exception:
                    logger.exception("multi-exchange premium monitor failed")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=1)
                except TimeoutError:
                    pass
        finally:
            await self.service.aclose()
