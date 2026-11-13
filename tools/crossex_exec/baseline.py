"""Public mid-price history and direction-independent admission state.

No account reads, order calls or network transport. Sampling uses the existing
public queue; SQLite retains one observation per unordered pair / 30s bucket.
"""
from itertools import combinations, islice
from statistics import median
import threading
import time

from tools.fill_sim.core import dec
from tools.fill_sim.exchanges import check_response
from .baseline_cache import WindowCache, arithmetic_context


BATCH_URLS = {
    'binance': 'https://fapi.binance.com/fapi/v1/ticker/bookTicker',
    'bybit': 'https://api.bybit.com/v5/market/tickers?category=linear',
    'okx': 'https://www.okx.com/api/v5/market/tickers?instType=SWAP',
    'gate': 'https://api.gateio.ws/api/v4/futures/usdt/tickers',
    'bitget': 'https://api.bitget.com/api/v3/market/tickers?category=USDT-FUTURES',
    # The current official documentation recommends V3 for new integrations.
    'aster': 'https://fapi.asterdex.com/fapi/v3/ticker/bookTicker',
}
SAMPLE_SECONDS = 30
WINDOW_SECONDS = 21600
RETENTION_SECONDS = 86400
FRESH_SECONDS = 90
RECHECK_SECONDS = 600
COOLDOWN_REASONS = frozenset((
    'stop_loss', 'loss_limit', 'adverse_spread', 'max_hold', 'max_age',
    'pre_funding', 'funding_exit', 'funding_unknown', 'stale', 'stale_exit',
    'one_leg', 'unhedged_position', 'repair_failed', 'unknown_order',
))


def supported_base(base):
    """Keep the existing baseline identity boundary, including ASCII-only keys."""
    return (isinstance(base, str) and bool(base) and base.isascii()
            and base.isalnum() and base == base.upper())


def pair_key(base, a, b):
    if not supported_base(base) or a == b or a not in BATCH_URLS or b not in BATCH_URLS:
        raise ValueError('baseline_identity_invalid')
    return '|'.join((base, *sorted((a, b))))


def parse_tickers(ex, data, bases, now):
    """Only exact whitelist USDT perpetual symbols; ambiguity invalidates a coin."""
    check_response(ex, data)
    if ex == 'bybit':
        if data['result']['category'] != 'linear':
            raise ValueError('baseline_category_invalid')
        rows = data['result']['list']
    else:
        rows = data if ex in ('binance', 'aster', 'gate') else data['data']
    if not isinstance(rows, list):
        raise ValueError('baseline_batch_required')
    server = data.get('time' if ex == 'bybit' else 'requestTime') if isinstance(data, dict) else None
    if server is not None and not -5 <= dec(now)-dec(server)/1000 <= FRESH_SECONDS:
        raise ValueError('baseline_response_stale')
    names = {(base+'_USDT' if ex == 'gate' else base+'-USDT-SWAP' if ex == 'okx' else base+'USDT'):base
             for base in bases}
    symbol = 'contract' if ex == 'gate' else 'instId' if ex == 'okx' else 'symbol'
    bid, ask = (('highest_bid', 'lowest_ask') if ex == 'gate' else ('bidPx', 'askPx') if ex == 'okx'
                else ('bidPrice', 'askPrice') if ex in ('binance', 'aster') else ('bid1Price', 'ask1Price'))
    result, seen = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        base = names.get(row.get(symbol))
        if base is None:
            continue
        if base in seen:
            result.pop(base, None)
            continue
        seen.add(base)
        try:
            if ex == 'okx' and row['instType'] != 'SWAP':
                continue
            if ex == 'bitget' and row['category'] != 'USDT-FUTURES':
                continue
            at = row.get('ts' if ex == 'okx' else 'time') if ex in ('okx', 'binance', 'aster') else None
            if at is not None and not -5 <= dec(now)-dec(at)/1000 <= FRESH_SECONDS:
                continue
            low, high = dec(row[bid]), dec(row[ask])
            if 0 < low <= high:
                result[base] = (low+high)/2
        except (ValueError, KeyError, TypeError):
            continue
    return result


def cooldown_for(pair):
    """Return an idempotent expiry only after loss accounting is known and flat."""
    if (pair.get('state') != 'CLOSED' or pair.get('exit_reason') not in COOLDOWN_REASONS
            or pair.get('accounting_pending') is not False or pair.get('funding_known') is not True
            or pair.get('net_usd') is None or dec(pair['net_usd']) >= 0):
        return None
    legs = pair['instruments']
    key = pair_key(pair['base'], *(i['market']['exchange'] for i in legs))
    return key, float(dec(pair['closed_at'])+RETENTION_SECONDS)


class Baseline:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self._cache = WindowCache()
        # Only sampling rounds serialize here. Admission/status/account reads
        # never wait for this lock, nor does Engine.close().
        self._record_lock = threading.Lock()

    def record(self, mids, bases, at=None, *, active=lambda: True, quotes_valid=lambda: True):
        with self._record_lock:
            self._record(mids, bases, at, active, quotes_valid)

    def _record(self, mids, bases, at, active, quotes_valid):
        now = self.clock() if at is None else at
        # Public catalogs can contain identities unsupported by this strategy.
        # Isolate them before quotes, samples AND empty-round status writes;
        # never normalize/rename them or abort valid pairs in the same round.
        bases = tuple(base for base in bases if supported_base(base))
        self._cache.retain(bases)
        clean = {}
        for ex in BATCH_URLS:
            for base in bases:
                try:
                    value = dec(mids[ex][base])
                    if value > 0:
                        clean.setdefault(ex, {})[base] = str(value)
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    continue
        mids = clean
        def samples():
            for base in bases:
                for a, b in combinations(sorted(BATCH_URLS), 2):
                    if base not in mids.get(a, {}) or base not in mids.get(b, {}):
                        continue
                    ma, mb = dec(mids[a][base]), dec(mids[b][base])
                    yield (pair_key(base,a,b), int(now//SAMPLE_SECONDS), now, str((mb/ma-1)*10000))
        # Revoke references before exposing any new samples or partial results.
        # Only a completed round can publish the captured reference set again.
        with self.store.transaction():
            if not active():
                return
            self.store.set_meta('baseline_quotes', dict(at=now, mids={}))
        versioned = hasattr(self.store, 'baseline_window')
        if versioned:
            if not self.store.add_baseline_samples(samples(), now, active):
                return
        else:  # The existing in-memory simulator implements the original Store protocol.
            with self.store.lock:
                if not active():
                    return
                self.store.add_baseline_samples(samples(), now)
        keys = (pair_key(base,a,b) for base in bases for a,b in combinations(sorted(BATCH_URLS), 2))
        while True:
            batch = list(islice(keys, 64))
            if not batch:
                break
            computed = []
            for key in batch:
                result = self._calculate(key, now, active=active)
                if result is None:
                    return
                computed.append((key, *result))
            retry = []
            with self.store.transaction():
                if not active():
                    return
                for key, stats, revision, _ in computed:
                    if revision is not None and self.store.baseline_revision(key) != revision:
                        retry.append(key)
                        continue
                    previous = self.store.baseline_state(key)
                    # An on-demand check may have already published a newer
                    # window. Do not roll its latch back with this slow round.
                    if previous.get('at', now) > now and self.clock() >= previous['at']:
                        continue
                    self.store.save_baseline_state(self._state(key, stats, previous, now))
            for key in retry:
                base, a, b = key.split('|')
                self.status(base, a, b, now, active=active, preserve_newer=True)
        if versioned:
            self.store.prune_baseline_states(bases, active)
        else:
            with self.store.lock:
                if not active():
                    return
                self.store.prune_baseline_states(bases)
        with self.store.transaction():
            if not active():
                return
            self.store.set_meta('baseline_quotes', dict(at=now, mids=mids if quotes_valid() else {}))

    def _calculate(self, key, now, reverse=False, active=lambda: True):
        since = now-WINDOW_SECONDS
        entry = self._cache.get(key)
        with self.store.lock:
            if not active():
                return None
            if hasattr(self.store, 'baseline_window'):
                revision, incremental, added = self.store.baseline_window(key, since, now, entry)
            else:
                revision, incremental = None, False
                added = self.store.baseline_samples(key, since, now)
        unchanged = (incremental and not added and entry.context == arithmetic_context()
                     and (entry.stats['first_at'] is None or entry.stats['first_at'] >= since))
        rows = None
        if unchanged:
            stats = dict(entry.stats)
        else:
            rows = ([r for r in entry.rows() if since <= r[0] <= now]+added) if incremental else added
            values = [dec(value) for _, value in rows]
            center = median(values) if values else None
            mad = median([abs(v-center) for v in values]) if values else None
            stats = dict(baseline_bps=str(center) if center is not None else None,
                         mad_bps=str(mad) if mad is not None else None, count=len(rows),
                         first_at=rows[0][0] if rows else None, latest_at=rows[-1][0] if rows else None)
            if revision is not None:
                self._cache.put(key, since, now, revision, rows, stats)
        reversed_stats = None
        if reverse and stats['count']:
            if rows is None:
                rows = entry.rows()
            values = [(1/(1+dec(v)/10000)-1)*10000 for _, v in rows]
            center = median(values)
            reversed_stats = dict(baseline_bps=str(center), mad_bps=str(median(abs(v-center) for v in values)))
        return stats, revision, reversed_stats

    @staticmethod
    def _state(key, stats, previous, now):
        latest = stats['latest_at']
        ready = bool(stats['count'] >= 300 and latest-stats['first_at'] >= 10800
                     and 0 <= now-latest <= FRESH_SECONDS)
        checked = previous.get('checked_at')
        blocked = previous.get('blocked', False)
        if ready and (checked is None or now < checked or now-checked >= RECHECK_SECONDS):
            blocked = abs(dec(stats['baseline_bps'])) >= 80 or dec(stats['mad_bps']) >= 40
            checked = now
        return dict(stats, key=key, ready=ready, blocked=blocked, checked_at=checked, at=now)

    def latest(self, base):
        return self.store.latest_baseline(base)

    def status(self, base, buy, sell, now=None, *, active=lambda: True, preserve_newer=False):
        now = self.clock() if now is None else now
        key = pair_key(base, buy, sell)
        for _ in range(3):
            result = self._calculate(key, now, buy > sell, active)
            if result is None:
                return None
            stats, revision, reversed_stats = result
            with self.store.transaction() if revision is not None else self.store.lock:
                if not active():
                    return None
                if revision is not None and self.store.baseline_revision(key) != revision:
                    continue
                previous = self.store.baseline_state(key)
                if preserve_newer and previous.get('at', now) > now and self.clock() >= previous['at']:
                    return previous
                state = self._state(key, stats, previous, now)
                self.store.save_baseline_state(state)
            return dict(state, **reversed_stats) if reversed_stats else state
        raise ValueError('baseline_changed')

    def require(self, base, buy, sell):
        now = self.clock()
        key = pair_key(base, buy, sell)
        if self.store.meta('pair_cooldowns', {}).get(key, 0) > now:
            raise ValueError('pair_cooldown')
        state = self.status(base, buy, sell, now)
        if not state['ready']:
            raise ValueError('baseline_warming')
        if state['blocked']:
            raise ValueError('structural_spread')
        return state
