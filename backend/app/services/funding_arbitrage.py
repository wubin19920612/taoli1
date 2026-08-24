from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha1
from itertools import combinations
from math import gcd

from app.models.funding_arbitrage import (
    AdlRiskLevel,
    FundingArbitrageCandidate,
    FundingArbitrageDecision,
    FundingArbitragePreview,
    FundingArbitrageSettings,
    FundingSource,
)
from app.models.market import MarketSnapshot, MarketType
from app.services.funding_research.opportunity_types import exchange_formula_family

HYPERLIQUID_EXCHANGE = "hyperliquid"
GATE_EXCHANGE = "gate"
DEFAULT_OPEN_COST_PCT = 0.02
DEFAULT_CLOSE_COST_PCT = 0.02
POOR_REWARD_RISK_LABEL = "POOR_REWARD_RISK"


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


def _funding_interval_hours(snapshot: MarketSnapshot) -> int | None:
    if snapshot.market_type == MarketType.SPOT:
        return None
    if snapshot.funding_interval_hours is None or snapshot.funding_interval_hours <= 0:
        return None
    return snapshot.funding_interval_hours


def _lcm(left: int, right: int) -> int:
    return abs(left * right) // gcd(left, right)


def _funding_comparison_interval_hours(
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
) -> int | None:
    intervals = [
        value
        for value in (_funding_interval_hours(long_leg), _funding_interval_hours(short_leg))
        if value is not None
    ]
    if not intervals:
        return None
    interval = intervals[0]
    for value in intervals[1:]:
        interval = _lcm(interval, value)
    return interval


def _funding_pct_for_interval(
    value: float | None,
    snapshot: MarketSnapshot,
    comparison_interval_hours: int | None,
) -> float | None:
    if value is None:
        return None
    if snapshot.market_type == MarketType.SPOT:
        return 0.0
    interval = _funding_interval_hours(snapshot)
    if interval is None or comparison_interval_hours is None:
        return None
    return value * comparison_interval_hours / interval


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
    target = _next_settlement_time(long_leg, short_leg)
    if target is None:
        return None
    return (target - now).total_seconds() / 60


def _next_settlement_time(
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
) -> datetime | None:
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
    return target.astimezone(UTC)


def _basis_risk_penalty_pct(
    basis_width_pct: float,
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
    settings: FundingArbitrageSettings,
) -> float:
    mark_component = max(_mark_index_diff_pct(long_leg), _mark_index_diff_pct(short_leg)) * 0.02
    width_component = basis_width_pct * 0.03
    return (mark_component + width_component) * settings.basis_risk_weight


def _adverse_entry_basis_pct(entry_basis_pct: float) -> float:
    return abs(min(entry_basis_pct, 0.0))


def _conflicted_reward_risk_ratio(
    expected_pnl_pct: float,
    adverse_entry_basis_pct: float,
) -> float | None:
    if adverse_entry_basis_pct <= 0:
        return None
    return max(expected_pnl_pct, 0.0) / adverse_entry_basis_pct


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


def _uses_gate(long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> bool:
    return GATE_EXCHANGE in {long_leg.exchange.lower(), short_leg.exchange.lower()}


def _opportunity_classification(
    *,
    long_leg: MarketSnapshot,
    short_leg: MarketSnapshot,
    next_edge: float | None,
    entry_basis: float,
    basis_width: float,
    long_interval: int | None,
    short_interval: int | None,
    minutes_to_settlement: float | None,
    settings: FundingArbitrageSettings,
) -> tuple[str, list[str], list[str]]:
    types: list[str] = []
    reasons: list[str] = []
    funding_edge = next_edge or 0.0

    def add(opportunity_type: str, reason: str) -> None:
        if opportunity_type in types:
            return
        types.append(opportunity_type)
        reasons.append(reason)

    if funding_edge > 0 and entry_basis >= 0:
        add(
            "BASIS_AND_FUNDING_ALIGNED",
            "entry basis and funding carry both favor the selected direction",
        )
    elif funding_edge > 0 and entry_basis < 0:
        add(
            "BASIS_CARRY_CONFLICTED",
            "funding carry is positive but entry basis is against the selected direction",
        )
    elif funding_edge > 0:
        add("PURE_FUNDING_SPREAD", "funding carry is positive")

    if (
        funding_edge >= settings.strong_funding_pct
        and abs(entry_basis) <= settings.small_basis_threshold_pct
        and minutes_to_settlement is not None
        and minutes_to_settlement <= settings.near_settlement_minutes
    ):
        add(
            "STRONG_FUNDING_NEAR_SETTLEMENT",
            "strong funding edge, small basis, and settlement is near",
        )

    if (
        long_interval is not None
        and short_interval is not None
        and abs(long_interval - short_interval) >= settings.interval_mismatch_min_hours
    ):
        add(
            "INTERVAL_MISMATCH",
            "long and short legs settle on different funding intervals",
        )

    if (
        exchange_formula_family(long_leg.exchange) != exchange_formula_family(short_leg.exchange)
        and abs(funding_edge) >= settings.formula_divergence_min_funding_pct
    ):
        add(
            "FORMULA_DIVERGENCE",
            "exchange funding formula families differ and funding edge is meaningful",
        )

    if entry_basis > 0 and basis_width <= settings.max_basis_width_pct:
        add("BASIS_MEAN_REVERSION", "entry basis can be captured if spread mean-reverts")

    if not types:
        add(
            "PURE_FUNDING_SPREAD",
            "no specialized detector matched; keep as generic funding-spread watch item",
        )

    priority = (
        "BASIS_AND_FUNDING_ALIGNED",
        "STRONG_FUNDING_NEAR_SETTLEMENT",
        "INTERVAL_MISMATCH",
        "FORMULA_DIVERGENCE",
        "BASIS_CARRY_CONFLICTED",
        "BASIS_MEAN_REVERSION",
        "PURE_FUNDING_SPREAD",
    )
    primary = next(item for item in priority if item in types)
    return primary, types, reasons


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
    long_interval = _funding_interval_hours(long_leg)
    short_interval = _funding_interval_hours(short_leg)
    comparison_interval = _funding_comparison_interval_hours(long_leg, short_leg)
    long_next_comparable = _funding_pct_for_interval(long_next, long_leg, comparison_interval)
    short_next_comparable = _funding_pct_for_interval(short_next, short_leg, comparison_interval)
    next_edge = (
        round(short_next_comparable - long_next_comparable, 10)
        if short_next_comparable is not None and long_next_comparable is not None
        else None
    )
    current_long = _side_current_funding_pct(long_leg)
    current_short = _side_current_funding_pct(short_leg)
    current_long_comparable = _funding_pct_for_interval(
        current_long,
        long_leg,
        comparison_interval,
    )
    current_short_comparable = _funding_pct_for_interval(
        current_short,
        short_leg,
        comparison_interval,
    )
    current_edge = (
        current_short_comparable - current_long_comparable
        if current_short_comparable is not None and current_long_comparable is not None
        else None
    )
    entry_basis, exit_basis = _basis_pct(long_leg, short_leg)
    basis_width = abs(exit_basis - entry_basis)
    basis_penalty = _basis_risk_penalty_pct(basis_width, long_leg, short_leg, settings)
    volume = _known_volume_24h_usdt(long_leg, short_leg)
    depth = _depth_usdt(long_leg, short_leg)
    minutes_to_settlement = _minutes_to_settlement(long_leg, short_leg, now)
    next_settlement = _next_settlement_time(long_leg, short_leg)
    primary_type, opportunity_types, opportunity_reasons = _opportunity_classification(
        long_leg=long_leg,
        short_leg=short_leg,
        next_edge=next_edge,
        entry_basis=entry_basis,
        basis_width=basis_width,
        long_interval=long_interval,
        short_interval=short_interval,
        minutes_to_settlement=minutes_to_settlement,
        settings=settings,
    )
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
    adverse_entry_basis = _adverse_entry_basis_pct(entry_basis)
    reward_risk_ratio = _conflicted_reward_risk_ratio(expected_pnl, adverse_entry_basis)

    risk_labels: list[str] = []
    reasons: list[str] = []
    if source == "missing":
        risk_labels.append("MISSING_FUNDING")
        reasons.append("missing funding on a futures leg")
    if (
        long_leg.market_type == MarketType.FUTURE and long_interval is None
    ) or (
        short_leg.market_type == MarketType.FUTURE and short_interval is None
    ):
        risk_labels.append("MISSING_FUNDING_INTERVAL")
        reasons.append("missing funding interval for same-cycle comparison")
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
        reasons.append("same-cycle funding edge below entry floor")
    if (
        adverse_entry_basis >= settings.conflicted_basis_min_check_pct
        and reward_risk_ratio is not None
        and reward_risk_ratio < settings.min_conflicted_reward_risk_ratio
    ):
        risk_labels.append(POOR_REWARD_RISK_LABEL)
        reasons.append(
            "conflicted basis reward/risk is below entry floor "
            f"({reward_risk_ratio:.2f}x < {settings.min_conflicted_reward_risk_ratio:.2f}x)"
        )
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
        long_current_funding_pct=current_long,
        short_current_funding_pct=current_short,
        long_next_funding_pct=long_next,
        short_next_funding_pct=short_next,
        current_funding_edge_pct=current_edge,
        next_funding_edge_pct=next_edge,
        long_funding_interval_hours=long_interval,
        short_funding_interval_hours=short_interval,
        funding_comparison_interval_hours=comparison_interval,
        long_next_settlement_time=long_leg.funding_next_time,
        short_next_settlement_time=short_leg.funding_next_time,
        next_settlement_time=next_settlement,
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
        adverse_entry_basis_pct=adverse_entry_basis,
        conflicted_reward_risk_ratio=reward_risk_ratio,
        adl_risk_score=adl_score,
        adl_risk_level=adl_level,
        decision=decision,
        decision_reasons=reasons,
        risk_labels=risk_labels,
        primary_opportunity_type=primary_type,  # type: ignore[arg-type]
        opportunity_types=opportunity_types,  # type: ignore[arg-type]
        opportunity_reasons=opportunity_reasons,
        volume_24h_usdt=volume,
        depth_usdt=depth,
        uses_gate=_uses_gate(long_leg, short_leg),
        uses_hyperliquid=_uses_hyperliquid(long_leg, short_leg),
    )


def _future_funding_per_hour_for_orientation(snapshot: MarketSnapshot) -> float | None:
    value, _ = _side_next_funding_pct(snapshot)
    interval = _funding_interval_hours(snapshot)
    if value is None or interval is None:
        return None
    return value / interval


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
        first_funding = _future_funding_per_hour_for_orientation(first)
        second_funding = _future_funding_per_hour_for_orientation(second)
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
