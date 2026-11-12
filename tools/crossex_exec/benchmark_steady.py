"""Offline wall-clock shadow process benchmark; synthetic I/O, real scheduling/math/SQLite.

Includes 5 Hz coordinator, 5s accounts/reference, 30s dynamic public sampling,
1 Hz opportunity polling and concurrent read API projections. No live credentials
or network. History preparation/warmup and a separate profile are not timed as
steady CPU. This is an equivalent local measurement, not production acceptance.
"""
import argparse
import cProfile
from itertools import combinations
import json
import math
import os
from pathlib import Path
import platform
import pstats
import socket
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch

from tools.fill_sim.core import Book, Market, Spec, dec
from tools.fill_sim.network import Limiter, PublicClient, ShadowAPI
from .accounts import FeeSnapshot
from .api_data import JournalView
from .background import PeriodicWorker
from .baseline import BATCH_URLS
from .benchmark_sampling import memory
from .engine import Engine
from .liquidity import VOLUME_URLS
from .management import Management
from .market import MarketData
from .policy import Instrument
from .storage import Store


class OfflineAccounts:
    fee_snapshots={}
    def __init__(self):
        self.cross=self.bitget=self
        self.reads=0
    def snapshot(self):
        self.reads+=1
        time.sleep(.02)
        return dict(positions={},margins=['4','4'],upnl='0',equity='20000',
                    equity_by_venue=dict(crossex='10000',bitget='10000'),notionals={},liabilities=[])
    def position_snapshot(self):
        return dict(positions={},captured_at=time.time())
    def fees(self,instruments):
        self.fee_snapshots={i.market.key:FeeSnapshot('0.0002',time.time()) for i in instruments}
        return {k:v.rate for k,v in self.fee_snapshots.items()}
    def check_instruments(self,instruments):
        pass
    def place(self,*args,**kwargs):
        raise AssertionError('benchmark_must_never_send')


class SyntheticMarket(MarketData):
    def __init__(self,store,bases,extra):
        self.base_count,self.extra,self.started=bases,extra,time.monotonic()
        self.rounds=[]
        super().__init__(store,client=PublicClient(Limiter(),transport=self.payload))
        rows=[]
        for base in ('COIN'+str(n) for n in range(bases+extra)):
            for ex in BATCH_URLS:
                raw=base+'_USDT' if ex=='gate' else base+'-USDT-SWAP' if ex=='okx' else base+'USDT'
                rows.append(Instrument(Market(ex,'future',raw),Spec(base,'USDT','1','.001','.001','1'),
                    raw if ex=='bitget' else ex.upper()+'_FUTURE_'+base+'_USDT','.0001',category='crypto'))
        self.publish_mapping({i.market.key:i for i in rows},{})
    def bootstrap(self,bases=()):
        return self.mapping  # Catalog is a synthetic fixed input, not a private/public request.
    def payload(self,url):
        now=time.time()
        ex=next(k for k,v in VOLUME_URLS.items() if v==url) if url in VOLUME_URLS.values() else next(k for k,v in BATCH_URLS.items() if v==url)
        phase=int((time.monotonic()-self.started)//30)%4
        active=self.base_count+(0,self.extra//2,self.extra,self.extra//2)[phase]
        rows=[]
        for i in self.mapping.values():
            if i.market.exchange!=ex:
                continue
            index=int(i.base[4:])
            price=100+index*.02+math.sin(now/5+index)*.01+list(BATCH_URLS).index(ex)*.0001
            bid,ask=str(price-.001),str(price+.001)
            volume='5000000' if index<active else '1000'
            if ex in ('binance','aster'):
                row=dict(symbol=i.market.raw_symbol,quoteVolume=volume,closeTime=int(now*1000),
                         bidPrice=bid,askPrice=ask,time=int(now*1000))
            elif ex=='bitget':
                row=dict(symbol=i.market.raw_symbol,usdtVolume=volume,bidPr=bid,askPr=ask,ts=str(int(now*1000)))
            elif ex=='bybit':
                row=dict(symbol=i.market.raw_symbol,turnover24h=volume,bid1Price=bid,ask1Price=ask)
            elif ex=='okx':
                row=dict(instId=i.market.raw_symbol,instType='SWAP',volCcy24h=volume,low24h='1',bidPx=bid,askPx=ask,ts=str(int(now*1000)))
            else:
                row=dict(contract=i.market.raw_symbol,volume_24h_quote=volume,highest_bid=bid,lowest_ask=ask)
            rows.append(row)
        if ex in ('binance','aster','gate'):
            return rows
        if ex=='bybit':
            return dict(retCode=0,time=int(now*1000),result=dict(category='linear',list=rows))
        return dict(code='00000' if ex=='bitget' else '0',data=rows)
    def sample_quotes(self,minimum):
        started=time.perf_counter()
        result=super().sample_quotes(minimum)
        self.rounds.append(dict(at=time.time(),bases=len(result[1]),quote_wall_seconds=time.perf_counter()-started))
        return result
    def books(self,instruments):
        return [Book([['100','10']],[['100.001','10']],time.time()) for i in instruments]
    def funding(self,instruments):
        return [dict(rate='0',interval=28800,next=time.time()+20000,captured_at=time.time(),history_ok=True,max_abs_24h='0') for i in instruments]
    def open_interests(self,instruments,books):
        return [dict(market_key=i.market.key,usd='10000000',at=time.time(),unit_verified=True) for i in instruments]


def run(args,directory):
    path=directory/'steady.db'
    with Store(path) as store:
        now=time.time()
        keys=[f'COIN{n}|{a}|{b}' for n in range(args.bases) for a,b in combinations(sorted(BATCH_URLS),2)]
        with store.transaction():
            for key in keys:
                store.db.executemany('INSERT INTO baseline_samples VALUES (?,?,?,?)',
                    ((key,int((now-n*30)//30),now-n*30,str((n%23-11)/100)) for n in range(args.history_hours*120+1)))
        store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        market=SyntheticMarket(store,args.bases,args.dynamic_bases)
        accounts=OfflineAccounts()
        engine=Engine(store,market,accounts,stop_file=directory/'STOP')
        engine.limits.initialize(accounts.snapshot())
        engine.reconcile()
        engine.next_catalog=time.time()+21600
        market.started=time.monotonic()
        engine.start_background(())
        api_reads=[]
        view,management=JournalView(path,{}),Management(path)
        def project():
            view.get('summary',{})
            management.get('universe')
            api_reads.append(time.time())
        reader=PeriodicWorker(project,5,'benchmark-api')
        source_calls=[]
        def source_payload(*args,**kwargs):
            source_calls.append(time.time())
            a=next(i for i in market.mapping.values() if i.base=='COIN0' and i.market.exchange=='binance')
            b=next(i for i in market.mapping.values() if i.base=='COIN0' and i.market.exchange=='bitget')
            return [dict(id='benchmark-'+str(int(time.time()//5)),strategy_version='shadow-v2',variant='conv-half',
                spread_tier='0.5–1%',liquidity='gt50k',fake_flag=False,symbol='COIN0',opened_at=time.time(),
                buy_market_key=a.dump()['market'],sell_market_key=b.dump()['market'])]
        source=ShadowAPI('http://offline.invalid',transport=source_payload)
        warm_until=time.monotonic()+args.warmup
        cpu_start=wall_start=None
        windows=[]
        ticks=0
        initial_memory=memory()
        try:
            while True:
                engine.tick()
                if engine.can_open():
                    for payload in source.poll() or []:
                        engine.ingest(payload)
                ticks+=1
                stamp=time.monotonic()
                if cpu_start is None and stamp>=warm_until:
                    wall_start,last_wall=stamp,stamp
                    cpu_start=last_cpu=time.process_time()
                if cpu_start is not None:
                    if stamp-last_wall>=30:
                        cpu=time.process_time()
                        window=dict(wall_seconds=stamp-last_wall,cpu_seconds=cpu-last_cpu,
                                    one_core_percent=(cpu-last_cpu)/(stamp-last_wall)*100)
                        windows.append(window)
                        print(json.dumps(window),flush=True)
                        last_cpu,last_wall=cpu,stamp
                    if stamp-wall_start>=args.seconds:
                        break
                time.sleep(.2)
            cpu_seconds=time.process_time()-cpu_start
            wall_seconds=time.monotonic()-wall_start
        finally:
            engine.close()
            reader.close()
            for worker in [*engine.workers,reader]:
                worker.thread.join(15)
                if worker.thread.is_alive():
                    raise RuntimeError('benchmark_worker_stalled')
        observed=memory()
        profiler=cProfile.Profile()
        def profile_round():
            mids,bases=market.sample_quotes(200000)
            engine.baseline.record(mids,bases,at=time.time())
        profiler.runcall(profile_round)
        hotspots=[]
        for (filename,line,name),(primitive,calls,total,cumulative,_) in pstats.Stats(profiler).stats.items():
            hotspots.append(dict(file=Path(filename).name,line=line,name=name,calls=calls,own_seconds=total,cumulative_seconds=cumulative))
        market.close()
        failures=[dict(r) for r in store.db.execute("SELECT kind,value FROM events WHERE kind IN ('BACKGROUND_FAILED','ACCOUNT_REFRESH_FAILED')")]
        result=dict(python=sys.version,sqlite=sqlite3.sqlite_version,platform=platform.platform(),
            bases=args.bases,dynamic_bases=args.dynamic_bases,native_contracts=len(market.mapping),
            history_hours=args.history_hours,sample_rows=store.db.execute('SELECT count(*) FROM baseline_samples').fetchone()[0],
            warmup_seconds=args.warmup,wall_seconds=wall_seconds,cpu_seconds=cpu_seconds,
            one_core_percent=cpu_seconds/wall_seconds*100,windows=windows,sampling=market.rounds,
            coordinator_ticks=ticks,account_reads=accounts.reads,api_reads=len(api_reads),source_polls=len(source_calls),
            initial_memory=initial_memory,steady_memory=observed,background_failures=failures,
            network=False,credentials=False,live=False,profile=sorted(hotspots,key=lambda r:r['cumulative_seconds'],reverse=True)[:20])
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bases',type=int,default=187)
    parser.add_argument('--dynamic-bases',type=int,default=40)
    parser.add_argument('--history-hours',type=int,default=24)
    parser.add_argument('--warmup',type=int,default=30)
    parser.add_argument('--seconds',type=int,default=180)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not (1<=args.bases<=500 and 0<=args.dynamic_bases<=100 and 6<=args.history_hours<=24
            and 0<=args.warmup<=180 and 30<=args.seconds<=1800):
        parser.error('benchmark_range')
    with tempfile.TemporaryDirectory(prefix='crossex-steady-') as tmp, patch.object(
            socket.socket,'connect',side_effect=AssertionError('external_socket_forbidden')), patch.object(
            socket,'getaddrinfo',side_effect=AssertionError('external_dns_forbidden')):
        result=run(args,Path(tmp))
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('one_core_percent','cpu_seconds','wall_seconds','steady_memory','background_failures')}),flush=True)
    return 0 if result['one_core_percent']<=15 and not result['background_failures'] else 1


if __name__=='__main__':
    sys.exit(main())
