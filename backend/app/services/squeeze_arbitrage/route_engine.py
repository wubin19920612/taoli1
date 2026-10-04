from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from functools import reduce
from itertools import pairwise
from math import gcd
from typing import Literal

from .models import utc_iso

ROUTE_RULE_VERSION = "squeeze-route-s2-v1"
D = Decimal


@dataclass(frozen=True)
class RouteLeg:
    exchange: str
    market_type: Literal["future", "spot"]
    raw_symbol: str
    dex: str
    asset_id: str
    base_asset: str
    quote_asset: str
    settlement_asset: str
    contract_base_qty: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    taker_fee_rate: Decimal
    fee_source: str
    verification_source: str
    chain_id: str | None = None
    contract_address: str | None = None

    @property
    def key(self) -> str:
        return f"{self.exchange}|{self.market_type}|{self.raw_symbol}|{self.dex}"


@dataclass(frozen=True)
class Route:
    route_id: str
    expensive: RouteLeg
    cheap: RouteLeg
    target_notionals: tuple[Decimal, ...] = (D("100"), D("500"), D("1000"))
    holding_hours: int = 2
    buffer_rate: Decimal = field(default_factory=lambda: D("0.001"))
    min_net_rate: Decimal = field(default_factory=lambda: D("0.01"))
    anomaly_rate: Decimal = field(default_factory=lambda: D("0.01"))


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class LegSnapshot:
    market_key: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    source_at: datetime | None
    received_at: datetime
    sequence: int | None
    last_trade_at: datetime | None
    last_trade_notional: Decimal | None
    funding_rate: Decimal | None
    funding_interval_hours: int | None
    next_funding_at: datetime | None
    funding_source_at: datetime | None
    metadata_verified_at: datetime | None
    turnover_24h: Decimal | None = None
    funding_kind: str = "unknown"


@dataclass(frozen=True)
class VwapFill:
    base_quantity: Decimal
    quote_notional: Decimal
    unit_price: Decimal
    best_unit_price: Decimal
    impact_rate: Decimal


@dataclass(frozen=True)
class CapacityEvaluation:
    target_notional: Decimal
    base_quantity: Decimal
    expensive_open_sell: VwapFill | None
    expensive_close_buy: VwapFill | None
    cheap_open_buy: VwapFill | None
    cheap_close_sell: VwapFill | None
    open_difference: Decimal | None
    close_difference_now: Decimal | None
    target_residual: Decimal | None
    entry_fee: Decimal | None
    estimated_exit_fee: Decimal | None
    estimated_funding: Decimal | None
    estimated_borrow: Decimal | None
    latency_buffer: Decimal | None
    estimated_net: Decimal | None
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class RouteEvaluation:
    route_id: str
    rule_version: str
    calculated_at: datetime
    expensive_key: str
    cheap_key: str
    asset_id: str
    quote_asset: str
    quality: Literal["blocked", "research_only"]
    blockers: tuple[str, ...]
    expensive_source_at: datetime | None
    cheap_source_at: datetime | None
    expensive_received_at: datetime
    cheap_received_at: datetime
    expensive_sequence: int | None
    cheap_sequence: int | None
    expensive_last_trade_at: datetime | None
    cheap_last_trade_at: datetime | None
    expensive_funding_rate: Decimal | None
    cheap_funding_rate: Decimal | None
    expensive_funding_kind: str
    cheap_funding_kind: str
    expensive_funding_source_at: datetime | None
    cheap_funding_source_at: datetime | None
    expensive_funding_interval_hours: int | None
    cheap_funding_interval_hours: int | None
    expensive_next_funding_at: datetime | None
    cheap_next_funding_at: datetime | None
    fee_assumption: str
    expensive_recent_trade_notional: Decimal | None
    cheap_recent_trade_notional: Decimal | None
    expensive_turnover_24h: Decimal | None
    cheap_turnover_24h: Decimal | None
    expensive_contract_base_qty: Decimal
    cheap_contract_base_qty: Decimal
    expensive_quantity_step: Decimal
    cheap_quantity_step: Decimal
    expensive_taker_fee_rate: Decimal
    cheap_taker_fee_rate: Decimal
    capacities: tuple[CapacityEvaluation, ...]

    def to_dict(self) -> dict:
        def encode(value):
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, datetime):
                return utc_iso(value)
            if isinstance(value, tuple):
                return [encode(item) for item in value]
            if isinstance(value, dict):
                return {key: encode(item) for key, item in value.items()}
            if isinstance(value, list):
                return [encode(item) for item in value]
            return value

        return encode(asdict(self))


def validate_route(route: Route) -> tuple[str, ...]:
    high, low = route.expensive, route.cheap
    reasons = []
    if high.key == low.key:
        reasons.append("same_market")
    if (high.asset_id != low.asset_id or not high.asset_id):
        reasons.append("asset_identity_mismatch")
    if high.base_asset != low.base_asset:
        reasons.append("base_asset_mismatch")
    if high.quote_asset != low.quote_asset or high.quote_asset != "USDT":
        reasons.append("quote_asset_mismatch")
    if high.settlement_asset != low.settlement_asset or high.settlement_asset != "USDT":
        reasons.append("settlement_asset_mismatch")
    if high.market_type != "future" or low.market_type != "future":
        reasons.append("non_linear_route_requires_borrow_or_inventory")
    if any(leg.market_type == "spot" and not leg.chain_id
           for leg in (high, low)):
        reasons.append("spot_asset_chain_unknown")
    if any(leg.contract_base_qty <= 0 or leg.quantity_step <= 0 for leg in (high, low)):
        reasons.append("invalid_contract_units")
    if any(not leg.verification_source for leg in (high, low)):
        reasons.append("identity_evidence_missing")
    if any(leg.taker_fee_rate <= 0 or not leg.fee_source for leg in (high, low)):
        reasons.append("fee_assumption_missing")
    return tuple(reasons)


def common_base_step(high: RouteLeg, low: RouteLeg) -> Decimal:
    steps = (high.quantity_step * high.contract_base_qty,
             low.quantity_step * low.contract_base_qty)
    scale = 10 ** max(max(-step.as_tuple().exponent, 0) for step in steps)
    integers = [int(step * scale) for step in steps]
    return D(reduce(lambda a, b: a * b // gcd(a, b), integers)) / D(scale)


def rounded_base_quantity(route: Route, target_notional: Decimal, reference_price: Decimal) -> Decimal:
    if target_notional <= 0 or reference_price <= 0:
        return D(0)
    step = common_base_step(route.expensive, route.cheap)
    return (target_notional / reference_price / step).to_integral_value(rounding=ROUND_DOWN) * step


def book_blockers(snapshot: LegSnapshot, leg: RouteLeg, now: datetime) -> tuple[str, ...]:
    reasons = []
    if snapshot.market_key != leg.key:
        reasons.append("market_identity_mismatch")
    if snapshot.source_at is None:
        reasons.append("source_time_missing")
    elif snapshot.source_at > now + timedelta(seconds=1) or now - snapshot.source_at > timedelta(seconds=1):
        reasons.append("source_stale")
    if snapshot.received_at > now + timedelta(seconds=1) or now - snapshot.received_at > timedelta(seconds=1):
        reasons.append("received_stale")
    if snapshot.sequence is None or snapshot.sequence <= 0:
        reasons.append("sequence_missing")
    if (snapshot.last_trade_at is None or snapshot.last_trade_at > now + timedelta(seconds=1)
            or now - snapshot.last_trade_at > timedelta(seconds=60)):
        reasons.append("recent_trade_missing")
    if snapshot.last_trade_notional is None or snapshot.last_trade_notional <= 0:
        reasons.append("recent_trade_zero")
    if snapshot.turnover_24h is None or snapshot.turnover_24h <= 0:
        reasons.append("turnover_unknown")
    if not snapshot.bids or not snapshot.asks:
        reasons.append("book_empty")
    else:
        for levels, reverse in ((snapshot.bids, True), (snapshot.asks, False)):
            if any(level.price <= 0 or level.quantity <= 0 for level in levels):
                reasons.append("book_invalid_level")
            if any((a.price < b.price if reverse else a.price > b.price)
                   for a, b in pairwise(levels)):
                reasons.append("book_unsorted")
        if snapshot.bids[0].price >= snapshot.asks[0].price:
            reasons.append("book_crossed")
    if snapshot.metadata_verified_at is None or now - snapshot.metadata_verified_at > timedelta(minutes=10):
        reasons.append("metadata_stale")
    if (snapshot.funding_rate is None or snapshot.funding_kind == "unknown"
            or snapshot.funding_interval_hours is None
            or snapshot.funding_interval_hours <= 0 or snapshot.next_funding_at is None
            or snapshot.funding_source_at is None
            or now - snapshot.funding_source_at > timedelta(minutes=2)):
        reasons.append("funding_unknown")
    return tuple(dict.fromkeys(reasons))


def vwap(book: LegSnapshot, leg: RouteLeg, side: Literal["buy", "sell"], base_quantity: Decimal) -> VwapFill | None:
    if base_quantity <= 0:
        return None
    levels = book.asks if side == "buy" else book.bids
    if not levels:
        return None
    remaining = base_quantity
    quote = D(0)
    for level in levels:
        if level.price <= 0 or level.quantity <= 0:
            return None
        available = level.quantity * leg.contract_base_qty
        filled = min(remaining, available)
        quote += filled * level.price / leg.contract_base_qty
        remaining -= filled
        if remaining <= 0:
            break
    if remaining > 0:
        return None
    price = quote / base_quantity
    best = levels[0].price / leg.contract_base_qty
    impact = (price / best - 1) if side == "buy" else (1 - price / best)
    return VwapFill(base_quantity, quote, price, best, impact)


def estimated_funding_cashflow(
    snapshot: LegSnapshot, notional: Decimal, side: Literal["short", "long"],
    now: datetime, holding_hours: int,
) -> Decimal | None:
    if (snapshot.funding_rate is None or snapshot.funding_interval_hours is None
            or snapshot.next_funding_at is None or snapshot.next_funding_at <= now):
        return None
    end = now + timedelta(hours=holding_hours)
    settlement = snapshot.next_funding_at
    count = 0
    while settlement <= end:
        count += 1
        settlement += timedelta(hours=snapshot.funding_interval_hours)
    sign = D(1) if side == "short" else D(-1)
    return sign * snapshot.funding_rate * notional * count


def evaluate_route(
    route: Route, expensive: LegSnapshot, cheap: LegSnapshot, now: datetime,
    *, target_residual: Decimal | None,
    quantity_overrides: tuple[Decimal, ...] | None = None,
) -> RouteEvaluation:
    blockers = list(validate_route(route))
    for label, snapshot, leg in (("expensive", expensive, route.expensive),
                                 ("cheap", cheap, route.cheap)):
        blockers.extend(f"{label}_{reason}" for reason in book_blockers(snapshot, leg, now))
    if (expensive.source_at and cheap.source_at
            and abs((expensive.source_at - cheap.source_at).total_seconds()) > 1):
        blockers.append("source_skew")
    if target_residual is None:
        blockers.append("normal_reference_missing")
    capacities = []
    reference_price = cheap.asks[0].price / route.cheap.contract_base_qty if cheap.asks else D(0)
    for index, target in enumerate(route.target_notionals):
        reasons = []
        q = (quantity_overrides[index] if quantity_overrides is not None
             else rounded_base_quantity(route, target, reference_price))
        if q <= 0:
            reasons.append("quantity_rounds_to_zero")
        for label, leg in (("expensive", route.expensive), ("cheap", route.cheap)):
            if q < leg.minimum_quantity * leg.contract_base_qty:
                reasons.append(f"{label}_minimum_quantity")
            if q > 0 and q % (leg.quantity_step * leg.contract_base_qty) != 0:
                reasons.append(f"{label}_quantity_step_mismatch")
        high_sell = vwap(expensive, route.expensive, "sell", q)
        high_buy = vwap(expensive, route.expensive, "buy", q)
        low_buy = vwap(cheap, route.cheap, "buy", q)
        low_sell = vwap(cheap, route.cheap, "sell", q)
        fills = (high_sell, high_buy, low_buy, low_sell)
        for label, fill in zip(("expensive_open", "expensive_close", "cheap_open", "cheap_close"), fills):
            if fill is None:
                reasons.append(f"{label}_depth_insufficient")
            elif fill.impact_rate > D("0.002"):
                reasons.append(f"{label}_impact_excess")
        for label, fill, leg in (
            ("expensive_open", high_sell, route.expensive),
            ("expensive_close", high_buy, route.expensive),
            ("cheap_open", low_buy, route.cheap),
            ("cheap_close", low_sell, route.cheap),
        ):
            if fill and fill.quote_notional < leg.minimum_notional:
                reasons.append(f"{label}_minimum_notional")
        open_diff = high_sell.unit_price - low_buy.unit_price if high_sell and low_buy else None
        close_diff = high_buy.unit_price - low_sell.unit_price if high_buy and low_sell else None
        entry_fee = (high_sell.quote_notional * route.expensive.taker_fee_rate
                     + low_buy.quote_notional * route.cheap.taker_fee_rate) if high_sell and low_buy else None
        exit_fee = (high_buy.quote_notional * route.expensive.taker_fee_rate
                    + low_sell.quote_notional * route.cheap.taker_fee_rate) if high_buy and low_sell else None
        funding_high = estimated_funding_cashflow(expensive, high_sell.quote_notional, "short", now, route.holding_hours) if high_sell else None
        funding_low = estimated_funding_cashflow(cheap, low_buy.quote_notional, "long", now, route.holding_hours) if low_buy else None
        funding = funding_high + funding_low if funding_high is not None and funding_low is not None else None
        if funding is None:
            reasons.append("funding_unknown")
        borrow = D(0) if (
            route.expensive.market_type == "future"
            and route.cheap.market_type == "future"
        ) else None
        if borrow is None:
            reasons.append("borrow_unknown")
        buffer = route.buffer_rate * target
        net = (q * (open_diff - target_residual) - entry_fee - exit_fee
               + funding - borrow - buffer
               if open_diff is not None and target_residual is not None
               and entry_fee is not None and exit_fee is not None
               and funding is not None and borrow is not None else None)
        if net is None or net < route.min_net_rate * target:
            reasons.append("net_space_below_minimum")
        capacities.append(CapacityEvaluation(
            target, q, high_sell, high_buy, low_buy, low_sell, open_diff, close_diff,
            target_residual, entry_fee, exit_fee, funding, borrow, buffer, net,
            tuple(dict.fromkeys(reasons)),
        ))
    if all(any("depth_insufficient" in reason or "impact_excess" in reason
               for reason in cap.blockers) for cap in capacities):
        blockers.append("no_capacity_has_valid_depth")
    return RouteEvaluation(
        route.route_id, ROUTE_RULE_VERSION, now.astimezone(UTC), route.expensive.key,
        route.cheap.key, route.expensive.asset_id, route.expensive.quote_asset,
        "blocked" if blockers else "research_only", tuple(dict.fromkeys(blockers)),
        expensive.source_at, cheap.source_at, expensive.received_at, cheap.received_at,
        expensive.sequence, cheap.sequence, expensive.last_trade_at, cheap.last_trade_at,
        expensive.funding_rate, cheap.funding_rate,
        expensive.funding_kind, cheap.funding_kind,
        expensive.funding_source_at, cheap.funding_source_at,
        expensive.funding_interval_hours,
        cheap.funding_interval_hours, expensive.next_funding_at, cheap.next_funding_at,
        f"{route.expensive.fee_source};{route.cheap.fee_source}",
        expensive.last_trade_notional, cheap.last_trade_notional,
        expensive.turnover_24h, cheap.turnover_24h,
        route.expensive.contract_base_qty, route.cheap.contract_base_qty,
        route.expensive.quantity_step, route.cheap.quantity_step,
        route.expensive.taker_fee_rate, route.cheap.taker_fee_rate,
        tuple(capacities),
    )


def lsk_research_route() -> Route:
    evidence = (
        "manual_lisk_underlying_review_2026-09-26;"
        "binance:fapi/v1/exchangeInfo:LSKUSDT;"
        "bybit:v5/market/instruments-info:linear:LSKUSDT"
    )
    common = {
        "market_type": "future", "raw_symbol": "LSKUSDT", "dex": "",
        "asset_id": "lisk:LSK", "base_asset": "LSK", "quote_asset": "USDT",
        "settlement_asset": "USDT", "contract_base_qty": D(1),
        "minimum_notional": D(5), "taker_fee_rate": D("0.0006"),
        "fee_source": "conservative_public_assumption_not_account_tier",
        "verification_source": evidence,
        "chain_id": None, "contract_address": None,
    }
    return Route(
        "bybit-future-LSKUSDT__binance-future-LSKUSDT",
        RouteLeg(exchange="bybit", quantity_step=D("0.1"), minimum_quantity=D("0.1"), **common),
        RouteLeg(exchange="binance", quantity_step=D("1"), minimum_quantity=D("1"), **common),
    )
