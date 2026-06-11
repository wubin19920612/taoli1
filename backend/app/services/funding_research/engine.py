from collections import defaultdict
from datetime import UTC, datetime
from itertools import combinations

from app.models.market import MarketSnapshot, MarketType
from app.services.funding_research.formulas import estimate_snapshot_funding, minutes_until
from app.services.funding_research.models import (
    BasisAlignment,
    FormulaConfidence,
    FundingFormulaEstimate,
    FundingResearchCandidate,
    FundingResearchDecision,
    FundingResearchDepthStats,
    FundingResearchSettings,
)


def _basis_pct(snapshot: MarketSnapshot) -> float | None:
    if snapshot.mark_price is None or snapshot.index_price is None or snapshot.index_price <= 0:
        return None
    return (snapshot.mark_price - snapshot.index_price) / snapshot.index_price * 100


def _mid_price(snapshot: MarketSnapshot) -> float:
    return (snapshot.bid + snapshot.ask) / 2


def _relative_basis_pair(
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
) -> tuple[float | None, float | None]:
    long_basis = _basis_pct(long_leg)
    short_basis = _basis_pct(short_leg)
    if long_basis is not None and short_basis is not None:
        return long_basis, short_basis
    long_mid = _mid_price(long_leg)
    short_mid = _mid_price(short_leg)
    reference = (long_mid + short_mid) / 2
    if reference <= 0:
        return long_basis, short_basis
    return (
        (long_mid - reference) / reference * 100,
        (short_mid - reference) / reference * 100,
    )


def _top_of_book_depth_stats(
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
    settings: FundingResearchSettings,
) -> FundingResearchDepthStats:
    long_depth = long_leg.ask * long_leg.ask_size if long_leg.ask_size is not None else None
    short_depth = short_leg.bid * short_leg.bid_size if short_leg.bid_size is not None else None
    values: list[float] = []
    if long_depth is not None:
        values.append(long_depth)
    if short_depth is not None:
        values.append(short_depth)
    return FundingResearchDepthStats(
        long_entry_depth_usdt=long_depth,
        short_entry_depth_usdt=short_depth,
        min_entry_depth_usdt=min(values) if values else None,
        target_notional_usdt=settings.notional_per_symbol_usdt,
    )


def _weak_volume(long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> float | None:
    if long_leg.volume_24h_usdt is None or short_leg.volume_24h_usdt is None:
        return None
    return min(long_leg.volume_24h_usdt, short_leg.volume_24h_usdt)


def _source_priority(source: FormulaConfidence) -> int:
    return {
        "formula": 0,
        "predicted": 1,
        "fallback_current": 2,
        "uncertain": 3,
        "missing": 4,
    }[source]


def _combined_source(left: FormulaConfidence, right: FormulaConfidence) -> FormulaConfidence:
    return max((left, right), key=_source_priority)


def _funding_income_pct(side: str, funding_rate_pct: float) -> float:
    if side == "long":
        return -funding_rate_pct
    if side == "short":
        return funding_rate_pct
    raise ValueError(f"unknown side {side}")


def _funding_for_window_pct(
    estimate: FundingFormulaEstimate,
    target_holding_hours: float,
) -> float | None:
    if estimate.funding_rate_pct is None:
        return None
    if estimate.interval_hours is None or estimate.interval_hours <= 0:
        return estimate.funding_rate_pct
    cycles = max(target_holding_hours / estimate.interval_hours, 1.0)
    return estimate.funding_rate_pct * cycles


def _basis_alignment(
    long_basis_pct: float | None,
    short_basis_pct: float | None,
    settings: FundingResearchSettings,
) -> BasisAlignment:
    if long_basis_pct is None or short_basis_pct is None:
        return "neutral"
    diff = short_basis_pct - long_basis_pct
    if abs(diff) <= settings.basis_neutral_threshold_pct:
        return "neutral"
    return "aligned" if diff > 0 else "conflicted"


def _expected_basis_change_pct(
    alignment: BasisAlignment,
    basis_diff_pct: float | None,
    settings: FundingResearchSettings,
) -> float:
    if basis_diff_pct is None or alignment == "neutral":
        return 0.0
    magnitude = abs(basis_diff_pct)
    if alignment == "aligned":
        return min(magnitude * settings.aligned_basis_capture_ratio, settings.max_expected_basis_pct)
    return -magnitude * settings.conflicted_basis_penalty_ratio


def _base_risk_pct(
    *,
    basis_diff_pct: float | None,
    weak_volume: float | None,
    depth_stats: FundingResearchDepthStats | None,
    minutes_to_settlement: float | None,
    funding_source: FormulaConfidence,
    settings: FundingResearchSettings,
) -> tuple[float, list[str]]:
    labels: list[str] = []
    risk = abs(basis_diff_pct or 0.0) * settings.basis_volatility_weight
    if weak_volume is None or weak_volume < settings.min_volume_24h_usdt:
        risk += settings.low_liquidity_penalty_pct
        labels.append("LOW_VOLUME")
    depth_usdt = depth_stats.min_entry_depth_usdt if depth_stats is not None else None
    if depth_usdt is None or depth_usdt < settings.notional_per_symbol_usdt * settings.min_depth_multiple:
        risk += settings.thin_depth_penalty_pct
        labels.append("THIN_DEPTH")
    if funding_source in {"missing", "uncertain", "fallback_current"}:
        risk += settings.formula_uncertainty_penalty_pct
        labels.append("FUNDING_UNCERTAIN")
    if minutes_to_settlement is None:
        labels.append("MISSING_SETTLEMENT")
    elif minutes_to_settlement < settings.settlement_crowding_minutes:
        risk += settings.settlement_crowding_penalty_pct
        labels.append("SETTLEMENT_CROWDING")
    return risk, labels


def _score_candidate(
    *,
    expected_net_funding_pct: float | None,
    expected_basis_change_pct: float,
    risk_buffer_pct: float,
    alignment: BasisAlignment,
    weak_volume: float | None,
    depth_stats: FundingResearchDepthStats | None,
    funding_source: FormulaConfidence,
    settings: FundingResearchSettings,
) -> float:
    score = 0.0
    funding = expected_net_funding_pct or 0.0
    if funding >= 1.5:
        score += 32
    elif funding >= 1.0:
        score += 25
    elif funding >= 0.6:
        score += 18
    elif funding >= 0.3:
        score += 10
    elif funding > 0:
        score += 4

    if alignment == "aligned":
        score += 25
    elif alignment == "neutral":
        score += 8
    else:
        score -= 25

    if expected_basis_change_pct > 0:
        score += min(expected_basis_change_pct * 12, 10)
    elif expected_basis_change_pct < 0:
        score += max(expected_basis_change_pct * 8, -15)

    if weak_volume is not None and weak_volume >= settings.min_volume_24h_usdt:
        score += 15
    elif weak_volume is not None and weak_volume >= settings.min_volume_24h_usdt * 0.25:
        score += 5
    else:
        score -= 15

    depth_usdt = depth_stats.min_entry_depth_usdt if depth_stats is not None else None
    if depth_usdt is not None and depth_usdt >= settings.notional_per_symbol_usdt * settings.min_depth_multiple:
        score += 10
    elif depth_usdt is not None and depth_usdt >= settings.notional_per_symbol_usdt:
        score -= 2
    else:
        score -= 10

    if depth_stats is not None and depth_stats.slippage_loss_pct is not None:
        score -= min(max(depth_stats.slippage_loss_pct, 0) * 10, 15)

    if funding_source in {"formula", "predicted"}:
        score += 10
    elif funding_source == "fallback_current":
        score -= 8
    else:
        score -= 25

    score -= min(risk_buffer_pct * 8, 25)
    return max(min(score, 100), 0)


def _decision(
    ev_pct: float | None,
    score: float,
    reasons: list[str],
    *,
    expected_net_funding_pct: float | None,
    risk_labels: list[str],
    settings: FundingResearchSettings,
) -> FundingResearchDecision:
    if ev_pct is None:
        return "NO_TRADE"
    hard_reasons = [
        reason
        for reason in reasons
        if reason != "entry depth below full safety multiple"
    ]
    if hard_reasons:
        return "NO_TRADE"
    if ev_pct >= settings.min_trade_ev_pct and score >= settings.min_trade_score:
        return "TRADE"
    if ev_pct >= settings.min_small_trade_ev_pct and score >= settings.min_small_trade_score:
        return "SMALL_TRADE"
    if ev_pct >= settings.min_watch_ev_pct and score >= settings.min_watch_score:
        return "WATCH"
    if (
        "THIN_DEPTH" in risk_labels
        and (expected_net_funding_pct or 0.0) >= settings.min_thin_depth_watch_funding_pct
        and ev_pct >= settings.min_thin_depth_watch_ev_pct
        and score >= settings.min_thin_depth_watch_score
    ):
        return "WATCH"
    return "NO_TRADE"


def build_candidate(
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
    *,
    settings: FundingResearchSettings | None = None,
    now: datetime | None = None,
    long_funding_estimate: FundingFormulaEstimate | None = None,
    short_funding_estimate: FundingFormulaEstimate | None = None,
    depth_stats: FundingResearchDepthStats | None = None,
) -> FundingResearchCandidate:
    resolved = settings or FundingResearchSettings()
    current = now or datetime.now(UTC)
    long_est = long_funding_estimate or estimate_snapshot_funding(long_leg)
    short_est = short_funding_estimate or estimate_snapshot_funding(short_leg)
    funding_source = _combined_source(long_est.source, short_est.source)

    long_window_funding = _funding_for_window_pct(long_est, resolved.target_holding_hours)
    short_window_funding = _funding_for_window_pct(short_est, resolved.target_holding_hours)
    if long_window_funding is None or short_window_funding is None:
        expected_net_funding = None
    else:
        expected_net_funding = (
            _funding_income_pct("long", long_window_funding)
            + _funding_income_pct("short", short_window_funding)
        )

    long_basis, short_basis = _relative_basis_pair(long_leg, short_leg)
    basis_diff = None if long_basis is None or short_basis is None else short_basis - long_basis
    alignment = _basis_alignment(long_basis, short_basis, resolved)
    basis_change = _expected_basis_change_pct(alignment, basis_diff, resolved)
    weak_volume = _weak_volume(long_leg, short_leg)
    resolved_depth_stats = depth_stats or _top_of_book_depth_stats(long_leg, short_leg, resolved)
    depth = resolved_depth_stats.min_entry_depth_usdt
    next_times = [value for value in (long_est.next_time, short_est.next_time) if value is not None]
    next_settlement = min(next_times) if next_times else None
    minutes_to_settlement = minutes_until(next_settlement, current)
    risk, risk_labels = _base_risk_pct(
        basis_diff_pct=basis_diff,
        weak_volume=weak_volume,
        depth_stats=resolved_depth_stats,
        minutes_to_settlement=minutes_to_settlement,
        funding_source=funding_source,
        settings=resolved,
    )
    cost = resolved.open_fee_pct + resolved.close_fee_pct + resolved.base_slippage_pct
    ev = None if expected_net_funding is None else expected_net_funding + basis_change - cost - risk
    reasons: list[str] = []
    if funding_source == "missing":
        reasons.append("missing funding estimate")
    if minutes_to_settlement is None:
        reasons.append("missing settlement time")
    elif (
        minutes_to_settlement < resolved.min_minutes_to_settlement
        or minutes_to_settlement > resolved.max_minutes_to_settlement
    ):
        reasons.append("outside settlement window")
    if weak_volume is None or weak_volume < resolved.min_volume_24h_usdt * 0.1:
        reasons.append("weak leg volume too low")
    if depth is None or depth < resolved.notional_per_symbol_usdt:
        reasons.append("entry depth too thin")
    elif depth < resolved.notional_per_symbol_usdt * resolved.min_depth_multiple:
        reasons.append("entry depth below full safety multiple")

    score = _score_candidate(
        expected_net_funding_pct=expected_net_funding,
        expected_basis_change_pct=basis_change,
        risk_buffer_pct=risk,
        alignment=alignment,
        weak_volume=weak_volume,
        depth_stats=resolved_depth_stats,
        funding_source=funding_source,
        settings=resolved,
    )
    return FundingResearchCandidate(
        symbol=long_leg.symbol,
        long_exchange=long_leg.exchange,
        short_exchange=short_leg.exchange,
        long_funding_pct=long_est.funding_rate_pct,
        short_funding_pct=short_est.funding_rate_pct,
        expected_net_funding_pct=expected_net_funding,
        expected_basis_change_pct=basis_change,
        estimated_cost_pct=cost,
        risk_buffer_pct=risk,
        ev_pct=ev,
        score=score,
        decision=_decision(
            ev,
            score,
            reasons,
            expected_net_funding_pct=expected_net_funding,
            risk_labels=risk_labels,
            settings=resolved,
        ),
        basis_alignment=alignment,
        basis_diff_pct=basis_diff,
        long_basis_pct=long_basis,
        short_basis_pct=short_basis,
        funding_window_hours=resolved.target_holding_hours,
        next_settlement_time=next_settlement,
        minutes_to_settlement=minutes_to_settlement,
        funding_source=funding_source,
        depth_stats=resolved_depth_stats,
        risk_labels=risk_labels,
        reasons=reasons,
    )


def _best_direction_for_pair(
    first: MarketSnapshot,
    second: MarketSnapshot,
    *,
    settings: FundingResearchSettings,
    now: datetime,
) -> FundingResearchCandidate:
    first_long = build_candidate(first, second, settings=settings, now=now)
    second_long = build_candidate(second, first, settings=settings, now=now)
    first_ev = first_long.ev_pct if first_long.ev_pct is not None else -999
    second_ev = second_long.ev_pct if second_long.ev_pct is not None else -999
    if first_ev != second_ev:
        return first_long if first_ev > second_ev else second_long
    return first_long if first_long.score >= second_long.score else second_long


def build_funding_research_candidates(
    markets: list[MarketSnapshot],
    *,
    settings: FundingResearchSettings | None = None,
    now: datetime | None = None,
) -> list[FundingResearchCandidate]:
    resolved = settings or FundingResearchSettings()
    current = now or datetime.now(UTC)
    by_symbol: dict[str, list[MarketSnapshot]] = defaultdict(list)
    for market in markets:
        if market.market_type != MarketType.FUTURE:
            continue
        by_symbol[market.symbol].append(market)

    candidates: list[FundingResearchCandidate] = []
    for symbol_markets in by_symbol.values():
        for first, second in combinations(symbol_markets, 2):
            candidates.append(_best_direction_for_pair(first, second, settings=resolved, now=current))
    return sorted(
        candidates,
        key=lambda item: (
            item.decision == "TRADE",
            item.decision == "SMALL_TRADE",
            item.ev_pct if item.ev_pct is not None else -999,
            item.score,
        ),
        reverse=True,
    )
