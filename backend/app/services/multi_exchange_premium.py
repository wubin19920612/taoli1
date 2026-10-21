"""Bounded, rotating all-market premium monitoring, independent of Astro orders."""

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from math import isfinite
from time import monotonic

from app.models.alert import AlertEvent
from app.models.market import MarketSnapshot, MarketType
from app.models.market_identity import MarketKey, utc
from app.models.premium_index import SUPPORTED_PREMIUM_INDEX_EXCHANGES
from app.models.settings import RiskSettings
from app.services.data_filters import filter_markets
from app.services.premium_index_query import PremiumIndexQueryService

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


def build_premium_alert_message(rows, settings: RiskSettings) -> str:
    lines = [
        "[多交易所实时溢价指数告警]",
        f"标的：{rows[0][0].symbol} | 资产：{rows[0][0].asset_id}",
        f"条件：至少两个交易所 |P| > {settings.multi_exchange_premium_threshold_pct:g}%",
    ]
    for market, point in sorted(rows, key=lambda row: (row[0].exchange, row[0].raw_symbol)):
        source = (
            "标记价/指数价偏离（回退估算）"
            if point.source == "mark_index_fallback"
            else point.source
        )
        lines.append(
            f"{market.exchange} / DEX={market.dex or 'main'} / {market.raw_symbol}："
            f"{point.premium_pct:+.4f}% | {source} | "
            f"{utc(point.bucket_at):%Y-%m-%d %H:%M:%S} UTC"
        )
    lines.append("仅溢价监控，不代表可成交收益；分批轮询存在扫描延迟。")
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
            message = build_premium_alert_message(hits, settings)
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
