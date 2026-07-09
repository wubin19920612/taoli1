from datetime import UTC, datetime, timedelta
from math import floor, isfinite

from app.db.pair_monitor_repository import PairMonitorRepository
from app.models.market import MarketSnapshot
from app.models.pair_monitor import (
    PairMonitorHistory,
    PairMonitorLeg,
    PairMonitorPoint,
    PairMonitorPriceField,
    PairMonitorRule,
    PairMonitorSampleResult,
    PairMonitorSampleStatus,
    PairMonitorValueStats,
)


def _bucket_time(value: datetime, seconds: int) -> datetime:
    timestamp = floor(value.timestamp() / seconds) * seconds
    return datetime.fromtimestamp(timestamp, UTC)


def _mid_price(market: MarketSnapshot) -> float:
    return (market.bid + market.ask) / 2


def _finite_positive(value: float | None) -> float | None:
    if value is None or not isfinite(value) or value <= 0:
        return None
    return value


def _resolve_price(
    market: MarketSnapshot,
    preferred: PairMonitorPriceField,
) -> tuple[float, PairMonitorPriceField] | None:
    candidates: list[tuple[float | None, PairMonitorPriceField]]
    if preferred == PairMonitorPriceField.AUTO:
        candidates = [
            (market.mark_price, PairMonitorPriceField.MARK_PRICE),
            (_mid_price(market), PairMonitorPriceField.MID_PRICE),
            (market.index_price, PairMonitorPriceField.INDEX_PRICE),
        ]
    elif preferred == PairMonitorPriceField.MID_PRICE:
        candidates = [(_mid_price(market), PairMonitorPriceField.MID_PRICE)]
    elif preferred == PairMonitorPriceField.MARK_PRICE:
        candidates = [(market.mark_price, PairMonitorPriceField.MARK_PRICE)]
    elif preferred == PairMonitorPriceField.INDEX_PRICE:
        candidates = [(market.index_price, PairMonitorPriceField.INDEX_PRICE)]
    elif preferred == PairMonitorPriceField.BID:
        candidates = [(market.bid, PairMonitorPriceField.BID)]
    else:
        candidates = [(market.ask, PairMonitorPriceField.ASK)]
    for value, field in candidates:
        resolved = _finite_positive(value)
        if resolved is not None:
            return resolved, field
    return None


def find_pair_monitor_market(
    markets: list[MarketSnapshot],
    leg: PairMonitorLeg,
) -> MarketSnapshot | None:
    return next(
        (
            market
            for market in markets
            if market.exchange.lower() == leg.exchange
            and market.symbol == leg.symbol
            and market.market_type == leg.market_type
        ),
        None,
    )


def build_pair_monitor_point(
    rule: PairMonitorRule,
    markets: list[MarketSnapshot],
    now: datetime | None = None,
) -> PairMonitorSampleResult:
    observed_at = now or datetime.now(UTC)
    leg1_market = find_pair_monitor_market(markets, rule.leg1)
    leg2_market = find_pair_monitor_market(markets, rule.leg2)
    if leg1_market is None:
        return PairMonitorSampleResult(
            rule_id=rule.id,
            status=PairMonitorSampleStatus.SKIPPED,
            reason=f"market not found: {rule.leg1.exchange}:{rule.leg1.symbol}:{rule.leg1.market_type.value}",
        )
    if leg2_market is None:
        return PairMonitorSampleResult(
            rule_id=rule.id,
            status=PairMonitorSampleStatus.SKIPPED,
            reason=f"market not found: {rule.leg2.exchange}:{rule.leg2.symbol}:{rule.leg2.market_type.value}",
        )

    leg1_price = _resolve_price(leg1_market, rule.leg1.price_field)
    leg2_price = _resolve_price(leg2_market, rule.leg2.price_field)
    if leg1_price is None:
        return PairMonitorSampleResult(
            rule_id=rule.id,
            status=PairMonitorSampleStatus.SKIPPED,
            reason=f"price unavailable: {rule.leg1.exchange}:{rule.leg1.symbol}",
        )
    if leg2_price is None:
        return PairMonitorSampleResult(
            rule_id=rule.id,
            status=PairMonitorSampleStatus.SKIPPED,
            reason=f"price unavailable: {rule.leg2.exchange}:{rule.leg2.symbol}",
        )

    price1, field1 = leg1_price
    price2, field2 = leg2_price
    spread_abs = price2 - price1
    spread_pct = spread_abs / price1 * 100
    return PairMonitorSampleResult(
        rule_id=rule.id,
        status=PairMonitorSampleStatus.RECORDED,
        point=PairMonitorPoint(
            rule_id=rule.id,
            observed_at=observed_at,
            bucket_at=_bucket_time(observed_at, rule.sample_interval_seconds),
            leg1_price=price1,
            leg2_price=price2,
            spread_abs=spread_abs,
            spread_pct=spread_pct,
            leg1_funding_rate_pct=leg1_market.funding_rate_pct,
            leg2_funding_rate_pct=leg2_market.funding_rate_pct,
            leg1_funding_next_rate_pct=leg1_market.funding_next_rate_pct,
            leg2_funding_next_rate_pct=leg2_market.funding_next_rate_pct,
            leg1_funding_next_time=leg1_market.funding_next_time,
            leg2_funding_next_time=leg2_market.funding_next_time,
            leg1_volume_24h_usdt=leg1_market.volume_24h_usdt,
            leg2_volume_24h_usdt=leg2_market.volume_24h_usdt,
            leg1_price_field=field1,
            leg2_price_field=field2,
            leg1_market_timestamp=leg1_market.timestamp,
            leg2_market_timestamp=leg2_market.timestamp,
        ),
    )


def _stats(points: list[PairMonitorPoint], field: str) -> PairMonitorValueStats:
    values = [
        float(value)
        for point in points
        if isinstance(value := getattr(point, field), int | float)
    ]
    if not values:
        return PairMonitorValueStats()
    return PairMonitorValueStats(
        min=min(values),
        max=max(values),
        mean=sum(values) / len(values),
        current=values[-1],
    )


def build_pair_monitor_history(
    rule: PairMonitorRule,
    points: list[PairMonitorPoint],
) -> PairMonitorHistory:
    chronological = sorted(points, key=lambda point: point.bucket_at)
    latest = chronological[-1] if chronological else None
    return PairMonitorHistory(
        rule=rule,
        count=len(chronological),
        first_seen_at=chronological[0].bucket_at if chronological else None,
        last_seen_at=latest.bucket_at if latest is not None else None,
        latest=latest,
        spread_pct=_stats(chronological, "spread_pct"),
        leg1_funding_rate_pct=_stats(chronological, "leg1_funding_rate_pct"),
        leg2_funding_rate_pct=_stats(chronological, "leg2_funding_rate_pct"),
        points=chronological,
    )


class PairMonitorSampler:
    def __init__(self, repository: PairMonitorRepository) -> None:
        self.repository = repository
        self._last_vacuum_at: datetime | None = None

    async def sample(
        self,
        markets: list[MarketSnapshot],
        now: datetime | None = None,
        rule_id: str | None = None,
    ) -> list[PairMonitorSampleResult]:
        observed_at = now or datetime.now(UTC)
        rules = await self.repository.list_rules()
        if rule_id is not None:
            rules = [rule for rule in rules if rule.id == rule_id]
        results: list[PairMonitorSampleResult] = []
        deleted_count = 0
        for rule in rules:
            if not rule.enabled:
                results.append(
                    PairMonitorSampleResult(
                        rule_id=rule.id,
                        status=PairMonitorSampleStatus.SKIPPED,
                        reason="rule disabled",
                    )
                )
                continue
            latest = await self.repository.latest_point(rule.id)
            if latest is not None:
                elapsed = (observed_at - latest.bucket_at).total_seconds()
                if elapsed < rule.sample_interval_seconds:
                    results.append(
                        PairMonitorSampleResult(
                            rule_id=rule.id,
                            status=PairMonitorSampleStatus.SKIPPED,
                            reason="sample interval not reached",
                        )
                    )
                    continue
            result = build_pair_monitor_point(rule, markets, observed_at)
            if result.point is not None:
                await self.repository.upsert_point(result.point)
                deleted_count += await self.repository.prune_points_before(
                    rule.id,
                    observed_at - timedelta(days=rule.retention_days),
                )
            results.append(result)
        if deleted_count > 0:
            await self._maybe_vacuum(observed_at)
        return results

    async def _maybe_vacuum(self, now: datetime) -> None:
        if self._last_vacuum_at is not None:
            elapsed = (now - self._last_vacuum_at).total_seconds()
            if elapsed < 86_400:
                return
        await self.repository.vacuum()
        self._last_vacuum_at = now
