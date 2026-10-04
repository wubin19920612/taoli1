from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha1
from statistics import median

from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity_radar import (
    OpportunityRadarCandidate,
    OpportunityRadarPreview,
    OpportunityRadarSettings,
    SUPPORTED_RADAR_EXCHANGES,
)


class OpportunityRadarAlertEngine:
    def __init__(self) -> None:
        self._hits: dict[str, int] = {}
        self._alerted_active: set[str] = set()
        self._last_sent: dict[str, datetime] = {}

    def evaluate(
        self,
        candidates: list[OpportunityRadarCandidate],
        settings: OpportunityRadarSettings,
        *,
        now: datetime | None = None,
    ) -> list[OpportunityRadarCandidate]:
        current = now or datetime.now(UTC)
        eligible = {
            candidate.id: candidate
            for candidate in candidates
            if candidate.score >= settings.min_alert_score
        }
        for candidate_id in list(self._hits):
            if candidate_id not in eligible:
                self._hits.pop(candidate_id, None)
                self._alerted_active.discard(candidate_id)

        matches: list[OpportunityRadarCandidate] = []
        for candidate_id, candidate in eligible.items():
            hits = self._hits.get(candidate_id, 0) + 1
            self._hits[candidate_id] = hits
            if hits < settings.alert_consecutive_hits:
                continue
            if candidate_id in self._alerted_active:
                continue
            last_sent = self._last_sent.get(candidate_id)
            if last_sent is not None:
                elapsed = (current - last_sent).total_seconds()
                if elapsed < settings.alert_cooldown_seconds:
                    continue
            self._alerted_active.add(candidate_id)
            self._last_sent[candidate_id] = current
            matches.append(candidate)
        return matches

    def release_failed(self, candidate_id: str) -> None:
        self._alerted_active.discard(candidate_id)
        self._last_sent.pop(candidate_id, None)

    def reset_active(self) -> None:
        self._hits.clear()
        self._alerted_active.clear()


def build_opportunity_radar_alert_message(
    candidate: OpportunityRadarCandidate,
    *,
    observed_at: datetime,
) -> str:
    hourly_edge = (
        "-"
        if candidate.hourly_funding_edge_pct is None
        else f"{candidate.hourly_funding_edge_pct:+.4f}%"
    )
    long_funding = (
        "-" if candidate.long_funding_pct is None else f"{candidate.long_funding_pct:+.4f}%"
    )
    short_funding = (
        "-" if candidate.short_funding_pct is None else f"{candidate.short_funding_pct:+.4f}%"
    )
    return "\n".join(
        [
            "[机会雷达] 极端溢价 / 低价差",
            f"标的：{candidate.symbol} | 评分：{candidate.score:.0f} | 信号：{candidate.signal_level}",
            f"方向：多 {candidate.long_exchange} / 空 {candidate.short_exchange}",
            (
                f"溢价：{candidate.anchor_exchange} {candidate.anchor_premium_pct:+.3f}% / "
                f"{candidate.peer_exchange} {candidate.peer_premium_pct:+.3f}%"
            ),
            f"跨所溢价差：{candidate.relative_premium_gap_pct:.3f}%",
            f"可执行价差：{candidate.entry_spread_pct:+.3f}%",
            (
                f"资金：多腿 {long_funding}/{candidate.long_funding_interval_hours or '-'}h / "
                f"空腿 {short_funding}/{candidate.short_funding_interval_hours or '-'}h"
            ),
            f"每小时资金优势：{hourly_edge}",
            f"时间：{observed_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        ]
    )


def _premium_pct(snapshot: MarketSnapshot) -> float | None:
    if snapshot.mark_price is None or snapshot.index_price is None or snapshot.index_price <= 0:
        return None
    return (snapshot.mark_price - snapshot.index_price) / snapshot.index_price * 100


def _data_age_seconds(snapshot: MarketSnapshot, now: datetime) -> float:
    timestamp = snapshot.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return max((now - timestamp.astimezone(UTC)).total_seconds(), 0.0)


def _funding_rate(snapshot: MarketSnapshot) -> float | None:
    if snapshot.funding_next_rate_pct is not None:
        return snapshot.funding_next_rate_pct
    return snapshot.funding_rate_pct


def _hourly_funding(snapshot: MarketSnapshot) -> float | None:
    rate = _funding_rate(snapshot)
    interval = snapshot.funding_interval_hours
    if rate is None or interval is None or interval <= 0:
        return None
    return rate / interval


def _entry_spread_pct(long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> float:
    midpoint = (long_leg.ask + short_leg.bid) / 2
    return (short_leg.bid - long_leg.ask) / midpoint * 100


def _entry_depth_usdt(long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> float | None:
    values: list[float] = []
    if long_leg.ask_size is not None:
        values.append(long_leg.ask * long_leg.ask_size)
    if short_leg.bid_size is not None:
        values.append(short_leg.bid * short_leg.bid_size)
    return min(values) if values else None


def _known_volume(long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> float | None:
    if long_leg.volume_24h_usdt is None or short_leg.volume_24h_usdt is None:
        return None
    return min(long_leg.volume_24h_usdt, short_leg.volume_24h_usdt)


def _candidate_id(symbol: str, long_exchange: str, short_exchange: str) -> str:
    raw = f"{symbol}:{long_exchange}:{short_exchange}"
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def _direction_allowed(premium: float, settings: OpportunityRadarSettings) -> bool:
    if premium < 0:
        return settings.premium_direction in {"negative", "both"}
    if premium > 0:
        return settings.premium_direction in {"positive", "both"}
    return False


def _score(
    *,
    anchor_premium: float,
    relative_gap: float,
    spread: float,
    hourly_edge: float | None,
    depth: float | None,
    settings: OpportunityRadarSettings,
) -> float:
    premium_ratio = abs(anchor_premium) / max(settings.min_abs_premium_pct, 0.0001)
    gap_ratio = relative_gap / max(settings.min_relative_premium_gap_pct, 0.0001)
    spread_room = 1 - abs(spread) / max(settings.max_abs_entry_spread_pct, 0.0001)
    premium_score = min(premium_ratio, 2) / 2 * 40
    gap_score = min(gap_ratio, 2) / 2 * 30
    spread_score = max(min(spread_room, 1), 0) * 20
    funding_score = 5 if hourly_edge is not None and hourly_edge >= 0 else 0
    required_depth = settings.notional_per_symbol_usdt * settings.min_depth_multiple
    depth_score = 5 if depth is not None and depth >= required_depth else 0
    return round(premium_score + gap_score + spread_score + funding_score + depth_score, 1)


def _signal_level(score: float) -> str:
    if score >= 75:
        return "HIGH"
    if score >= 55:
        return "MEDIUM"
    return "WATCH"


def build_opportunity_radar_preview(
    markets: list[MarketSnapshot],
    settings: OpportunityRadarSettings | None = None,
    *,
    now: datetime | None = None,
) -> OpportunityRadarPreview:
    resolved = settings or OpportunityRadarSettings()
    current = now or datetime.now(UTC)
    supported = set(SUPPORTED_RADAR_EXCHANGES)
    by_symbol: dict[str, list[MarketSnapshot]] = defaultdict(list)
    for market in markets:
        if market.market_type != MarketType.FUTURE:
            continue
        if market.exchange.lower() not in supported:
            continue
        by_symbol[market.symbol].append(market)

    anchor_markets = 0
    pairs_evaluated = 0
    candidates: list[OpportunityRadarCandidate] = []
    if resolved.enabled:
        peer_exchange_set = set(resolved.peer_exchanges)
        for symbol, symbol_markets in by_symbol.items():
            anchors = [
                item
                for item in symbol_markets
                if item.exchange.lower() == resolved.anchor_exchange
            ]
            peers = [
                item
                for item in symbol_markets
                if item.exchange.lower() in peer_exchange_set
            ]
            for anchor in anchors:
                anchor_markets += 1
                anchor_premium = _premium_pct(anchor)
                peer_premiums = [
                    value
                    for peer in peers
                    if (value := _premium_pct(peer)) is not None
                ]
                if anchor_premium is None or not peer_premiums:
                    continue
                if abs(anchor_premium) < resolved.min_abs_premium_pct:
                    continue
                if not _direction_allowed(anchor_premium, resolved):
                    continue
                peer_median = median(peer_premiums)
                median_gap = (
                    peer_median - anchor_premium
                    if anchor_premium < 0
                    else anchor_premium - peer_median
                )
                if median_gap < resolved.min_relative_premium_gap_pct:
                    continue

                for peer in peers:
                    peer_premium = _premium_pct(peer)
                    if peer_premium is None:
                        continue
                    pairs_evaluated += 1
                    relative_gap = (
                        peer_premium - anchor_premium
                        if anchor_premium < 0
                        else anchor_premium - peer_premium
                    )
                    if relative_gap < resolved.min_relative_premium_gap_pct:
                        continue
                    if anchor_premium < 0:
                        long_leg, short_leg = anchor, peer
                        direction = "LONG_ANCHOR_SHORT_PEER"
                    else:
                        long_leg, short_leg = peer, anchor
                        direction = "LONG_PEER_SHORT_ANCHOR"

                    age = max(
                        _data_age_seconds(long_leg, current),
                        _data_age_seconds(short_leg, current),
                    )
                    if age > resolved.max_data_age_seconds:
                        continue
                    volume = _known_volume(long_leg, short_leg)
                    if resolved.min_volume_24h_usdt > 0 and (
                        volume is None or volume < resolved.min_volume_24h_usdt
                    ):
                        continue
                    spread = _entry_spread_pct(long_leg, short_leg)
                    if abs(spread) > resolved.max_abs_entry_spread_pct:
                        continue
                    depth = _entry_depth_usdt(long_leg, short_leg)
                    min_depth = resolved.notional_per_symbol_usdt * resolved.min_depth_multiple
                    if depth is not None and depth < min_depth:
                        continue

                    long_rate = _funding_rate(long_leg)
                    short_rate = _funding_rate(short_leg)
                    long_hourly = _hourly_funding(long_leg)
                    short_hourly = _hourly_funding(short_leg)
                    hourly_edge = (
                        short_hourly - long_hourly
                        if long_hourly is not None and short_hourly is not None
                        else None
                    )
                    if resolved.require_funding_alignment and (
                        hourly_edge is None or hourly_edge < resolved.min_hourly_funding_edge_pct
                    ):
                        continue

                    risk_labels: list[str] = []
                    reasons = [
                        "anchor premium crossed the configured extreme threshold",
                        "relative premium gap crossed the configured threshold",
                        "executable spread remains inside the configured trial range",
                    ]
                    if depth is None:
                        risk_labels.append("UNKNOWN_DEPTH")
                    if hourly_edge is None:
                        risk_labels.append("FUNDING_UNCONFIRMED")
                    elif hourly_edge < 0:
                        risk_labels.append("FUNDING_AGAINST")
                    else:
                        reasons.append("hourly funding edge supports the selected direction")

                    score = _score(
                        anchor_premium=anchor_premium,
                        relative_gap=relative_gap,
                        spread=spread,
                        hourly_edge=hourly_edge,
                        depth=depth,
                        settings=resolved,
                    )
                    candidates.append(
                        OpportunityRadarCandidate(
                            id=_candidate_id(symbol, long_leg.exchange, short_leg.exchange),
                            symbol=symbol,
                            signal_level=_signal_level(score),
                            score=score,
                            direction=direction,
                            long_exchange=long_leg.exchange,
                            short_exchange=short_leg.exchange,
                            anchor_exchange=anchor.exchange,
                            peer_exchange=peer.exchange,
                            anchor_premium_pct=anchor_premium,
                            peer_premium_pct=peer_premium,
                            peer_median_premium_pct=peer_median,
                            relative_premium_gap_pct=relative_gap,
                            entry_spread_pct=spread,
                            long_entry_price=long_leg.ask,
                            short_entry_price=short_leg.bid,
                            long_funding_pct=long_rate,
                            short_funding_pct=short_rate,
                            long_funding_interval_hours=long_leg.funding_interval_hours,
                            short_funding_interval_hours=short_leg.funding_interval_hours,
                            hourly_funding_edge_pct=hourly_edge,
                            volume_24h_usdt=volume,
                            depth_usdt=depth,
                            data_age_seconds=round(age, 1),
                            reasons=reasons,
                            risk_labels=risk_labels,
                        )
                    )

    candidates = sorted(
        candidates,
        key=lambda item: (
            item.score,
            item.relative_premium_gap_pct,
            -abs(item.entry_spread_pct),
            item.volume_24h_usdt or 0,
        ),
        reverse=True,
    )[: resolved.max_candidates]
    return OpportunityRadarPreview(
        observed_at=current,
        settings=resolved,
        anchor_markets=anchor_markets,
        total_pairs_evaluated=pairs_evaluated,
        displayed_candidates=len(candidates),
        high_count=sum(item.signal_level == "HIGH" for item in candidates),
        medium_count=sum(item.signal_level == "MEDIUM" for item in candidates),
        watch_count=sum(item.signal_level == "WATCH" for item in candidates),
        candidates=candidates,
    )
