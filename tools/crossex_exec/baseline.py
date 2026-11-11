"""Public mid-price history and direction-independent admission state.

No account reads, order calls or network transport. Sampling uses the existing
public queue; SQLite retains one observation per unordered pair / 30s bucket.
"""
from itertools import combinations
from statistics import median
import time

from tools.fill_sim.core import dec
from tools.fill_sim.exchanges import check_response


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


def pair_key(base, a, b):
    if (not isinstance(base, str) or not base or not base.isascii() or not base.isalnum()
            or base != base.upper() or a == b or a not in BATCH_URLS or b not in BATCH_URLS):
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

    def record(self, mids, bases, at=None):
        now = self.clock() if at is None else at
        rows = []
        for base in bases:
            for a, b in combinations(sorted(BATCH_URLS), 2):
                if base not in mids.get(a, {}) or base not in mids.get(b, {}):
                    continue
                ma, mb = dec(mids[a][base]), dec(mids[b][base])
                if ma <= 0 or mb <= 0:
                    continue
                rows.append((pair_key(base,a,b), int(now//SAMPLE_SECONDS), now, str((mb/ma-1)*10000)))
        self.store.add_baseline_samples(rows, now)
        # Remove observations of a removed whitelist base from the API counts.
        with self.store.lock:
            states = self.store.meta('baseline_status', {})
            self.store.set_meta('baseline_status', {k:v for k,v in states.items() if k.split('|')[0] in bases})
        for base in bases:
            for a, b in combinations(sorted(BATCH_URLS), 2):
                self.status(base, a, b, now)

    def status(self, base, buy, sell, now=None):
        now = self.clock() if now is None else now
        key = pair_key(base, buy, sell)
        with self.store.lock:
            rows = self.store.baseline_samples(key, now-WINDOW_SECONDS, now)
            values = [dec(value) for _,value in rows]
            center = median(values) if values else None
            mad = median([abs(v-center) for v in values]) if values else None
            latest = rows[-1][0] if rows else None
            ready = bool(len(rows) >= 300 and latest-rows[0][0] >= 10800 and 0 <= now-latest <= FRESH_SECONDS)
            states = self.store.meta('baseline_status', {})
            previous = states.get(key, {})
            checked = previous.get('checked_at')
            blocked = previous.get('blocked', False)
            # Missing history never clears a structural latch. Reevaluate once
            # warm and then every ten minutes, including after restart.
            if ready and (checked is None or now < checked or now-checked >= RECHECK_SECONDS):
                blocked = abs(center) >= 80 or mad >= 40
                checked = now
            state = dict(key=key, baseline_bps=str(center) if center is not None else None,
                         mad_bps=str(mad) if mad is not None else None, count=len(rows),
                         first_at=rows[0][0] if rows else None, latest_at=latest,
                         ready=ready, blocked=blocked, checked_at=checked, at=now)
            states[key] = state
            self.store.set_meta('baseline_status', states)
        if buy > sell and values:
            # Reversing a ratio is not just negating bp; transform observations
            # before the median (including the even-sample case).
            reverse = [(1/(1+v/10000)-1)*10000 for v in values]
            value = median(reverse)
            state = dict(state, baseline_bps=str(value), mad_bps=str(median(abs(v-value) for v in reverse)))
        return state

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
