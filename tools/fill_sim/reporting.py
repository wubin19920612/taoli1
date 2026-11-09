"""UTC daily statistics and day-cluster bootstrap. Unknown evidence cannot pass."""
from collections import Counter, defaultdict
import random
from pathlib import Path
import sqlite3
import tempfile
from .core import AMOUNTS, day


def stats(rows):
    n = wins = total = usd = 0
    for row in rows:
        n += 1
        total += row["net_bps"]
        wins += row["net_bps"] > 0
        usd += row["net_usd"]
    return dict(n=n, mean_bps=total/n if n else None,
                win_rate=wins/n if n else None, usd=usd)


def bootstrap_lower(rows, samples=2000, seed=1905):
    days = defaultdict(lambda: [0, 0])
    for row in rows:
        group = days[day(row["closed_at"])]
        group[0] += row["net_bps"]
        group[1] += 1
    return bootstrap_groups(list(days.values()), samples, seed)


def bootstrap_groups(groups, samples=2000, seed=1905):
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
    """Spool narrow rows to disk; Python retains only output groups and days.

    Exact distinct counts and bias order statistics also stay in SQLite. This
    accepts one-shot streams, including archives, without materializing trades.
    """
    with tempfile.TemporaryDirectory(prefix="fill-report-") as temp:
        db = sqlite3.connect(str(Path(temp) / "aggregate.db"))
        try:
            db.execute("PRAGMA cache_size=-1024")
            db.execute("PRAGMA temp_store=FILE")
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            return _summarize(db, events, fills, positions)
        finally:
            db.close()


def _summarize(db, events, fills, positions):
    event_count = considered = 0
    reasons = Counter()
    for e in events:
        event_count += 1
        considered += bool(e.get("filter_passed"))
        reasons[e["status"]] += 1
    db.execute("CREATE TABLE opened (event_id TEXT, symbol TEXT, pair TEXT)")
    db.executemany("INSERT INTO opened VALUES (?,?,?)", (
        (f["event_id"], f["symbol"], f["pair"]) for f in fills
        if f["variant"] == "measured" and f["amount"] == 300 and f["status"] == "opened"))
    db.execute("""CREATE TABLE p (variant TEXT, amount INTEGER, symbol TEXT, pair TEXT,
               spread_tier TEXT, closed_at REAL, eligible INTEGER, known INTEGER,
               reason TEXT, net_bps REAL, net_usd REAL, shadow REAL, pricing TEXT)""")
    db.executemany("INSERT INTO p VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        (p.get("variant"), p.get("amount"), p.get("symbol"), p.get("pair"), p.get("spread_tier"),
         p.get("closed_at"), bool(p.get("main_eligible")), bool(p.get("funding_known")),
         p.get("exit_reason"), p.get("net_bps"), p.get("net_usd"), p.get("shadow_net_bps"),
         p.get("exit_pricing", "unpriced" if p.get("exit_reason") == "unpriced_exit" else "priced"))
        for p in positions))
    db.execute("CREATE VIEW closed AS SELECT rowid AS seq,* FROM p WHERE closed_at IS NOT NULL AND pricing='priced' AND net_bps IS NOT NULL")
    db.execute("CREATE VIEW eligible AS SELECT * FROM closed WHERE eligible=1")
    db.execute("CREATE VIEW main AS SELECT * FROM eligible WHERE variant='measured' AND amount=300")
    def aggregate(source, condition="1", params=()):
        n, avg, wins, usd = db.execute(
            "SELECT count(*),avg(net_bps),sum(net_bps>0),coalesce(sum(net_usd),0) FROM "
            + source + " WHERE " + condition, params).fetchone()
        return dict(n=n, mean_bps=avg, win_rate=wins/n if n else None, usd=usd)
    top = [r[0] for r in db.execute("SELECT symbol FROM main GROUP BY symbol ORDER BY count(*) DESC,symbol LIMIT 3")]
    marks = ",".join("?" for _ in top) or "NULL"
    variants = {v: {str(n): aggregate("eligible", "variant=? AND amount=?", (v,n))
                   for n in AMOUNTS} for v in ("measured", "plus_1s", "leg_delay")}
    amounts = variants["measured"]
    grouped = {}
    for key in ("pair", "symbol", "spread_tier"):
        grouped[key] = {k: dict(n=n, mean_bps=avg, win_rate=wins/n, usd=usd)
                        for k,n,avg,wins,usd in db.execute(
                            "SELECT " + key + ",count(*),avg(net_bps),sum(net_bps>0),sum(net_usd) "
                            "FROM main GROUP BY " + key + " ORDER BY " + key)}
    days = list(db.execute("SELECT sum(net_bps),count(*) FROM main GROUP BY date(closed_at,'unixepoch') ORDER BY min(seq)"))
    bias_n, bias_mean = db.execute("SELECT count(*),avg(shadow-net_bps) FROM main WHERE shadow IS NOT NULL").fetchone()
    def percentile(offset):
        if not bias_n:
            return None
        return db.execute("SELECT shadow-net_bps FROM main WHERE shadow IS NOT NULL "
                          "ORDER BY shadow-net_bps LIMIT 1 OFFSET ?", (offset,)).fetchone()[0]
    opened, open_events, coins, pairs = db.execute(
        "SELECT count(*),count(DISTINCT event_id),count(DISTINCT symbol),count(DISTINCT pair) FROM opened").fetchone()
    observations, unknown, open_positions = db.execute(
        "SELECT count(*),coalesce(sum(known=0),0),coalesce(sum(closed_at IS NULL),0) FROM p").fetchone()
    return dict(events=event_count, considered_events=considered, skipped_reasons=dict(reasons),
                still_there_rate=opened/considered if considered else None,
                measured_300_open_events=open_events, coins=coins, pairs=pairs,
                amounts=amounts, main=aggregate("main"), variants=variants,
                provisional_price_only={str(n): aggregate("closed", "variant='measured' AND amount=? AND reason NOT IN ('stale_exit','unpriced_exit')", (n,)) for n in AMOUNTS},
                capacity_curve=[dict(amount=n, **amounts[str(n)]) for n in AMOUNTS],
                first_nonpositive_amount=next((n for n in AMOUNTS if amounts[str(n)]["mean_bps"] is not None and amounts[str(n)]["mean_bps"] <= 0), None),
                lower_90_bps=bootstrap_groups(days), top3=top,
                without_top3=aggregate("main", "symbol NOT IN ("+marks+")" if top else "1", top),
                top3_stats=aggregate("main", "symbol IN ("+marks+")", top),
                leg_delay=variants["leg_delay"]["300"],
                shadow_bias=dict(n=bias_n, mean=bias_mean, p10=percentile(int((bias_n-1)*.1)),
                                 p50=percentile(bias_n//2), p90=percentile(int((bias_n-1)*.9))),
                groups=grouped, stale_exits=aggregate("closed", "reason='stale_exit'"),
                funding_unknown=unknown, funding_observations=observations,
                funding_unknown_ratio=unknown/observations if observations else None,
                open_positions=open_positions)


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
