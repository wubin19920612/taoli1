"""python3 -m fill_sim {run,report,smoke}; run from tools or set PYTHONPATH."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import sys
import time
from .core import EXCHANGES, day, dec
from .engine import Engine
from .network import Limiter, PublicClient, ShadowAPI
from .reporting import summarize, gates
from .storage import Store, atomic_json, run_lock


def default_fees_json():
    return Path(__file__).with_name("default-fees.json").read_text(encoding="utf-8")


def unconfigured_fees(fees):
    return [ex+":"+kind for ex in EXCHANGES for kind in ("future", "spot")
            if fees.get(ex, {}).get(kind) is None]


def load_env(path):
    """Explicit fill-sim env only, never repository .env; no shell interpolation."""
    if not path:
        return
    allowed = {"TAOLI1_API_BASE", "TAOLI1_API_TOKEN", "FILL_SIM_DB", "FILL_SIM_FEES_JSON",
               "FILL_SIM_EXCHANGE_RATE", "FILL_SIM_GLOBAL_RATE", "FILL_SIM_OUT", "FILL_SIM_GUARD_FILE"}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key.strip() not in allowed:
            raise ValueError("unsupported_env_field")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def rss_mb():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (x, ctypes.c_size_t) for x in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = wintypes.HANDLE
        memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        memory_info.restype = wintypes.BOOL
        if not memory_info(process(), ctypes.byref(c), c.cb):
            return None
        return c.WorkingSetSize / 1024 ** 2
    try:
        fields = Path("/proc/self/statm").read_text().split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE") / 1024 ** 2
    except (OSError, ValueError):
        return None


def guard(path, now):
    """External read-only metrics contract. Missing/stale data stops observation."""
    if sys.platform.startswith("linux"):
        mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        if int(mem["MemAvailable"].split()[0]) < 200 * 1024:
            return "low_memory", {}
    if not path:
        return "guard_file_required", {}
    try:
        metrics = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(metrics, dict):
            raise ValueError("invalid_guard")
        if type(metrics["timestamp"]) not in (int, float):
            raise ValueError("invalid_guard")
        dec(metrics["timestamp"])
        if type(metrics["taoli_stalls_last_hour"]) is not int:
            raise ValueError("invalid_guard")
        for field in ("taoli_stalls_not_worse", "no_429_increase"):
            if field in metrics and type(metrics[field]) is not bool:
                raise ValueError("invalid_guard")
        age = now - metrics["timestamp"]
        if age < -5 or age > 90:
            return "guard_metrics_stale", metrics
        if metrics["taoli_stalls_last_hour"] > 4:
            return "taoli_stalls", metrics
        if metrics["taoli_stalls_last_hour"] < 0:
            raise ValueError("invalid_guard")
    except (OSError, ValueError, KeyError, TypeError):
        return "guard_metrics_unavailable", {}
    return None, metrics


def window(store, since, until):
    # Drop stored books/payloads before aggregation to stay inside the RSS budget.
    events = [{k:e[k] for k in ("id","status","filter_passed","t1") if k in e}
              for e in store.rows("events", since, until)]
    fills = [{k:f[k] for k in ("event_id","variant","amount","status","symbol","pair","t1") if k in f}
             for f in store.rows("fills", since, until)]
    # Daily income is attributed to close day, including older opening cohorts.
    positions = []
    for p in store.rows("positions", since, until, by_close=True):
        ts = p.get("closed_at") if p.get("closed_at") is not None else p["opened_at"]
        if since <= ts < until:
            shadow = store.meta("shadow:" + p["event_id"])
            if shadow is not None:
                p["shadow_net_bps"] = shadow
            positions.append({k:v for k,v in p.items() if k not in
                              ("fill","markets","specs","last_books","last_executable_exit","funding","close_buy","close_sell")})
    result = summarize(events, fills, positions)
    result["unconfigured_fees"] = store.meta("unconfigured_fees", unconfigured_fees(json.loads(default_fees_json())))
    return result


def publish_daily(store, out, now):
    from datetime import datetime, timezone
    current = datetime.fromtimestamp(now, timezone.utc)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    for date_start in (start - 86400, start):
        summary = window(store, date_start, date_start + 86400)
        summary.update(date=day(date_start), generated_at=now, timezone="UTC")
        atomic_json(Path(out) / ("daily-" + day(date_start) + ".json"), summary)
    cutoff = day(now - 30 * 86400)
    for path in Path(out).glob("daily-????-??-??.json"):
        if path.stem[-10:] < cutoff:
            path.unlink()


def run(args):
    fees = json.loads(args.fees_json)
    if not isinstance(fees, dict):
        raise ValueError("fees_must_be_object")
    for ex, values in fees.items():
        if ex not in EXCHANGES or not isinstance(values, dict):
            raise ValueError("invalid_fees")
        for kind, value in values.items():
            if kind not in ("spot", "future") or (value is not None and not 0 <= dec(value) < 1):
                raise ValueError("invalid_fees")
    store = Store(args.db)
    with store.transaction():
        store.set_meta("unconfigured_fees", unconfigured_fees(fees))
    limiter = Limiter(args.exchange_rate, args.global_rate, state=store.meta("limiter"))
    api = ShadowAPI(args.api_base, os.environ.get("TAOLI1_API_TOKEN", ""))
    api.failures = store.meta("api_failures", 0)
    api.next_poll = max(time.time(), store.meta("api_next_poll", 0))
    api.streak = store.meta("api_streak", 0)
    engine = Engine(store, PublicClient(limiter), fees)
    api_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shadow-api")
    future, stopped, guard_stop = None, False, None
    started = time.time()
    next_status = next_daily = 0
    observation = store.meta("observation_seconds", 0)
    recorded_observation = observation
    metrics = {}
    max_rss = store.meta("max_rss_mb", 0)
    previous = started

    def stop(signum, frame):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopped:
            now = time.time()
            if now >= next_status:
                guard_stop, metrics = guard(args.guard_file, now)
                rss = rss_mb()
                if rss is not None:
                    max_rss = max(max_rss, rss)
                if rss is not None and rss >= 120:
                    guard_stop = "rss_limit"
                summary = window(store, now - now % 86400, now + .001)
                state = dict(timestamp=now, uptime_seconds=now-started, observation_seconds=observation,
                             rss_mb=rss, max_rss_mb=max_rss, exchanges=limiter.snapshot(), api_failures=api.failures,
                             positions=len(engine.positions), active_events=len(engine.active_events()),
                             today_events=summary["events"], today_open_events=summary["measured_300_open_events"],
                             funding_unknown=summary["funding_unknown"],
                             funding_observations=summary["funding_observations"],
                             funding_unknown_ratio=summary["funding_unknown_ratio"],
                             unconfigured_fees=summary["unconfigured_fees"],
                             open_funding_unknown_ratio=(sum(not p.get("funding_known", False) for p in engine.positions.values())
                                                         / len(engine.positions) if engine.positions else None),
                             stop_reason=guard_stop, resource_evidence=metrics)
                atomic_json(Path(args.out) / "status.json", state)
                with store.transaction():
                    store.runtime(now, observation-recorded_observation, rss, metrics,
                                  sum(s["rate_limits"] for s in limiter.stats.values()))
                    recorded_observation = observation
                    for key, value in (("limiter", limiter.stats), ("api_failures", api.failures),
                                       ("api_next_poll", api.next_poll), ("api_streak", api.streak),
                                       ("observation_seconds", observation), ("max_rss_mb", max_rss)):
                        store.set_meta(key, value)
                next_status = now + 30
                if guard_stop:
                    break
            engine.tick()
            if future is not None and future.done():
                payloads = future.result()
                future = None
                if payloads is not None:
                    # API already supplies newest first; no queue of deferred opens.
                    for payload in payloads:
                        engine.ingest(payload)
            if future is None and now >= api.next_poll:
                future = api_pool.submit(api.poll)
            if now >= next_daily:
                store.rotate(now)
                publish_daily(store, args.out, now)
                next_daily = now + 300
            if api.streak == 0:
                observation += max(0, min(now - previous, 1))
            previous = now
            time.sleep(.05)
    finally:
        api_pool.shutdown(wait=True)
        engine.close()
        with store.transaction():
            store.runtime(time.time(), observation-recorded_observation, rss_mb(), metrics,
                          sum(s["rate_limits"] for s in limiter.stats.values()))
            store.set_meta("limiter", limiter.stats)
            store.set_meta("api_failures", api.failures)
            store.set_meta("api_next_poll", api.next_poll)
            store.set_meta("api_streak", api.streak)
            store.set_meta("observation_seconds", observation)
        publish_daily(store, args.out, time.time())
        store.close()
    if guard_stop:
        print("STOP " + guard_stop)
        return 78
    return 0


def main(argv=None):
    # Parse explicit env path before computing defaults; report/smoke never read tokens.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file")
    preliminary, _ = pre.parse_known_args(argv)
    load_env(preliminary.env_file)
    parser = argparse.ArgumentParser(parents=[pre])
    parser.add_argument("command", choices=("run", "report", "smoke"))
    parser.add_argument("--db", default=os.environ.get("FILL_SIM_DB", "fill_sim.db"))
    parser.add_argument("--out", default=os.environ.get("FILL_SIM_OUT", "out"))
    parser.add_argument("--api-base", default=os.environ.get("TAOLI1_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--fees-json", default=os.environ.get("FILL_SIM_FEES_JSON", default_fees_json()))
    parser.add_argument("--exchange-rate", type=float, default=float(os.environ.get("FILL_SIM_EXCHANGE_RATE", 2)))
    parser.add_argument("--global-rate", type=float, default=float(os.environ.get("FILL_SIM_GLOBAL_RATE", 8)))
    parser.add_argument("--guard-file", default=os.environ.get("FILL_SIM_GUARD_FILE"))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--fixtures")
    parser.add_argument("--include-spot", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "smoke":
        from .smoke import run as smoke
        return int(bool(smoke(args.fixtures, args.include_spot)))
    if args.command == "run":
        with run_lock(args.db):
            return run(args)
    if not 1 <= args.days <= 30:
        parser.error("days must be between 1 and 30")
    store = Store(args.db)
    now = time.time()
    summary = window(store, now-args.days*86400, now+.001)
    observation, resources = store.runtime_window(now-args.days*86400, now+.001)
    result = gates(summary, observation, resources)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for key, passed in result.items():
        print(("PASS" if passed is True else "FAIL" if passed is False else "UNKNOWN") + " " + key)
    store.close()
    return 0 if all(v is True for v in result.values()) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        # No exception payloads: config/transport errors may contain secrets.
        print("ERROR " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(2)
