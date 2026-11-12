"""Opening sizing in common base units, with no rounding up to meet minimums."""
from decimal import ROUND_FLOOR

from tools.fill_sim.core import common_step, dec, walk
from .policy import limit_price


def size_plan(instruments, books, interests, config, remaining_total, now):
    if len(instruments) != 2 or len(books) != 2 or len(interests) != 2:
        raise ValueError('two_legs_required')
    valid = []
    for leg, (i, oi) in enumerate(zip(instruments, interests)):
        try:
            if (oi['unit_verified'] is True and oi['market_key'] == i.market.key
                    and 0 <= dec(now)-dec(oi['at']) <= 300 and dec(oi['usd']) >= 0):
                valid.append((leg, dec(oi['usd'])))
        except (KeyError, TypeError, ValueError):
            pass
    if not valid:
        raise ValueError('oi_missing')
    depths, prices = [], []
    for i, book, side in zip(instruments, books, ('buy', 'sell')):
        levels = book.asks if side == 'buy' else book.bids
        if not levels:
            raise ValueError('size_too_small')
        band = dec(limit_price(levels[0][0], side, i.tick))
        eligible = [(dec(p), dec(q)) for p, q in levels
                    if (dec(p) <= band if side == 'buy' else dec(p) >= band)]
        if any(p <= 0 or q < 0 for p, q in eligible):
            raise ValueError('invalid_depth')
        depths.append(sum((p*q for p, q in eligible), dec(0)))
        # Include outward tick rounding in the budget before flooring quantity.
        deepest = max((p for p, q in eligible), default=band) if side == 'buy' else min((p for p, q in eligible), default=band)
        prices.append(dec(limit_price(deepest, side, i.tick)))
    remaining = max(dec(0), dec(remaining_total))
    constraints = dict(order_usd_max=min(dec(config['order_usd_max']), dec(150)),
                       depth=min(depths)*dec(config['depth_fraction']),
                       oi=min(v for _, v in valid)*dec(config['oi_fraction']),
                       remaining_total=remaining*max(prices)/sum(prices))
    limiting = min(constraints, key=constraints.get)
    budget = constraints[limiting]
    step = common_step(*(i.spec.qty_step for i in instruments))
    quantity = (budget/max(prices)/step).to_integral_value(rounding=ROUND_FLOOR)*step
    if quantity <= 0:
        raise ValueError('size_too_small')
    fills = [walk(books[0].asks, quantity=quantity), walk(books[1].bids, quantity=quantity)]
    limits = [limit_price(f['deepest'], side, i.tick) for f, side, i in zip(fills, ('buy', 'sell'), instruments)]
    if any(quantity > dec(i.spec.max_qty) for i in instruments):
        raise ValueError('final_size_above_max')
    if any(quantity < dec(i.spec.min_qty) or quantity*dec(p) < dec(i.spec.min_notional)
           for i, p in zip(instruments, limits)):
        raise ValueError('size_too_small')
    return dict(quantity=str(quantity), limits=limits, depth_usd=list(map(str, depths)),
                oi_usd=[str(next((v for leg, v in valid if leg == n), '')) or None for n in (0, 1)],
                oi_legs_used=[leg for leg, _ in valid], constraints={k: str(v) for k, v in constraints.items()},
                remaining_total_usd=str(remaining), budget_usd=str(budget), limiting=limiting)
