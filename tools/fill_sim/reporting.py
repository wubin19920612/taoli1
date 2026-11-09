"""UTC daily statistics and day-cluster bootstrap. Unknown evidence cannot pass."""
from collections import Counter, defaultdict
import random
from statistics import mean
from .core import AMOUNTS, day


def stats(rows):
    values = [r["net_bps"] for r in rows]
    return {"n": len(values), "mean_bps": mean(values) if values else None,
            "win_rate": sum(v > 0 for v in values) / len(values) if values else None,
            "usd": sum(r["net_usd"] for r in rows)}


def bootstrap_lower(rows, samples=2000, seed=1905):
    days = defaultdict(list)
    for row in rows:
        days[day(row["closed_at"])].append(row["net_bps"])
    groups = [(sum(v), len(v)) for v in days.values()]
    if len(groups) < 2:
        return None
    rng, values = random.Random(seed), []
    for _ in range(samples):
        chosen = rng.choices(groups, k=len(groups))
        values.append(sum(v[0] for v in chosen) / sum(v[1] for v in chosen))
    values.sort()
    # One-sided 90% lower bound, resampling entire days, trade-weighted mean.
    return values[int(samples * .1)]


def summarize(events, fills, positions):
    events, fills, positions = list(events), list(fills), list(positions)
    closed = [p for p in positions if p.get("closed_at") is not None]
    eligible = [p for p in closed if p.get("main_eligible")]
    main = [p for p in eligible if p["variant"] == "measured" and p["amount"] == 300]
    opened = [f for f in fills if f["variant"] == "measured" and f["amount"] == 300 and f["status"] == "opened"]
    considered = [e for e in events if e.get("filter_passed")]
    counts = Counter(p["symbol"] for p in main)
    top = sorted(counts, key=lambda s: (-counts[s], s))[:3]
    remainder = [p for p in main if p["symbol"] not in top]
    amount_stats = {str(n): stats([p for p in eligible if p["variant"] == "measured" and p["amount"] == n]) for n in AMOUNTS}
    biases = sorted(p["shadow_net_bps"] - p["net_bps"] for p in main if p.get("shadow_net_bps") is not None)
    grouped = {}
    for key in ("pair", "symbol", "spread_tier"):
        groups = defaultdict(list)
        for p in main:
            groups[p[key]].append(p)
        grouped[key] = {k: stats(v) for k, v in sorted(groups.items())}
    return dict(events=len(events), considered_events=len(considered),
                skipped_reasons=dict(Counter(e["status"] for e in events)),
                still_there_rate=len(opened) / len(considered) if considered else None,
                measured_300_open_events=len({f["event_id"] for f in opened}),
                coins=len({f["symbol"] for f in opened}), pairs=len({f["pair"] for f in opened}),
                amounts=amount_stats, main=stats(main),
                variants={v:{str(n):stats([p for p in eligible if p["variant"]==v and p["amount"]==n])
                             for n in AMOUNTS} for v in ("measured","plus_1s","leg_delay")},
                provisional_price_only={str(n):stats([p for p in closed if p["variant"]=="measured" and p["amount"]==n
                                                      and p.get("exit_reason") not in ("stale_exit","unpriced_exit")])
                                        for n in AMOUNTS},
                capacity_curve=[dict(amount=n, **amount_stats[str(n)]) for n in AMOUNTS],
                first_nonpositive_amount=next((n for n in AMOUNTS if amount_stats[str(n)]["mean_bps"] is not None and amount_stats[str(n)]["mean_bps"] <= 0), None),
                lower_90_bps=bootstrap_lower(main), top3=top, without_top3=stats(remainder),
                top3_stats=stats([p for p in main if p["symbol"] in top]),
                leg_delay=stats([p for p in eligible if p["variant"] == "leg_delay" and p["amount"] == 300]),
                shadow_bias={"n": len(biases), "mean": mean(biases) if biases else None,
                             "p10": biases[int((len(biases)-1)*.1)] if biases else None,
                             "p50": biases[len(biases)//2] if biases else None,
                             "p90": biases[int((len(biases)-1)*.9)] if biases else None},
                groups=grouped, stale_exits=stats([p for p in closed if p.get("exit_reason") == "stale_exit"]),
                funding_unknown=sum(not p.get("funding_known", False) for p in positions),
                funding_observations=len(positions),
                funding_unknown_ratio=(sum(not p.get("funding_known", False) for p in positions) / len(positions)
                                       if positions else None),
                open_positions=sum(p.get("closed_at") is None for p in positions))


def gates(summary, observed_seconds, resources=None):
    r = resources or {}
    results = {
        "observation_7_days": observed_seconds >= 7 * 86400,
        "events_300_coins_5_pairs_3": summary["measured_300_open_events"] >= 300 and summary["coins"] >= 5 and summary["pairs"] >= 3,
        "day_bootstrap_90_lower_positive": summary["lower_90_bps"] is not None and summary["lower_90_bps"] > 0,
        "without_top3_positive": summary["without_top3"]["mean_bps"] is not None and summary["without_top3"]["mean_bps"] > 0,
        "leg_delay_positive": summary["leg_delay"]["mean_bps"] is not None and summary["leg_delay"]["mean_bps"] > 0,
        "resources": None,
    }
    required = ("taoli_stalls_not_worse", "no_429_increase", "max_rss_mb")
    if all(k in r and r[k] is not None for k in required):
        results["resources"] = r["taoli_stalls_not_worse"] is True and r["no_429_increase"] is True and r["max_rss_mb"] < 120
    return results
