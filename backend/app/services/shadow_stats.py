"""Completed-event statistics aligned with arb-mvp EV ledger specification §6."""

import math
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import fmean, stdev

DIMENSIONS = frozenset({"variant", "pair", "spread_tier", "liquidity", "routes", "fake_flag", "executable", "strategy_version"})
COMPONENTS = ("spread_bps", "slippage_bps", "fees_bps", "funding_bps")
# One-sided 95%, df=1..29. df>=30 follows the specification's 1.645.
T95 = (0, 6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833,
       1.812, 1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729,
       1.725, 1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699)
BEIJING = timezone(timedelta(hours=8))


def quantile(sorted_values, probability):
    at = (len(sorted_values) - 1) * probability
    lo = int(at)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (at - lo)


def event_day(row):
    return datetime.fromisoformat(row["closed_at"]).astimezone(BEIJING).date().isoformat()


def statistics(rows, seed=20261103):
    rows = sorted((r for r in rows if r.get("closed_at") and r.get("net_bps") is not None
                   and r.get("exit_reason") != "retired"),
                  key=lambda r: (r["closed_at"], r["id"]))
    n = len(rows)
    if not n:
        return {"n_lot": 0, "n_evt": 0, "days": 0, "coins": 0, "status": "INSUFFICIENT",
                "win_rate": None, "wilson_low": None, "wilson_high": None, "E": None,
                "sigma": None, "average_win": None, "average_loss": None,
                "t_lcb": None, "bootstrap_lcb": None, "lcb": None, "n_min": None,
                "p5": None, "cvar5": None, "worst": None, "max_drawdown": 0,
                "equity_curve": [], **dict.fromkeys(COMPONENTS)}
    values = [r["net_bps"] for r in rows]
    mean = fmean(values)
    sigma = stdev(values) if n > 1 else 0.0
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    win_rate = len(wins) / n
    z = 1.96
    denominator = 1 + z * z / n
    centre = (win_rate + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(win_rate * (1 - win_rate) / n + z * z / (4 * n * n)) / denominator
    daily = defaultdict(list)
    for row in rows:
        daily[event_day(row)].append(row["net_bps"])
    blocks = [(sum(daily[k]), len(daily[k])) for k in sorted(daily)]
    rng = random.Random(seed)
    bootstrap = []
    for _ in range(2000):
        total = count = 0
        for _ in blocks:
            block_sum, block_n = blocks[rng.randrange(len(blocks))]
            total += block_sum
            count += block_n
        bootstrap.append(total / count)
    bootstrap_lcb = quantile(sorted(bootstrap), 0.05)
    t_lcb = mean - (T95[n - 1] if n - 1 < 30 else 1.645) * sigma / math.sqrt(n) if n > 1 else None
    lcb = min(t_lcb, bootstrap_lcb) if t_lcb is not None else None
    n_min = max(30, math.ceil((1.645 * sigma / mean) ** 2)) if mean > 0 else None
    coins = len({r["symbol"] for r in rows})
    insufficient = n < (n_min or 30) or len(blocks) < 5 or coins < 3
    status = "INSUFFICIENT" if insufficient else "POSITIVE" if lcb is not None and lcb > 0 else "NOT_POSITIVE"
    sorted_values = sorted(values)
    equity = peak = drawdown = 0.0
    curve = []
    for row in rows:
        equity += row["net_bps"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        curve.append({"closed_at": row["closed_at"], "net_bps": equity})
    return {"n_lot": n, "n_evt": n, "days": len(blocks), "coins": coins, "status": status,
            "win_rate": win_rate, "wilson_low": max(0, centre - radius), "wilson_high": min(1, centre + radius),
            "E": mean, "sigma": sigma, "average_win": fmean(wins) if wins else None,
            "average_loss": fmean(losses) if losses else None,
            "t_lcb": t_lcb, "bootstrap_lcb": bootstrap_lcb, "lcb": lcb, "n_min": n_min,
            "p5": quantile(sorted_values, 0.05), "cvar5": fmean(sorted_values[:max(1, math.ceil(n * 0.05))]),
            "worst": sorted_values[0], "max_drawdown": drawdown, "equity_curve": curve,
            **{field: fmean(r[field] for r in rows) for field in COMPONENTS}}


def buckets(rows, dims, include_fake=False):
    if len(dims) != len(set(dims)) or not set(dims) <= DIMENSIONS:
        raise ValueError("Invalid shadow dimensions")
    groups = defaultdict(list)
    for row in rows:
        if row.get("closed_at") and row.get("exit_reason") != "retired" and (include_fake or not row["fake_flag"]):
            groups[tuple(row[dim] for dim in dims)].append(row)
    return [{"dimensions": dict(zip(dims, key)), **statistics(group)} for key, group in groups.items()]
