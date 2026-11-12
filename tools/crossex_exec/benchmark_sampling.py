"""Offline, reproducible sampling CPU/RSS benchmark (no network or credentials).

Run each mode in a separate process. --before-zip accepts the preserved pre-edit
WIP archive's performance-before/{baseline,storage}.py, not arbitrary shell code.
"""
import argparse
import cProfile
import ctypes
import importlib.util
from itertools import combinations
import json
import os
from pathlib import Path
import pstats
import sys
import tempfile
import time
import zipfile

from .baseline import Baseline, BATCH_URLS
from .storage import Store


def memory():
    if os.name == 'nt':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)] + [
                (k,ctypes.c_size_t) for k in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage',
                 'QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
        result=Counters()
        result.cb=ctypes.sizeof(result)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        psapi=ctypes.WinDLL('psapi',use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes=(wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(result),result.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return dict(rss_bytes=result.WorkingSetSize,peak_rss_bytes=result.PeakWorkingSetSize,
                    private_commit_bytes=result.PagefileUsage,peak_private_commit_bytes=result.PeakPagefileUsage)
    import resource
    return dict(peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)


def load_before(archive, directory):
    modules=[]
    with zipfile.ZipFile(archive) as z:
        for name in ('baseline','storage'):
            path=directory/('before_'+name+'.py')
            path.write_bytes(z.read('performance-before/'+name+'.py'))
            spec=importlib.util.spec_from_file_location('tools.crossex_exec.before_'+name,path)
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
    return modules[0].Baseline,modules[1].Store


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-zip',type=Path)
    parser.add_argument('--bases',type=int,default=84)
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--history-hours',type=int,default=24)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.bases<=1000 or not 1<=args.rounds<=20 or not 6<=args.history_hours<=24:
        parser.error('benchmark_range')
    start=time.perf_counter()
    now=1794448800.0
    bases=tuple('COIN'+str(n) for n in range(args.bases))
    exchanges=sorted(BATCH_URLS)
    keys=[f'{base}|{a}|{b}' for base in bases for a,b in combinations(exchanges,2)]
    mids={ex:{base:str(100+n*.001) for base in bases} for n,ex in enumerate(exchanges)}
    with tempfile.TemporaryDirectory(prefix='crossex-sampling-') as tmp:
        directory=Path(tmp)
        baseline_type,store_type=load_before(args.before_zip,directory) if args.before_zip else (Baseline,Store)
        dbpath=directory/'sampling.db'
        with store_type(dbpath) as store:
            history=args.history_hours*120+1
            # Stream one pair at a time. This benchmark must not manufacture a
            # multi-million-row Python list and mislabel that allocation as RSS.
            with store.transaction():
                for key in keys:
                    store.db.executemany('INSERT INTO baseline_samples VALUES (?,?,?,?)',
                        ((key,int((now-n*30)//30),now-n*30,'0') for n in range(history)))
            store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            baseline=baseline_type(store,lambda:now)
            rows=[]
            initial_memory=memory()
            for index in range(args.rounds):
                now+=30
                wall,cpu=time.perf_counter(),time.process_time()
                baseline.record(mids,bases,at=now)
                sample=dict(round=index+1,cpu_seconds=time.process_time()-cpu,
                            wall_seconds=time.perf_counter()-wall,**memory())
                rows.append(sample)
                print(json.dumps(sample),flush=True)
            # Profile one additional identical round separately from timing.
            profiler=cProfile.Profile()
            now+=30
            profiler.runcall(baseline.record,mids,bases,now)
            stats=pstats.Stats(profiler)
            hotspots=[]
            for (filename,line,name),(primitive,calls,total,cumulative,_) in stats.stats.items():
                if name in ('clean','encode','set_meta','status','baseline_samples','record'):
                    hotspots.append(dict(file=Path(filename).name,line=line,name=name,calls=calls,
                                         own_seconds=total,cumulative_seconds=cumulative))
            state=baseline.require(bases[0],exchanges[0],exchanges[1])
            assert state['ready'] and not state['blocked']
            samples=store.db.execute('SELECT count(*) FROM baseline_samples').fetchone()[0]
            oldest=store.db.execute('SELECT min(at) FROM baseline_samples').fetchone()[0]
            assert oldest>=now-86400
            disk=sum(p.stat().st_size for p in directory.glob('sampling.db*'))
            result=dict(mode='before' if args.before_zip else 'after',python=sys.version,
                platform=sys.platform,bases=args.bases,native_contracts=args.bases*len(exchanges),
                venue_pairs=len(keys),history_hours=args.history_hours,sample_rows=samples,
                network=False,credentials=False,initial_memory=initial_memory,rounds=rows,
                average_cpu_seconds=sum(r['cpu_seconds'] for r in rows)/len(rows),
                projected_one_core_percent_at_30s=sum(r['cpu_seconds'] for r in rows)/len(rows)/30*100,
                final_memory=memory(),database_and_wal_bytes=disk,profile=hotspots,
                elapsed_seconds=time.perf_counter()-start)
            args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result),flush=True)


if __name__=='__main__':
    main()
