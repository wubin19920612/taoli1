"""Conservative same-quantity FS scenarios; no basis convergence assumption."""

from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from math import ceil, floor, lcm

from app.models.fs_monitor import FsCandidate, FsScenario, FsSettings
from app.services.fs_bybit import FsDataError, number, timestamp


def common_step(left, right):
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        if not a.is_finite() or not b.is_finite() or min(a, b) <= 0:
            raise ValueError
        digits = max(0, -a.as_tuple().exponent, -b.as_tuple().exponent)
        if digits > 12:
            raise ValueError
        scale = 10**digits
        return Decimal(lcm(int(a * scale), int(b * scale))) / scale
    except (ValueError, ArithmeticError):
        raise FsDataError("quantity_step_unknown") from None


def vwap(levels, quantity, *, bids):
    remain, total, previous = quantity, 0.0, None
    for entry in levels:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise FsDataError("invalid_orderbook")
        price, size = (number(x, nonnegative=True) for x in entry)
        if price is None or size is None or price <= 0 or size <= 0:
            raise FsDataError("invalid_orderbook")
        if previous is not None and (
            (bids and price > previous) or (not bids and price < previous)
        ):
            raise FsDataError("invalid_orderbook")
        previous = price
        filled = min(remain, size)
        total += filled * price
        remain -= filled
        if remain <= max(1e-10, quantity * 1e-12):
            return total / quantity
    raise FsDataError("insufficient_depth")


def settlement_count(now, next_at, interval_hours, hours):
    if next_at <= now or interval_hours <= 0:
        raise FsDataError("funding_schedule_unknown")
    seconds = (now + timedelta(hours=hours) - next_at).total_seconds()
    # Boundary is deliberately strict: an exit exactly at settlement does not guarantee payment.
    return 0 if seconds <= 0 else 1 + floor((seconds - 1e-6) / (interval_hours * 3600))


def scenario(row, settings, now, mark, label, hours):
    q, p = row.quantity, row.prices
    count = settlement_count(now, row.next_funding_at, row.interval_hours, hours)
    funding = -q * mark * row.funding_pct / 100 * count
    budget_hours = max(1, ceil(hours))
    borrow = row.borrow_required * row.hourly_borrow_pct / 100 * budget_hours * p["spot_buy"]
    fees = q * (
        (p["spot_sell"] + p["spot_buy"]) * row.spot_fee_pct / 100
        + (p["future_buy"] + p["future_sell"]) * row.future_fee_pct / 100
    )
    price_pnl = q * (p["spot_sell"] - p["future_buy"] - p["spot_buy"] + p["future_sell"])
    notional = q * p["spot_sell"]
    buffer = notional * settings.execution_buffer_pct / 100
    net = funding - borrow - fees + price_pnl - buffer
    return FsScenario(
        label=label,
        hours=hours,
        settlements=count,
        budget_interest_hours=budget_hours,
        funding_usdt=funding,
        borrow_usdt=borrow,
        fees_usdt=fees,
        round_trip_price_pnl_usdt=price_pnl,
        buffer_usdt=buffer,
        net_usdt=net,
        net_pct=net / notional * 100,
        stressed_net_usdt=net - notional * settings.basis_stress_pct / 100,
    )


def evaluate(
    row: FsCandidate,
    settings: FsSettings,
    *,
    spot_meta,
    future_meta,
    spot_book,
    future_book,
    mark,
    now,
):
    """Return an observation even when input data cannot support an economic estimate."""
    try:
        if (
            spot_meta.get("baseCoin") != row.base
            or future_meta.get("baseCoin") != row.base
            or spot_meta.get("quoteCoin") != "USDT"
            or future_meta.get("quoteCoin") != "USDT"
            or future_meta.get("settleCoin") != "USDT"
            or future_meta.get("contractType") != "LinearPerpetual"
            or any(m.get("status") != "Trading" for m in (spot_meta, future_meta))
            or any(m.get("symbol") != row.symbol for m in (spot_meta, future_meta))
            or future_meta.get("isPreListing") is True
        ):
            raise FsDataError("unsupported_contract")
        for key, book in (("spot_book", spot_book), ("future_book", future_book)):
            if book.get("s") != row.symbol:
                raise FsDataError("orderbook_symbol_mismatch")
            source = timestamp(book.get("ts"))
            row.source_times[key] = source
            if not -5 <= (now - source).total_seconds() <= 30:
                raise FsDataError("stale_orderbook")
            if not book.get("b") or not book.get("a"):
                raise FsDataError("insufficient_depth")
            if float(book["b"][0][0]) > float(book["a"][0][0]):
                raise FsDataError("invalid_orderbook")
        if (
            abs((row.source_times["spot_book"] - row.source_times["future_book"]).total_seconds())
            > 10
        ):
            raise FsDataError("leg_time_skew")
        for source in row.source_times.values():
            if not -5 <= (now - source).total_seconds() <= 60:
                raise FsDataError("stale_input")
        input_valid_until = min(
            value + timedelta(seconds=30 if "book" in key else 60)
            for key, value in row.source_times.items()
        )
        row.valid_until = (
            min(row.valid_until, input_valid_until) if row.valid_until else input_valid_until
        )
        if row.next_funding_at is not None:
            row.valid_until = min(row.valid_until, row.next_funding_at)
        spot_lot, future_lot = (
            spot_meta.get("lotSizeFilter", {}),
            future_meta.get("lotSizeFilter", {}),
        )
        step = common_step(spot_lot.get("basePrecision"), future_lot.get("qtyStep"))
        best = number(spot_book["b"][0][0], nonnegative=True)
        if not best:
            raise FsDataError("invalid_orderbook")
        units = (Decimal(str(settings.target_usdt)) / Decimal(str(best)) / step).to_integral_value(
            rounding=ROUND_DOWN
        )
        q = float(units * step)
        if q <= 0:
            raise FsDataError("quantity_below_minimum")
        row.quantity = q
        for book, prefix in ((spot_book, "spot"), (future_book, "future")):
            row.prices[prefix + "_sell"] = vwap(book["b"], q, bids=True)
            row.prices[prefix + "_buy"] = vwap(book["a"], q, bids=False)
        for lot, prefix in ((spot_lot, "spot"), (future_lot, "future")):
            min_q = number(lot.get("minOrderQty"), nonnegative=True)
            min_amt = number(
                lot.get("minOrderAmt" if prefix == "spot" else "minNotionalValue"), nonnegative=True
            )
            if min_q is None or min_amt is None:
                raise FsDataError("order_limits_unknown")
            if (
                q < min_q
                or q * min(row.prices[prefix + "_buy"], row.prices[prefix + "_sell"]) < min_amt
            ):
                raise FsDataError("quantity_below_minimum")
            maximum = number(
                lot.get("maxMarketOrderQty" if prefix == "spot" else "maxMktOrderQty"),
                nonnegative=True,
            )
            if maximum is not None and q > maximum:
                raise FsDataError("quantity_above_maximum")
        if row.product_borrowable is not True:
            row.blockers.append(
                "borrow_not_supported"
                if row.product_borrowable is False
                else "borrow_support_unknown"
            )
        if any(
            v is None or v < settings.min_volume_usdt
            for v in (row.spot_volume_usdt, row.future_volume_usdt)
        ):
            row.blockers.append("low_or_unknown_volume")
        if row.hourly_borrow_pct is None or not 0 <= row.hourly_borrow_pct < 10:
            raise FsDataError("borrow_rate_unknown")
        row.borrow_required = q / (1 - row.hourly_borrow_pct / 100)
        if row.account_state == "checked":
            if row.account_max_loan is None or row.available_inventory is None:
                row.blockers.append("account_capacity_unknown")
            else:
                capacity = min(row.account_max_loan, row.available_inventory)
                row.capacity_usdt = (
                    capacity * (1 - row.hourly_borrow_pct / 100) * row.prices["spot_sell"]
                )
                if capacity < row.borrow_required:
                    row.blockers.append("insufficient_borrow_capacity")
                elif row.product_borrowable is True:
                    row.stage = "quota_sufficient"
        else:
            row.blockers.append("account_" + row.account_state)
        if row.fee_source != "account":
            row.blockers.append("fees_unverified")
        if row.identity_state != "verified":
            row.blockers.append("identity_unverified")
        if row.funding_pct is None or row.funding_pct >= 0:
            raise FsDataError("negative_funding_missing")
        if not row.interval_hours or not row.next_funding_at or not mark or mark <= 0:
            raise FsDataError("funding_schedule_unknown")
        if (
            row.next_funding_at <= now
            or (row.next_funding_at - now).total_seconds() > row.interval_hours * 3600 + 60
        ):
            raise FsDataError("funding_schedule_unknown")
        next_hours = (row.next_funding_at - now).total_seconds() / 3600 + 2 / 60
        row.scenarios = [
            scenario(row, settings, now, mark, label, hours)
            for label, hours in (
                ("next", next_hours),
                ("holding", settings.holding_hours),
                ("24h", 24),
            )
        ]
        holding = row.scenarios[1]
        notional = q * row.prices["spot_sell"]
        if holding.stressed_net_usdt / notional * 100 < settings.min_net_pct:
            row.blockers.append("cost_threshold_not_met")
        if not row.blockers:
            row.stage = "cost_pass"
    except (FsDataError, ValueError, TypeError, KeyError, OverflowError) as exc:
        row.blockers.append(str(exc) if isinstance(exc, FsDataError) else "invalid_market_data")
        row.stage = "blocked"
        row.scenarios = []
    if any(
        x in row.blockers
        for x in ("borrow_not_supported", "insufficient_borrow_capacity", "identity_conflict")
    ):
        row.stage = "blocked"
    row.blockers = list(dict.fromkeys(row.blockers))
    return row
