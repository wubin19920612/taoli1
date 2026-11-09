"""Decimal execution math; quantities are base units, prices quote/base."""
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR, InvalidOperation
from math import gcd
from datetime import datetime, timezone

AMOUNTS = (100, 300, 1000, 3000)
VARIANTS = ("measured", "plus_1s", "leg_delay")
EXCHANGES = ("gate", "bitget", "binance", "bybit", "okx", "aster")


def dec(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("invalid_number") from None
    if not result.is_finite():
        raise ValueError("nonfinite_number")
    return result


def timestamp(value):
    if isinstance(value, (float, int)):
        return float(dec(value))
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result.timestamp()


def day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


@dataclass(frozen=True)
class Market:
    exchange: str
    market_type: str
    raw_symbol: str
    price_multiplier: str = "1"
    contract_size_multiplier: str = None

    @classmethod
    def from_payload(cls, row):
        if row.get("dex") or row["exchange"] not in EXCHANGES:
            raise ValueError("unsupported_market")
        if row["market_type"] not in ("spot", "future"):
            raise ValueError("unsupported_market")
        raw = row["raw_symbol"]
        if not raw or not all(c.isalnum() or c in "_-" for c in raw):
            raise ValueError("invalid_raw_symbol")
        return cls(row["exchange"], row["market_type"], raw,
                   str(row.get("price_multiplier", 1)),
                   None if row.get("contract_size_multiplier") is None
                   else str(row["contract_size_multiplier"]))

    @property
    def key(self):
        return "|".join((self.exchange, self.market_type, self.raw_symbol))


@dataclass(frozen=True)
class Spec:
    base: str
    quote: str
    contract_size: str
    qty_step: str
    min_qty: str
    min_notional: str
    price_multiplier: str = "1"
    max_qty: str = "1E30"

    def validate(self, market):
        for x in (self.contract_size, self.qty_step, self.price_multiplier, self.max_qty):
            if dec(x) <= 0:
                raise ValueError("invalid_spec")
        if min(dec(self.min_qty), dec(self.min_notional)) < 0 or self.quote != "USDT":
            raise ValueError("unsupported_spec")
        if dec(market.price_multiplier) != dec(self.price_multiplier):
            raise ValueError("spec_mismatch")
        if (market.contract_size_multiplier is not None
                and dec(market.contract_size_multiplier) != dec(self.contract_size)):
            raise ValueError("spec_mismatch")


@dataclass
class Book:
    bids: list
    asks: list
    received_at: float
    exchange_at: float = None

    def to_dict(self):
        return asdict(self)


def common_step(a, b):
    a, b = dec(a), dec(b)
    scale = 10 ** max(0, -a.as_tuple().exponent, -b.as_tuple().exponent)
    x, y = int(a * scale), int(b * scale)
    return Decimal(x * y // gcd(x, y)) / scale


def walk(levels, *, quantity=None, notional=None):
    """One of quantity/notional must be specified. No partial fills."""
    if (quantity is None) == (notional is None):
        raise ValueError("one_target_required")
    remaining = dec(quantity if quantity is not None else notional)
    if remaining <= 0:
        raise ValueError("below_min")
    qty, cost, count, last = dec(0), dec(0), 0, None
    for p, q in levels:
        p, q = dec(p), dec(q)
        take = min(q, remaining if quantity is not None else remaining / p)
        qty += take
        cost += take * p
        remaining -= take if quantity is not None else take * p
        count += 1
        last = p
        if remaining <= dec("1e-24"):
            first = dec(levels[0][0])
            return dict(qty=str(qty), notional=str(cost), vwap=str(cost / qty),
                        levels=count, deepest=str(last),
                        slippage_bps=str(abs(cost / qty / first - 1) * 10000))
    raise ValueError("too_thin")


def opening(a, b, sa, sb, amount, fee_a, fee_b, threshold=10):
    result = {"amount": amount}
    try:
        qa = dec(walk(a.asks, notional=amount)["qty"])
        qb = dec(walk(b.bids, notional=amount)["qty"])
        step = common_step(sa.qty_step, sb.qty_step)
        qty = (min(qa, qb) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if qty <= 0 or qty < max(dec(sa.min_qty), dec(sb.min_qty)):
            raise ValueError("below_min")
        if qty > min(dec(sa.max_qty), dec(sb.max_qty)):
            raise ValueError("above_max")
        fa, fb = walk(a.asks, quantity=qty), walk(b.bids, quantity=qty)
        if dec(fa["notional"]) < dec(sa.min_notional) or dec(fb["notional"]) < dec(sb.min_notional):
            raise ValueError("below_min")
        gross = (dec(fb["vwap"]) / dec(fa["vwap"]) - 1) * 10000
        fees = 2 * (dec(fee_a) + dec(fee_b)) * 10000
        net = gross - fees
        top_net = (dec(b.bids[0][0]) / dec(a.asks[0][0]) - 1) * 10000 - fees
        status = "opened" if net >= dec(threshold) else ("thin" if top_net >= dec(threshold) else "gone")
        result.update(status=status, quantity=str(qty), buy=fa, sell=fb,
                      gross_bps=float(gross), net_bps=float(net),
                      fee_a=str(fee_a), fee_b=str(fee_b))
    except ValueError as exc:
        result["status"] = str(exc)
    return result


def close_reason(age, close_spread, open_spread, stale=False):
    if stale:
        return "stale_exit"
    if age >= 172800:
        return "timeout_48h"
    if close_spread <= open_spread / 2:
        return "timeout_24h_converged" if age >= 86400 else "converged"
    return None


def track_interval(age):
    return 60 if age > 3600 else (15 if age > 300 else 3)


def funding_pnl(schedules, opened, closed, buy_notional, sell_notional):
    """Opening snapshots: rate is a decimal ratio, not percent; next is UTC seconds.

    Unknown schedules are NEVER silently treated as known zero funding.
    """
    total, known = dec(0), True
    for i, notional in enumerate((buy_notional, sell_notional)):
        s = schedules[i] if schedules and len(schedules) == 2 else None
        if s and s.get("spot"):
            continue
        if not s or any(s.get(k) is None for k in ("next", "rate", "interval")):
            known = False
            continue
        interval = float(s["interval"])
        if interval <= 0:
            known = False
            continue
        first = float(s["next"])
        if first <= opened:
            first += (int((opened - first) // interval) + 1) * interval
        count = 0 if closed < first else int((closed - first) // interval) + 1
        total += dec(notional) * dec(s["rate"]) * count * (-1 if i == 0 else 1)
    return float(total), known


def closing(position, a, b, now, stale=False):
    entry = position["fill"]
    fa = walk(a.bids, quantity=entry["quantity"])
    fb = walk(b.asks, quantity=entry["quantity"])
    spread = float((dec(fb["vwap"]) / dec(fa["vwap"]) - 1) * 10000)
    age = now - position["opened_at"]
    reason = close_reason(age, spread, entry["gross_bps"], stale)
    if reason is None:
        return None
    na, nb = dec(entry["buy"]["notional"]), dec(entry["sell"]["notional"])
    ca, cb = dec(fa["notional"]), dec(fb["notional"])
    fees = (na + ca) * dec(entry["fee_a"]) + (nb + cb) * dec(entry["fee_b"])
    funding, known = funding_pnl(position.get("funding"), position["opened_at"], now, na, nb)
    pnl = ca - na + nb - cb - fees + dec(funding)
    return dict(closed_at=now, holding_seconds=age, exit_reason=reason,
                close_buy=fa, close_sell=fb, close_spread_bps=spread,
                fees_usd=float(fees), funding_usd=funding, funding_known=known,
                funding_estimated=True, net_usd=float(pnl), net_bps=float(pnl / na * 10000),
                main_eligible=not stale and known,
                close_tA=a.received_at, close_tB=b.received_at,
                close_exchange_tA=a.exchange_at, close_exchange_tB=b.exchange_at)
