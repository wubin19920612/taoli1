"""Frozen f34576a baseline oracle; only synthetic DBs and local test inputs."""
from itertools import combinations
from statistics import median
import time
from tools.fill_sim.core import dec
from tools.crossex_exec.baseline import (BATCH_URLS, SAMPLE_SECONDS, WINDOW_SECONDS,
    FRESH_SECONDS, RECHECK_SECONDS, supported_base, pair_key)


class LegacyBaseline:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def record(self, mids, bases, at=None):
        now = self.clock() if at is None else at
        # Public catalogs can contain identities unsupported by this strategy.
        # Isolate them before quotes, samples AND empty-round status writes;
        # never normalize/rename them or abort valid pairs in the same round.
        bases = tuple(base for base in bases if supported_base(base))
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
        with self.store.transaction():
            # Replace the entire round, including failed/empty venues. Never
            # merge old quotes into evidence of three simultaneously observed venues.
            self.store.set_meta('baseline_quotes', dict(at=now, mids=mids))
            self.store.prune_baseline_states(bases)
            for base in bases:
                for a, b in combinations(sorted(BATCH_URLS), 2):
                    self.status(base, a, b, now)

    def latest(self, base):
        return self.store.latest_baseline(base)

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
            previous = self.store.baseline_state(key)
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
            self.store.save_baseline_state(state)
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
