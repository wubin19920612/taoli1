from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha1
from itertools import combinations

from app.models.funding_arbitrage import (
    AdlRiskLevel,
    FundingArbitrageCandidate,
    FundingArbitrageDecision,
    FundingArbitragePreview,
    FundingArbitrageSettings,
    FundingSource,
)
from app.models.market import MarketSnapshot, MarketType

HYPERLIQUID_EXCHANGE = "hyperliquid"
DEFAULT_OPEN_COST_PCT = 0.02
DEFAULT_CLOSE_COST_PCT = 0.02


def _candidate_id(kind: str, symbol: str, long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> str:
    raw = (
        f"{kind}:{symbol}:{long_leg.exchange}:{long_leg.market_type}:"
        f"{short_leg.exchange}:{short_leg.market_type}"
    )
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def _side_next_funding_pct(snapshot: MarketSnapshot) -> tuple[float | None, FundingSource]:
    if snapshot.market_type == MarketType.SPOT:
        return 0.0, "predicted"
    if snapshot.funding_next_rate_pct is not None:
        return snapshot.funding_next_rate_pct, "predicted"
    if snapshot.funding_rate_pct is not None:
        return snapshot.funding_rate_pct, "fallback_current"
    return None, "missing"


def _side_current_funding_pct(snapshot: MarketSnapshot) -> float | None:
    if snapshot.market_type == MarketType.SPOT:
        return 0.0
    return snapshot.funding_rate_pct


def _funding_source(left: FundingSource, right: FundingSource) -> FundingSource:
    if left == "missing" or right == "missing":
        return "missing"
    if left == "fallback_current" or right == "fallback_current":
        return "fallback_current"
    return "predicted"


def _basis_pct(long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> tuple[float, float]:
    entry = 2 * (short_leg.bid - long_leg.ask) / (short_leg.bid + long_leg.ask) * 100
    exit_value = 2 * (short_leg.ask - long_leg.bid) / (short_leg.ask + long_leg.bid) * 100
    return entry, exit_value


def _mark_index_diff_pct(snapshot: MarketSnapshot) -> float:
    if snapshot.mark_price is None or snapshot.index_price is None or snapshot.index_price <= 0:
        return 0.0
    return abs((snapshot.mark_price - snapshot.index_price) / snapshot.index_price * 100)


def _known_volume_24h_usdt(long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> float | None:
    if long_leg.volume_24h_usdt is None or short_leg.volume_24h_usdt is None:
        return None
    values = [
        value
        for value in (long_leg.volume_24h_usdt, short_leg.volume_24h_usdt)
    ]
    return min(values)


def _depth_usdt(long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> float | None:
    values: list[float] = []
    if long_leg.ask_size is not None:
        values.append(long_leg.ask * long_leg.ask_size)
    if short_leg.bid_size is not None:
        values.append(short_leg.bid * short_leg.bid_size)
    if not values:
        return None
    return min(values)


def _minutes_to_settlement(
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
    now: datetime,
) -> float | None:
    times = [
        value
        for value in (long_leg.funding_next_time, short_leg.funding_next_time)
        if value is not None
    ]
    if not times:
        return None
    target = min(times)
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return (target - now).total_seconds() / 60


def _basis_risk_penalty_pct(
    basis_width_pct: float,
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
    settings: FundingArbitrageSettings,
) -> float:
    mark_component = max(_mark_index_diff_pct(long_leg), _mark_index_diff_pct(short_leg)) * 0.02
    width_component = basis_width_pct * 0.03
    return (mark_component + width_component) * settings.basis_risk_weight


def _adl_risk_score(
    funding_edge_pct: float | None,
    basis_width_pct: float,
    volume_24h_usdt: float | None,
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
    settings: FundingArbitrageSettings,
) -> float:
    mark_component = (
        max(_mark_index_diff_pct(long_leg), _mark_index_diff_pct(short_leg))
        / max(settings.max_mark_index_deviation_pct, 0.01)
        * 40
    )
    basis_component = (
        basis_width_pct / max(settings.max_basis_width_pct, 0.01) * 20
    )
    funding_component = min(abs(funding_edge_pct or 0.0) * 40, 20)
    liquidity_component = 0.0
    if volume_24h_usdt is None:
        liquidity_component = 15
    elif volume_24h_usdt < settings.min_volume_24h_usdt:
        liquidity_component = 20
    leverage_component = min(max(settings.leverage - 1, 0) * 3, 15)
    return mark_component + basis_component + funding_component + liquidity_component + leverage_component


def _adl_level(score: float, settings: FundingArbitrageSettings) -> AdlRiskLevel:
    if score >= settings.adl_block_score:
        return "BLOCKED"
    if score >= settings.adl_block_score * 0.7:
        return "HIGH"
    if score >= settings.adl_block_score * 0.4:
        return "MEDIUM"
    return "LOW"


def _uses_hyperliquid(long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> bool:
    return HYPERLIQUID_EXCHANGE in {long_leg.exchange.lower(), short_leg.exchange.lower()}


def _build_candidate(
    kind: str,
    symbol: str,
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
    settings: FundingArbitrageSettings,
    now: datetime,
) -> FundingArbitrageCandidate:
    long_next, long_source = _side_next_funding_pct(long_leg)
    short_next, short_source = _side_next_funding_pct(short_leg)
    source = _funding_source(long_source, short_source)
    next_edge = (
        round(short_next - long_next, 10)
        if short_next is not None and long_next is not None
        else None
    )
    current_long = _side_current_funding_pct(long_leg)
    current_short = _side_current_funding_pct(short_leg)
    current_edge = (
        current_short - current_long
        if current_short is not None and current_long is not None
        else None
    )
    entry_basis, exit_basis = _basis_pct(long_leg, short_leg)
    basis_width = abs(exit_basis - entry_basis)
    basis_penalty = _basis_risk_penalty_pct(basis_width, long_leg, short_leg, settings)
    volume = _known_volume_24h_usdt(long_leg, short_leg)
    depth = _depth_usdt(long_leg, short_leg)
    minutes_to_settlement = _minutes_to_settlement(long_leg, short_leg, now)
    confidence_penalty = settings.confidence_penalty_pct if source == "fallback_current" else 0.0
    adl_score = _adl_risk_score(next_edge, basis_width, volume, long_leg, short_leg, settings)
    adl_level = _adl_level(adl_score, settings)
    adl_penalty = adl_score / 100 * 0.02
    expected_pnl = (
        (next_edge or 0.0)
        - DEFAULT_OPEN_COST_PCT
        - DEFAULT_CLOSE_COST_PCT
        - settings.slippage_buffer_pct
        - basis_penalty
        - adl_penalty
        - confidence_penalty
    )

    risk_labels: list[str] = []
    reasons: list[str] = []
    if source == "missing":
        risk_labels.append("MISSING_FUNDING")
        reasons.append("missing funding on a futures leg")
    if volume is None or volume < settings.min_volume_24h_usdt:
        risk_labels.append("LOW_VOLUME")
        reasons.append("24h volume below funding strategy floor")
    if depth is None:
        risk_labels.append("UNKNOWN_DEPTH")
    elif depth < settings.notional_per_symbol_usdt:
        risk_labels.append("THIN_DEPTH")
        reasons.append("top-of-book depth below funding strategy notional")
    if basis_width >= settings.max_basis_width_pct:
        risk_labels.append("WIDE_BASIS")
        reasons.append("basis width exceeds funding strategy limit")
    if max(_mark_index_diff_pct(long_leg), _mark_index_diff_pct(short_leg)) >= settings.max_mark_index_deviation_pct:
        risk_labels.append("MARK_INDEX_DEVIATION")
        reasons.append("mark/index deviation exceeds funding strategy limit")
    if adl_level == "BLOCKED":
        risk_labels.append("ADL_RISK_BLOCKED")
        reasons.append("ADL risk proxy crossed block threshold")
    if next_edge is not None and next_edge < settings.min_funding_edge_pct:
        reasons.append("raw funding edge below entry floor")
    if minutes_to_settlement is None:
        risk_labels.append("MISSING_SETTLEMENT_TIME")
        reasons.append("missing next settlement time")
    elif (
        minutes_to_settlement < settings.min_minutes_to_settlement
        or minutes_to_settlement > settings.max_minutes_to_settlement
    ):
        risk_labels.append("OUTSIDE_SETTLEMENT_WINDOW")
        reasons.append("outside settlement window for funding entry")

    decision: FundingArbitrageDecision
    if reasons:
        decision = "BLOCKED"
    elif expected_pnl >= settings.min_entry_edge_pct:
        decision = "ENTER"
        reasons.append("expected next-cycle PnL is above entry threshold")
    elif expected_pnl >= settings.min_hold_edge_pct:
        decision = "HOLD"
        reasons.append("expected next-cycle PnL is non-negative but below entry threshold")
    elif expected_pnl < settings.min_exit_edge_pct:
        decision = "EXIT_NOW"
        reasons.append("expected next-cycle PnL is below exit threshold")
    else:
        decision = "EXIT_SOON"
        reasons.append("expected next-cycle PnL is deteriorating")

    return FundingArbitrageCandidate(
        id=_candidate_id(kind, symbol, long_leg, short_leg),
        symbol=symbol,
        type=kind,  # type: ignore[arg-type]
        long_exchange=long_leg.exchange,
        long_market_type=long_leg.market_type.value,
        short_exchange=short_leg.exchange,
        short_market_type=short_leg.market_type.value,
        funding_source=source,
        current_funding_edge_pct=current_edge,
        next_funding_edge_pct=next_edge,
        minutes_to_settlement=minutes_to_settlement,
        entry_basis_pct=entry_basis,
        exit_basis_pct=exit_basis,
        basis_width_pct=basis_width,
        basis_risk_penalty_pct=basis_penalty,
        estimated_open_cost_pct=DEFAULT_OPEN_COST_PCT,
        estimated_close_cost_pct=DEFAULT_CLOSE_COST_PCT,
        slippage_buffer_pct=settings.slippage_buffer_pct,
        confidence_penalty_pct=confidence_penalty,
        adl_risk_penalty_pct=adl_penalty,
        expected_cycle_pnl_pct=expected_pnl,
        adl_risk_score=adl_score,
        adl_risk_level=adl_level,
        decision=decision,
        decision_reasons=reasons,
        risk_labels=risk_labels,
        volume_24h_usdt=volume,
        depth_usdt=depth,
        uses_hyperliquid=_uses_hyperliquid(long_leg, short_leg),
    )


def _future_funding_for_orientation(snapshot: MarketSnapshot) -> float | None:
    value, _ = _side_next_funding_pct(snapshot)
    return value


def _build_candidates_for_symbol(
    symbol: str,
    markets: list[MarketSnapshot],
    settings: FundingArbitrageSettings,
    now: datetime,
) -> list[FundingArbitrageCandidate]:
    candidates: list[FundingArbitrageCandidate] = []
    spots = [item for item in markets if item.market_type == MarketType.SPOT]
    futures = [item for item in markets if item.market_type == MarketType.FUTURE]

    for spot in spots:
        for future in futures:
            candidates.append(_build_candidate("SF", symbol, spot, future, settings, now))

    for first, second in combinations(futures, 2):
        first_funding = _future_funding_for_orientation(first)
        second_funding = _future_funding_for_orientation(second)
        if first_funding is None and second_funding is None:
            long_leg, short_leg = first, second
        elif second_funding is None:
            long_leg, short_leg = second, first
        elif first_funding is None:
            long_leg, short_leg = first, second
        elif first_funding >= second_funding:
            long_leg, short_leg = second, first
        else:
            long_leg, short_leg = first, second
        candidates.append(_build_candidate("FF", symbol, long_leg, short_leg, settings, now))

    return candidates


def build_funding_arbitrage_preview(
    markets: list[MarketSnapshot],
    settings: FundingArbitrageSettings | None = None,
    now: datetime | None = None,
) -> FundingArbitragePreview:
    resolved_settings = settings or FundingArbitrageSettings()
    current = now or datetime.now(UTC)
    by_symbol: dict[str, list[MarketSnapshot]] = defaultdict(list)
    for market in markets:
        by_symbol[market.symbol].append(market)

    candidates: list[FundingArbitrageCandidate] = []
    total_pairs = 0
    for symbol, symbol_markets in by_symbol.items():
        symbol_candidates = _build_candidates_for_symbol(symbol, symbol_markets, resolved_settings, current)
        total_pairs += len(symbol_candidates)
        candidates.extend(symbol_candidates)

    candidates = sorted(
        candidates,
        key=lambda item: (
            item.expected_cycle_pnl_pct,
            -item.adl_risk_score,
            item.volume_24h_usdt or 0,
            1 if resolved_settings.prefer_hyperliquid and item.uses_hyperliquid else 0,
            -item.basis_width_pct,
        ),
        reverse=True,
    )[: resolved_settings.max_candidates]

    return FundingArbitragePreview(
        settings=resolved_settings,
        total_pairs_evaluated=total_pairs,
        displayed_candidates=len(candidates),
        blocked_missing_funding=sum("MISSING_FUNDING" in item.risk_labels for item in candidates),
        blocked_liquidity=sum(
            bool({"LOW_VOLUME", "THIN_DEPTH"} & set(item.risk_labels))
            for item in candidates
        ),
        blocked_adl_risk=sum("ADL_RISK_BLOCKED" in item.risk_labels for item in candidates),
        blocked_expected_pnl=sum(
            item.decision in {"EXIT_NOW", "EXIT_SOON"}
            for item in candidates
        ),
        enter_count=sum(item.decision == "ENTER" for item in candidates),
        hold_count=sum(item.decision == "HOLD" for item in candidates),
        exit_count=sum(item.decision in {"EXIT_NOW", "EXIT_SOON"} for item in candidates),
        blocked_count=sum(item.decision == "BLOCKED" for item in candidates),
        candidates=candidates,
    )
