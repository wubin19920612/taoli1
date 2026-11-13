"""Round 27 offline benchmark. Separate seed/before/after processes, same DB.

python -m tools.crossex_exec.benchmark_baseline --mode seed --directory <empty-dir>
python -m tools.crossex_exec.benchmark_baseline --mode before --directory <dir>
python -m tools.crossex_exec.benchmark_baseline --mode after --directory <dir>
"""
import argparse
import hashlib
import importlib.util
from itertools import combinations
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import threading
import time

from .baseline import Baseline, BATCH_URLS
from .benchmark_sampling import memory
from .storage import Store


NOW = 1794556800.0


class TimedLock:
    """Bounded counters, only outermost acquisitions; no per-acquisition list."""
    def __init__(self, lock):
        self.lock = lock
        self.local = threading.local()
        self.reset()

    def reset(self):
        self.max_hold = self.max_wait = 0
        self.acquisitions = 0

    def __enter__(self):
        started = time.perf_counter()
        self.lock.acquire()
        entered = time.perf_counter()
        depth = getattr(self.local, 'depth', 0)
        if not depth:
            self.local.started = entered
            self.max_wait = max(self.max_wait, entered-started)
            self.acquisitions += 1
        self.local.depth = depth+1
        return self

    def __exit__(self, *args):
        self.local.depth -= 1
        if not self.local.depth:
            self.max_hold = max(self.max_hold, time.perf_counter()-self.local.started)
        self.lock.release()


def before_types(directory, ref):
    """Load the actual old files from a local commit, with isolated module names."""
    result = []
    for name in ('public_store', 'baseline', 'storage'):
        source = subprocess.check_output(['git', 'show', ref+':tools/crossex_exec/'+name+'.py']).decode('utf-8')
        if name == 'storage':
            source = source.replace('from .public_store import', 'from ._round27_before_public_store import')
        path = directory/('before_'+name+'.py')
        path.write_text(source, encoding='utf-8')
        identity = 'tools.crossex_exec._round27_before_'+name
        spec = importlib.util.spec_from_file_location(identity, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[identity] = module
        spec.loader.exec_module(module)
        result.append(module)
    return result[1].Baseline, result[2].Store


def digest(store):
    value = {key: store.meta(key) for key in ('baseline_status', 'baseline_quotes')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('seed', 'before', 'after'), required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--bases', type=int, default=467)
    parser.add_argument('--samples', type=int, default=880)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--before-ref', default='f34576a')
    args = parser.parse_args()
    if not 1 <= args.bases <= 1000 or not 720 <= args.samples <= 2880 or not 1 <= args.rounds <= 10:
        parser.error('benchmark_range')
    args.directory.mkdir(parents=True, exist_ok=True)
    old_baseline, old_store = before_types(args.directory, args.before_ref)
    seed = args.directory/'seed.db'
    bases = tuple('COIN'+str(n) for n in range(args.bases))
    venues = sorted(BATCH_URLS)
    if args.mode == 'seed':
        if seed.exists():
            parser.error('seed_already_exists')
        rng = random.Random(27)
        with old_store(seed) as store:
            with store.transaction():
                for index, base in enumerate(bases):
                    for a, b in combinations(venues, 2):
                        key = '|'.join((base, a, b))
                        store.db.executemany('INSERT INTO baseline_samples VALUES (?,?,?,?)',
                            ((key, int((NOW-n*30)//30), NOW-n*30,
                              str(rng.randrange(-100, 101))+'.'+str(rng.getrandbits(64)).zfill(20))
                             for n in range(args.samples)))
                    if index % 50 == 0:
                        print(json.dumps(dict(seeded_bases=index+1)), flush=True)
            store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        (args.directory/'seed.json').write_text(json.dumps(dict(bases=args.bases, samples=args.samples)), encoding='utf-8')
        print(json.dumps(dict(rows=len(bases)*15*args.samples, bytes=seed.stat().st_size)), flush=True)
        return
    manifest = json.loads((args.directory/'seed.json').read_text())
    if manifest != dict(bases=args.bases, samples=args.samples):
        parser.error('seed_parameters_mismatch')
    path = args.directory/(args.mode+'.db')
    if path.exists():
        parser.error('measurement_already_exists')
    shutil.copyfile(seed, path)
    baseline_type, store_type = (old_baseline, old_store) if args.mode == 'before' else (Baseline, Store)
    result = dict(mode=args.mode, python=sys.version, before_ref=args.before_ref,
                  pairs=len(bases)*15, initial_rows=len(bases)*15*args.samples, rounds=[])
    with store_type(path) as store:
        store.lock = TimedLock(store.lock)
        baseline = baseline_type(store)
        result['initial_memory'] = memory()
        for index in range(args.rounds):
            now = NOW+(index+1)*30
            mids = {ex: {base: str(100+(n+index)/1000) for base in bases} for n, ex in enumerate(venues)}
            store.lock.reset()
            wall, cpu = time.perf_counter(), time.process_time()
            if args.mode == 'before':
                # This is the original Engine.refresh_baselines critical section.
                with store.lock:
                    baseline.record(mids, bases, at=now)
            else:
                baseline.record(mids, bases, at=now)
            row = dict(round=index+1, wall_seconds=time.perf_counter()-wall,
                       cpu_seconds=time.process_time()-cpu, max_lock_seconds=store.lock.max_hold,
                       lock_acquisitions=store.lock.acquisitions, **memory())
            row['state_sha256'] = digest(store)
            if args.mode == 'after':
                row.update(cache_charged_bytes=baseline._cache.bytes,
                           cache_entries=len(baseline._cache.entries))
            result['rounds'].append(row)
            print(json.dumps(row), flush=True)
    (args.directory/(args.mode+'.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
