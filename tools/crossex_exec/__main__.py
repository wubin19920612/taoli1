"""python -m tools.crossex_exec run --env-file /private/path (shadow by default)."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time
from .security import Credentials, REDACTOR, load_env
from .storage import Store
from .transport import Transport
from .adapters import CrossEx, Bitget
from .accounts import Accounts
from .market import MarketData
from .engine import Engine
from .reporting import report
from tools.fill_sim.network import ShadowAPI


def emit(kind,**fields):
    print(json.dumps(REDACTOR.clean(dict(kind=kind,at=time.time(),**fields)),allow_nan=False),flush=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description='CrossEx/Bitget execution; no keys in CLI or process environment')
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run')
    run.add_argument('--env-file',required=True)
    reporting=sub.add_parser('report')
    reporting.add_argument('--db',default='var/crossex-exec.db')
    args=parser.parse_args(argv)
    if args.command=='report':
        if not Path(args.db).is_file():
            parser.error('database_not_found')
        with Store(args.db) as store:
            print(json.dumps(report(store),allow_nan=False,indent=2))
        return 0
    # A local development machine cannot satisfy POSIX ownership/mode guarantees.
    config=load_env(args.env_file)
    if config.get('CROSSEX_LIVE_ENABLED','0') not in ('0','1'):
        raise ValueError('invalid_live_switch')
    live=config.get('CROSSEX_LIVE_ENABLED','0')=='1'
    risk_confirmed=config.get('BITGET_RISK_RATE_CONFIRMED','0')=='1'
    if live and not risk_confirmed:
        raise ValueError('bitget_risk_convention_unconfirmed')
    path=Path(config.get('CROSSEX_DB','var/crossex-exec.db'))
    path.parent.mkdir(parents=True,exist_ok=True)
    # flock held for full process lifetime prevents two services sharing a journal.
    import fcntl
    with open(str(path)+'.lock','a',encoding='utf-8') as process_lock:
        fcntl.flock(process_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with Store(path) as store:
            store.assert_startable()
            cross=CrossEx(Transport('gate',Credentials(config.get('GATE_API_KEY',''),config.get('GATE_API_SECRET','')),live=live),
                          config.get('CROSSEX_POSITION_MODE','SINGLE'))
            bitget=Bitget(Transport('bitget',Credentials(config.get('BITGET_API_KEY',''),config.get('BITGET_API_SECRET',''),
                                                       config.get('BITGET_API_PASSPHRASE','')),live=live),
                          config.get('BITGET_POSITION_MODE','one_way_mode'))
            market=MarketData(store)
            accounts=Accounts(cross,bitget,risk_confirmed=risk_confirmed)
            engine=Engine(store,market,accounts,live=live,stop_file=config.get('CROSSEX_STOP_FILE','var/STOP'),
                          alert_sink=lambda reason:emit('alert',reason=reason))
            shutdown=False
            def stop(signum,frame):
                nonlocal shutdown
                shutdown=True
                engine.risk='STOP'
            signal.signal(signal.SIGTERM,stop)
            signal.signal(signal.SIGINT,stop)
            source=ShadowAPI(config.get('TAOLI_BASE_URL','http://127.0.0.1:8000'),config.get('TAOLI_API_TOKEN',''))
            try:
                engine.startup(tuple(filter(None,config.get('CROSSEX_BASES','').split(','))))
                last_status=last_refresh=0
                while not shutdown and store.meta('state')!='HALTED':
                    try:
                        engine.tick()
                        # Hourly fee refresh; catalog rebuild only when flat (6h).
                        now=time.time()
                        if now-last_refresh>=3600:
                            accounts.fees(market.mapping.values())
                            store.set_meta('actual_fees',dict(at=now,rates=accounts.fees_by_market))
                            last_refresh=now
                        if now-store.meta('mapping')['at']>=21600 and not store.pairs(active=True):
                            market.bootstrap(tuple(filter(None,config.get('CROSSEX_BASES','').split(','))))
                        rows=source.poll()
                        if rows:
                            # Each accepted pair can take 10s to confirm; service exits
                            # this batch after one fresh trigger to revisit risk/positions.
                            for row in rows:
                                if not store.db.execute('SELECT 1 FROM triggers WHERE id=?',(row.get('id'),)).fetchone():
                                    engine.ingest(row)
                                    break
                        if now-last_status>=300:
                            emit('status',live=live,state=store.meta('state','RUNNING'),risk=engine.risk,
                                 stop_reason=store.meta('stop_reason'),active_pairs=len(store.pairs(active=True)))
                            last_status=now
                    except Exception:
                        engine.alert('cycle_failed_closed')
                        emit('alert',reason='cycle_failed_closed')
                    time.sleep(.2)
                if shutdown:
                    engine.flatten_all('shutdown')
                    if store.pairs(active=True):
                        emit('alert',reason='shutdown_residual_exposure')
                        return 1
                return 0
            finally:
                market.close()


if __name__=='__main__':
    try:
        sys.exit(main())
    except Exception:
        # No traceback with credentials, exchange text, headers or env content.
        emit('fatal',reason='startup_or_runtime_failed_closed')
        sys.exit(1)
