from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import mean
from typing import Any

from .paper_models import PaperAccount, PaperRun, PaperTrade

D = Decimal


def _minimum(values: list[Decimal]) -> str | None:
    return str(min(values)) if values else None


def build_paper_report(
    run: PaperRun | None, trades: list[PaperTrade],
    accounts: dict[str, PaperAccount], now: datetime,
) -> dict[str, Any]:
    now = now.astimezone(UTC)
    continuous_start = (run.continuous_started_at or run.started_at) if run else None
    elapsed_days = max(0, (now - continuous_start).total_seconds() / 86400) if continuous_start else 0
    independent_events = len({
        trade.event_id for trade in trades
        if continuous_start is not None and trade.signal_at >= continuous_start
    })
    sufficient = (
        elapsed_days >= 14 and independent_events >= 30
        and run is not None and not run.coverage_gap_open
        and run.last_success_at is not None
        and timedelta(0) <= now - run.last_success_at <= timedelta(seconds=15)
    )
    closed = sorted(
        (trade for trade in trades
         if trade.status == "closed" and trade.closed_at and trade.funding_gap_at is None),
        key=lambda trade: (trade.closed_at, trade.id),
    )
    initial_capital = sum((item.initial_balance for item in accounts.values()), D(0))
    closed_net = sum((trade.net_realized for trade in closed), D(0))
    realized_cashflow = sum((trade.net_realized for trade in trades), D(0))
    equity = initial_capital
    peak = equity
    drawdown = D(0)
    for trade in closed:
        equity += trade.net_realized
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    durations = [
        (trade.closed_at - trade.opened_at).total_seconds()
        for trade in closed if trade.opened_at and trade.closed_at
    ]
    failed_entries = [
        trade for trade in trades
        if trade.exit_reason == "entry_partial_or_leg_failure"
    ]
    attempted_entries = [
        trade for trade in trades if trade.status not in {"blocked", "pending_entry"}
    ]
    unfinished = [
        trade for trade in trades
        if trade.status in {"pending_entry", "recovering", "open", "pending_exit", "unresolved"}
        or trade.funding_gap_at is not None
    ]
    adverse = [
        max(D(0), trade.worst_close_difference - trade.signal_open_difference)
        for trade in trades if trade.worst_close_difference is not None
    ]
    free_by_leg: dict[str, list[Decimal]] = defaultdict(list)
    capacities: dict[str, dict[str, int]] = defaultdict(
        lambda: {"events": 0, "depth_qualified": 0, "cost_qualified": 0}
    )
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"events": 0, "closed": 0, "failed_or_unfilled": 0, "closed_net": D(0)}
    )
    for trade in trades:
        route = grouped[trade.route_id]
        route["events"] += 1
        if trade.status == "closed" and trade.funding_gap_at is None:
            route["closed"] += 1
            route["closed_net"] += trade.net_realized
        if trade.status in {"blocked", "unfilled", "unresolved"} or trade in failed_entries:
            route["failed_or_unfilled"] += 1
        if trade.minimum_expensive_free_balance is not None:
            free_by_leg[trade.expensive_key].append(trade.minimum_expensive_free_balance)
        if trade.minimum_cheap_free_balance is not None:
            free_by_leg[trade.cheap_key].append(trade.minimum_cheap_free_balance)
        for capacity in trade.signal_capacities:
            target = str(capacity["target_notional"])
            row = capacities[target]
            row["events"] += 1
            blockers = capacity["blockers"]
            if not any("depth_insufficient" in reason or "impact_excess" in reason
                       for reason in blockers):
                row["depth_qualified"] += 1
            if not blockers:
                row["cost_qualified"] += 1
    funding_actual = sum(
        (flow.amount for trade in trades for flow in trade.cashflows
         if flow.kind == "funding" and flow.mark_kind == "actual"), D(0)
    )
    funding_proxy = sum(
        (flow.amount for trade in trades for flow in trade.cashflows
         if flow.kind == "funding" and flow.mark_kind == "one_minute_proxy"), D(0)
    )
    stress = []
    for trade in unfinished:
        if trade.expensive_entry_price is None or trade.cheap_entry_price is None:
            continue
        reference = trade.cheap_entry_price
        short_exchange = trade.expensive_key.split("|", 1)[0]
        short_account = accounts.get(short_exchange)
        stress.append({
            "trade_id": trade.id,
            "common_up_100pct_short_account_free_after_price_move": str(
                short_account.free_balance
                - trade.expensive_entry_price * trade.expensive_open_quantity
            ) if short_account else None,
            "spread_widens_5pct_additional_loss": str(
                trade.target_quantity * reference * D("0.05")
            ),
            "spread_widens_10pct_additional_loss": str(
                trade.target_quantity * reference * D("0.10")
            ),
            "method": "paper_price_stress_without_maintenance_margin_tiers",
        })
    return {
        "rule_version": run.settings.rule_version if run else None,
        "frozen_parameters": run.settings.model_dump(mode="json") if run else None,
        "started_at": run.started_at.isoformat() if run else None,
        "continuous_started_at": continuous_start.isoformat() if continuous_start else None,
        "coverage_gap_count": run.coverage_gap_count if run else 0,
        "coverage_gap_open": run.coverage_gap_open if run else False,
        "as_of": now.isoformat(),
        "elapsed_days": round(elapsed_days, 3),
        "independent_events": independent_events,
        "total_independent_events": len({trade.event_id for trade in trades}),
        "minimum_days": 14,
        "minimum_independent_events": 30,
        "sample_status": "ready_for_review" if sufficient else "sample_insufficient",
        "profitability_conclusion": None,
        "closed_trades": len(closed),
        "closed_with_funding_gap": sum(
            trade.status == "closed" and trade.funding_gap_at is not None
            for trade in trades
        ),
        "blocked_events": sum(trade.status == "blocked" for trade in trades),
        "unfilled_events": sum(trade.status == "unfilled" for trade in trades),
        "unresolved_events": sum(trade.status == "unresolved" for trade in trades),
        "unfinished_events": len(unfinished),
        "partial_or_single_leg_failures": len(failed_entries),
        "single_leg_failure_rate": (
            len(failed_entries) / len(attempted_entries) if attempted_entries else None
        ),
        "closed_net_pnl": str(closed_net),
        "net_per_closed_trade": str(closed_net / len(closed)) if closed else None,
        "realized_cashflow_to_date": str(realized_cashflow),
        "initial_capital_both_accounts": str(initial_capital),
        "closed_trade_capital_return": str(closed_net / initial_capital)
        if initial_capital > 0 else None,
        "max_drawdown_closed_trade_only": str(drawdown),
        "max_drawdown_rate_closed_trade_only": str(drawdown / initial_capital)
        if initial_capital > 0 else None,
        "average_holding_seconds": mean(durations) if durations else None,
        "max_holding_seconds": max(durations) if durations else None,
        "maximum_adverse_spread_expansion": str(max(adverse)) if adverse else None,
        "minimum_free_balance_by_leg": {
            key: _minimum(values) for key, values in free_by_leg.items()
        },
        "funding_gap_trades": sum(trade.funding_gap_at is not None for trade in trades),
        "total_entry_and_exit_fees": str(sum(
            (trade.entry_fees + trade.exit_fees for trade in trades), D(0)
        )),
        "funding_cashflow_actual_mark": str(funding_actual),
        "funding_cashflow_proxy_mark": str(funding_proxy),
        "borrow_cost": str(sum((trade.borrow_total for trade in trades), D(0))),
        "capacity_by_target_notional": dict(capacities),
        "by_route": {
            key: {**row, "closed_net": str(row["closed_net"])}
            for key, row in grouped.items()
        },
        "open_trade_stress": stress,
        "collateral_model_incomplete": True,
        "drawdown_excludes_open_positions": bool(unfinished),
    }
