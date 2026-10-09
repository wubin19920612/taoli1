from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

from .paper_models import (
    FundingSettlement,
    PaperAccount,
    PaperCashflow,
    PaperFill,
    PaperOrder,
    PaperSettings,
    PaperTrade,
)
from .route_engine import (
    BookLevel,
    LegSnapshot,
    Route,
    RouteLeg,
    common_base_step,
    evaluate_route,
    vwap,
)

D = Decimal


def _at(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def decode_route_inputs(payload: dict[str, Any]) -> tuple[Route, LegSnapshot, LegSnapshot]:
    def leg(raw: dict[str, Any]) -> RouteLeg:
        values = dict(raw)
        for field in (
            "contract_base_qty", "quantity_step", "minimum_quantity", "minimum_notional",
            "taker_fee_rate",
        ):
            values[field] = D(values[field])
        return RouteLeg(**values)

    def snapshot(raw: dict[str, Any]) -> LegSnapshot:
        return LegSnapshot(
            market_key=raw["market_key"],
            bids=tuple(BookLevel(D(row["price"]), D(row["quantity"])) for row in raw["bids"]),
            asks=tuple(BookLevel(D(row["price"]), D(row["quantity"])) for row in raw["asks"]),
            source_at=_at(raw["source_at"]),
            received_at=_at(raw["received_at"]),
            sequence=raw["sequence"],
            last_trade_at=_at(raw["last_trade_at"]),
            last_trade_notional=D(raw["last_trade_notional"])
            if raw["last_trade_notional"] is not None else None,
            funding_rate=D(raw["funding_rate"]) if raw["funding_rate"] is not None else None,
            funding_interval_hours=raw["funding_interval_hours"],
            next_funding_at=_at(raw["next_funding_at"]),
            funding_source_at=_at(raw["funding_source_at"]),
            metadata_verified_at=_at(raw["metadata_verified_at"]),
            turnover_24h=D(raw["turnover_24h"]) if raw["turnover_24h"] is not None else None,
            funding_kind=raw["funding_kind"],
        )

    raw_route = payload["route"]
    route = Route(
        route_id=raw_route["route_id"],
        expensive=leg(raw_route["expensive"]),
        cheap=leg(raw_route["cheap"]),
        target_notionals=tuple(D(value) for value in raw_route["target_notionals"]),
        holding_hours=raw_route["holding_hours"],
        buffer_rate=D(raw_route["buffer_rate"]),
        min_net_rate=D(raw_route["min_net_rate"]),
        anomaly_rate=D(raw_route["anomaly_rate"]),
    )
    return route, snapshot(payload["expensive"]), snapshot(payload["cheap"])


@dataclass(frozen=True)
class IocResult:
    quantity: Decimal
    quote_notional: Decimal
    unit_price: Decimal | None


def ioc_from_book(
    snapshot: LegSnapshot, leg: RouteLeg, side: Literal["buy", "sell"],
    target_quantity: Decimal, limit_price: Decimal,
) -> IocResult:
    if target_quantity <= 0 or limit_price <= 0:
        return IocResult(D(0), D(0), None)
    levels = snapshot.asks if side == "buy" else snapshot.bids
    step = leg.quantity_step * leg.contract_base_qty
    remaining = target_quantity
    filled = D(0)
    quote = D(0)
    for level in levels:
        unit_price = level.price / leg.contract_base_qty
        if level.quantity <= 0 or unit_price <= 0:
            break
        if (side == "buy" and unit_price > limit_price) or (
            side == "sell" and unit_price < limit_price
        ):
            break
        available = level.quantity * leg.contract_base_qty
        quantity = (min(remaining, available) / step).to_integral_value(rounding=ROUND_DOWN) * step
        filled += quantity
        quote += quantity * unit_price
        remaining -= quantity
        if remaining < step:
            break
    return IocResult(filled, quote, quote / filled if filled else None)


def _order(
    trade: PaperTrade, role: Literal["entry", "recovery", "exit"], leg: RouteLeg,
    side: Literal["buy", "sell"], quantity: Decimal, reference_price: Decimal,
    at: datetime, settings: PaperSettings, submitted_sequence: int | None,
) -> PaperOrder:
    limit = reference_price * (
        D(1) + settings.limit_buffer_rate if side == "buy"
        else D(1) - settings.limit_buffer_rate
    )
    return PaperOrder(
        id=f"{trade.id}:{role}:{len(trade.orders)}", trade_id=trade.id, role=role,
        leg_key=leg.key, side=side, target_quantity=quantity, limit_price=limit,
        submitted_at=at, eligible_at=at + timedelta(milliseconds=settings.delay_ms),
        submitted_sequence=submitted_sequence,
    )


def create_paper_trade(event: dict[str, Any], settings: PaperSettings) -> PaperTrade:
    evaluation = event["evaluation"]
    capacity = next(
        (item for item in evaluation["capacities"]
         if D(item["target_notional"]) == settings.entry_notional), None
    )
    capacity_matches = capacity is not None
    capacity = capacity or evaluation["capacities"][0]
    at = datetime.fromisoformat(event["occurred_at"]).astimezone(UTC)
    quantity = D(capacity["base_quantity"])
    open_difference = D(capacity["open_difference"] or 0)
    residual = D(capacity["target_residual"] or 0)
    blocked = (
        not capacity_matches or evaluation["quality"] != "research_only" or evaluation["blockers"]
        or capacity["blockers"] or quantity <= 0
        or capacity["target_residual"] is None
    )
    trade = PaperTrade(
        id=event["id"], event_id=event["id"], route_id=event["route_id"],
        asset_id=evaluation["asset_id"], quote_asset=evaluation["quote_asset"],
        expensive_key=evaluation["expensive_key"], cheap_key=evaluation["cheap_key"],
        status="blocked" if blocked else "pending_entry", signal_at=at,
        eligible_at=at + timedelta(milliseconds=settings.delay_ms),
        target_quantity=quantity, signal_open_difference=open_difference,
        target_residual=residual,
        target_close_difference=open_difference - (open_difference - residual) * settings.capture_ratio,
        signal_expensive_sequence=evaluation["expensive_sequence"],
        signal_cheap_sequence=evaluation["cheap_sequence"],
        signal_capacities=evaluation["capacities"],
        last_error=("frozen_entry_notional_missing" if not capacity_matches
                    else "signal_quality_or_cost_blocked") if blocked else None,
    )
    if not blocked:
        route, _, _ = decode_route_inputs(event["inputs"])
        if route.route_id != trade.route_id or (
            route.expensive.key != trade.expensive_key or route.cheap.key != trade.cheap_key
        ):
            trade.status = "blocked"
            trade.last_error = "signal_market_identity_changed"
        else:
            trade.orders.append(_order(
                trade, "entry", route.expensive, "sell", quantity,
                D(capacity["expensive_open_sell"]["unit_price"]), at, settings,
                trade.signal_expensive_sequence,
            ))
            trade.orders.append(_order(
                trade, "entry", route.cheap, "buy", quantity,
                D(capacity["cheap_open_buy"]["unit_price"]), at, settings,
                trade.signal_cheap_sequence,
            ))
    return trade


def _side_snapshot(
    order: PaperOrder, route: Route, expensive: LegSnapshot, cheap: LegSnapshot,
) -> tuple[RouteLeg, LegSnapshot]:
    if order.leg_key == route.expensive.key:
        return route.expensive, expensive
    if order.leg_key == route.cheap.key:
        return route.cheap, cheap
    raise ValueError("paper order market identity mismatch")


def _fill_order(
    trade: PaperTrade, order: PaperOrder, route: Route,
    expensive: LegSnapshot, cheap: LegSnapshot, accounts: dict[str, PaperAccount],
    at: datetime,
) -> None:
    leg, snapshot = _side_snapshot(order, route, expensive, cheap)
    result = ioc_from_book(snapshot, leg, order.side, order.target_quantity, order.limit_price)
    order.filled_quantity = result.quantity
    order.completed_at = at
    order.status = (
        "filled" if result.quantity == order.target_quantity else
        "partial" if result.quantity > 0 else "unfilled"
    )
    if result.quantity == 0 or result.unit_price is None:
        return
    fee = result.quote_notional * leg.taker_fee_rate
    fill = PaperFill(
        id=f"{order.id}:fill", order_id=order.id, trade_id=trade.id,
        leg_key=leg.key, role=order.role, side=order.side,
        quantity=result.quantity, quote_notional=result.quote_notional,
        unit_price=result.unit_price, fee=fee, filled_at=at,
        source_at=snapshot.source_at, received_at=snapshot.received_at,
        sequence=snapshot.sequence,
        book_levels=[(str(level.price), str(level.quantity)) for level in (
            snapshot.asks if order.side == "buy" else snapshot.bids
        )],
    )
    trade.fills.append(fill)
    account = accounts[leg.exchange]
    account.cash_balance -= fee
    account.fees_paid += fee
    account.updated_at = at
    trade.cashflows.append(PaperCashflow(
        id=f"{fill.id}:fee", trade_id=trade.id, leg_key=leg.key,
        exchange=leg.exchange, kind="fee", amount=-fee, occurred_at=at,
        source="conservative_taker_fee_assumption",
    ))
    if order.role == "entry":
        account.reserved_margin += result.quote_notional
        trade.entry_fees += fee
        if leg.key == route.expensive.key:
            trade.expensive_entry_price = result.unit_price
            trade.expensive_open_quantity += result.quantity
            trade.next_expensive_funding_at = expensive.next_funding_at
            trade.expensive_funding_interval_hours = expensive.funding_interval_hours
        else:
            trade.cheap_entry_price = result.unit_price
            trade.cheap_open_quantity += result.quantity
            trade.next_cheap_funding_at = cheap.next_funding_at
            trade.cheap_funding_interval_hours = cheap.funding_interval_hours
        return
    trade.exit_fees += fee
    if leg.key == route.expensive.key:
        entry_price = trade.expensive_entry_price
        assert entry_price is not None
        pnl = (entry_price - result.unit_price) * result.quantity
        trade.expensive_open_quantity -= result.quantity
    else:
        entry_price = trade.cheap_entry_price
        assert entry_price is not None
        pnl = (result.unit_price - entry_price) * result.quantity
        trade.cheap_open_quantity -= result.quantity
    account.reserved_margin -= entry_price * result.quantity
    account.cash_balance += pnl
    account.price_pnl += pnl
    trade.price_pnl += pnl
    trade.cashflows.append(PaperCashflow(
        id=f"{fill.id}:price_pnl", trade_id=trade.id, leg_key=leg.key,
        exchange=leg.exchange, kind="price_pnl", amount=pnl, occurred_at=at,
        source="paper_ioc_visible_depth",
    ))


def _quality_ok(
    trade: PaperTrade, route: Route, expensive: LegSnapshot, cheap: LegSnapshot,
    at: datetime,
) -> bool:
    if route.route_id != trade.route_id or (
        expensive.market_key != trade.expensive_key or cheap.market_key != trade.cheap_key
    ):
        trade.last_error = "route_market_identity_changed"
        return False
    evaluation = evaluate_route(route, expensive, cheap, at, target_residual=trade.target_residual)
    blockers = [reason for reason in evaluation.blockers if reason != "no_capacity_has_valid_depth"]
    if blockers:
        trade.last_error = ",".join(blockers)[:500]
        return False
    trade.last_error = None
    return True


def _fresh_book(
    trade: PaperTrade, expensive: LegSnapshot, cheap: LegSnapshot,
) -> bool:
    fresh = (
        expensive.sequence is not None and cheap.sequence is not None
        and (trade.signal_expensive_sequence is None
             or expensive.sequence > trade.signal_expensive_sequence)
        and (trade.signal_cheap_sequence is None
             or cheap.sequence > trade.signal_cheap_sequence)
    )
    if not fresh:
        trade.last_error = "execution_snapshot_not_independent"
    return fresh


def _pending_orders(trade: PaperTrade, role: str) -> list[PaperOrder]:
    return [order for order in trade.orders if order.role == role and order.status == "pending"]


def _orders_have_new_books(
    trade: PaperTrade, orders: list[PaperOrder], route: Route,
    expensive: LegSnapshot, cheap: LegSnapshot,
) -> bool:
    for order in orders:
        _, snapshot = _side_snapshot(order, route, expensive, cheap)
        if (order.submitted_sequence is None or snapshot.sequence is None
                or snapshot.sequence <= order.submitted_sequence):
            trade.last_error = "paper_order_awaiting_independent_book"
            return False
    return True


def _close_or_recover(trade: PaperTrade, at: datetime, settings: PaperSettings) -> None:
    if trade.expensive_open_quantity == 0 and trade.cheap_open_quantity == 0:
        trade.status = "closed"
        trade.closed_at = at
        return
    trade.status = "recovering"
    trade.recovery_started_at = trade.recovery_started_at or at
    if trade.recovery_attempts >= settings.max_recovery_attempts:
        trade.status = "unresolved"
        trade.last_error = "paper_recovery_attempt_limit"


def advance_paper_trade(
    trade: PaperTrade, accounts: dict[str, PaperAccount], route: Route,
    expensive: LegSnapshot, cheap: LegSnapshot, at: datetime,
    settings: PaperSettings,
) -> PaperTrade:
    if trade.status in {"closed", "blocked", "unfilled", "unresolved"}:
        return trade
    at = at.astimezone(UTC)
    if trade.last_observed_at is not None and at <= trade.last_observed_at:
        return trade
    trade.last_observed_at = at
    if trade.opened_at and (trade.expensive_open_quantity or trade.cheap_open_quantity):
        for field, exchange in (
            ("minimum_expensive_free_balance", route.expensive.exchange),
            ("minimum_cheap_free_balance", route.cheap.exchange),
        ):
            value = accounts[exchange].free_balance
            previous = getattr(trade, field)
            setattr(trade, field, value if previous is None else min(previous, value))
    if not _fresh_book(trade, expensive, cheap) or not _quality_ok(
        trade, route, expensive, cheap, at
    ):
        if trade.status == "pending_entry" and at - trade.signal_at > timedelta(seconds=60):
            trade.status = "unfilled"
            trade.last_error = "entry_book_unavailable_after_delay"
        elif trade.opened_at and at - trade.opened_at >= timedelta(seconds=settings.max_holding_seconds):
            trade.status = "unresolved"
            trade.last_error = "exit_book_unavailable_at_max_holding"
        return trade
    if trade.status == "pending_entry":
        if at < trade.eligible_at:
            return trade
        if at - trade.signal_at > timedelta(seconds=60):
            trade.status = "unfilled"
            trade.last_error = "entry_snapshot_too_late"
            return trade
        for order in _pending_orders(trade, "entry"):
            leg, _ = _side_snapshot(order, route, expensive, cheap)
            required = order.target_quantity * order.limit_price
            if accounts[leg.exchange].free_balance < required * (D(1) + leg.taker_fee_rate):
                trade.status = "blocked"
                trade.last_error = f"{leg.exchange}_paper_balance_insufficient"
                return trade
        pending = _pending_orders(trade, "entry")
        if not _orders_have_new_books(trade, pending, route, expensive, cheap):
            return trade
        for order in pending:
            _fill_order(trade, order, route, expensive, cheap, accounts, at)
        trade.minimum_expensive_free_balance = accounts[route.expensive.exchange].free_balance
        trade.minimum_cheap_free_balance = accounts[route.cheap.exchange].free_balance
        if (trade.expensive_open_quantity == trade.target_quantity
                and trade.cheap_open_quantity == trade.target_quantity):
            trade.status = "open"
            trade.opened_at = at
        elif trade.expensive_open_quantity or trade.cheap_open_quantity:
            trade.status = "recovering"
            trade.opened_at = at
            trade.recovery_started_at = at
            trade.exit_reason = "entry_partial_or_leg_failure"
        else:
            trade.status = "unfilled"
            trade.last_error = "both_entry_ioc_unfilled"
        return trade
    if trade.status == "recovering":
        if trade.recovery_started_at and (
            at - trade.recovery_started_at > timedelta(seconds=settings.recovery_seconds)
        ):
            trade.status = "unresolved"
            trade.last_error = "paper_naked_leg_recovery_timeout"
            return trade
        pending = _pending_orders(trade, "recovery")
        if pending:
            if any(at < order.eligible_at for order in pending):
                return trade
            if not _orders_have_new_books(trade, pending, route, expensive, cheap):
                return trade
            for order in pending:
                _fill_order(trade, order, route, expensive, cheap, accounts, at)
            trade.recovery_attempts += 1
            _close_or_recover(trade, at, settings)
            return trade
        for leg, snapshot, quantity, side in (
            (route.expensive, expensive, trade.expensive_open_quantity, "buy"),
            (route.cheap, cheap, trade.cheap_open_quantity, "sell"),
        ):
            if quantity > 0:
                best = snapshot.asks[0] if side == "buy" else snapshot.bids[0]
                trade.orders.append(_order(
                    trade, "recovery", leg, side, quantity,
                    best.price / leg.contract_base_qty, at, settings, snapshot.sequence,
                ))
        return trade
    if trade.status == "pending_exit":
        if trade.opened_at and (
            at - trade.opened_at > timedelta(seconds=settings.max_holding_seconds)
        ):
            trade.status = "unresolved"
            trade.last_error = "paper_exit_pending_at_max_holding"
            return trade
        pending = _pending_orders(trade, "exit")
        if any(at < order.eligible_at for order in pending):
            return trade
        if not _orders_have_new_books(trade, pending, route, expensive, cheap):
            return trade
        for order in pending:
            _fill_order(trade, order, route, expensive, cheap, accounts, at)
        if trade.expensive_open_quantity == 0 and trade.cheap_open_quantity == 0:
            trade.status = "closed"
            trade.closed_at = at
        elif trade.expensive_open_quantity == trade.cheap_open_quantity and all(
            order.status == "filled" for order in pending
        ):
            trade.status = "open"
        else:
            _close_or_recover(trade, at, settings)
        return trade
    quantity = min(trade.expensive_open_quantity, trade.cheap_open_quantity)
    high_close = vwap(expensive, route.expensive, "buy", quantity)
    low_close = vwap(cheap, route.cheap, "sell", quantity)
    if high_close is None or low_close is None:
        trade.last_error = "exit_depth_insufficient"
        if trade.opened_at and at - trade.opened_at >= timedelta(seconds=settings.max_holding_seconds):
            trade.status = "unresolved"
        return trade
    close_difference = high_close.unit_price - low_close.unit_price
    trade.best_close_difference = (
        close_difference if trade.best_close_difference is None
        else min(close_difference, trade.best_close_difference)
    )
    trade.worst_close_difference = (
        close_difference if trade.worst_close_difference is None
        else max(close_difference, trade.worst_close_difference)
    )
    expensive_free = accounts[route.expensive.exchange].free_balance
    cheap_free = accounts[route.cheap.exchange].free_balance
    trade.minimum_expensive_free_balance = (
        expensive_free if trade.minimum_expensive_free_balance is None
        else min(expensive_free, trade.minimum_expensive_free_balance)
    )
    trade.minimum_cheap_free_balance = (
        cheap_free if trade.minimum_cheap_free_balance is None
        else min(cheap_free, trade.minimum_cheap_free_balance)
    )
    assert trade.expensive_entry_price is not None and trade.cheap_entry_price is not None
    estimated_exit_fee = (
        high_close.quote_notional * route.expensive.taker_fee_rate
        + low_close.quote_notional * route.cheap.taker_fee_rate
    )
    marked_net = (
        quantity * (trade.expensive_entry_price - high_close.unit_price)
        + quantity * (low_close.unit_price - trade.cheap_entry_price)
        - trade.entry_fees - trade.exit_fees - estimated_exit_fee
        + trade.funding_total - trade.borrow_total + trade.price_pnl
    )
    trade.max_adverse_net = (
        marked_net if trade.max_adverse_net is None else min(marked_net, trade.max_adverse_net)
    )
    held = at - trade.opened_at if trade.opened_at else timedelta(0)
    reason = None
    if marked_net <= -(
        settings.initial_balance_per_exchange * D(2) * settings.max_loss_fraction
    ):
        reason = "paper_loss_budget"
    elif close_difference <= trade.target_close_difference and marked_net > 0:
        reason = "target_capture"
    elif held >= timedelta(seconds=settings.max_holding_seconds):
        reason = "maximum_holding"
    elif held >= timedelta(seconds=settings.routine_exit_seconds):
        reason = "routine_two_hour_exit"
    elif (held >= timedelta(seconds=settings.no_improvement_seconds)
          and "half_reduced" not in trade.risk_labels
          and trade.best_close_difference >= trade.signal_open_difference):
        reason = "no_improvement_half"
    if reason is None:
        return trade
    if reason == "no_improvement_half":
        step = common_base_step(route.expensive, route.cheap)
        quantity = ((quantity / D(2)) / step).to_integral_value(rounding=ROUND_DOWN) * step
        if quantity <= 0:
            return trade
        trade.risk_labels.append("half_reduced")
    trade.exit_reason = reason
    trade.orders.append(_order(
        trade, "exit", route.expensive, "buy", quantity,
        expensive.asks[0].price / route.expensive.contract_base_qty, at, settings,
        expensive.sequence,
    ))
    trade.orders.append(_order(
        trade, "exit", route.cheap, "sell", quantity,
        cheap.bids[0].price / route.cheap.contract_base_qty, at, settings,
        cheap.sequence,
    ))
    trade.status = "pending_exit"
    return trade


def quantity_at_settlement(trade: PaperTrade, leg_key: str, at: datetime) -> Decimal:
    quantity = D(0)
    for fill in trade.fills:
        if fill.leg_key != leg_key or fill.filled_at > at:
            continue
        quantity += fill.quantity if fill.role == "entry" else -fill.quantity
    return max(D(0), quantity)


def apply_funding_settlement(
    trade: PaperTrade, accounts: dict[str, PaperAccount], settlement: FundingSettlement,
) -> bool:
    if settlement.market_key not in {trade.expensive_key, trade.cheap_key}:
        raise ValueError("funding market identity mismatch")
    if trade.opened_at is None or settlement.settled_at <= trade.opened_at or (
        trade.closed_at is not None and settlement.settled_at > trade.closed_at
    ):
        return False
    key = f"{trade.id}:funding:{settlement.market_key}:{settlement.settled_at.isoformat()}"
    if any(item.id == key for item in trade.cashflows):
        return False
    quantity = quantity_at_settlement(trade, settlement.market_key, settlement.settled_at)
    if quantity <= 0:
        return False
    sign = D(1) if settlement.market_key == trade.expensive_key else D(-1)
    amount = sign * quantity * settlement.mark_price * settlement.rate
    flow = PaperCashflow(
        id=key, trade_id=trade.id, leg_key=settlement.market_key,
        exchange=settlement.exchange, kind="funding", amount=amount,
        occurred_at=settlement.settled_at, source=settlement.source,
        rate=settlement.rate, mark_price=settlement.mark_price,
        mark_kind=settlement.mark_kind,
    )
    trade.cashflows.append(flow)
    trade.funding_total += amount
    account = accounts[settlement.exchange]
    account.cash_balance += amount
    account.funding_cashflow += amount
    account.updated_at = max(account.updated_at, settlement.settled_at)
    if settlement.mark_kind == "one_minute_proxy" and "funding_mark_proxy" not in trade.risk_labels:
        trade.risk_labels.append("funding_mark_proxy")
    return True
