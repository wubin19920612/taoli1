"""python -m tools.crossex_exec run --env-file /private/path (shadow by default)."""
import argparse
from contextlib import contextmanager
import http.client
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
from .limits import DEFAULT_VAR, DEFAULT_ENV
from .reporting import report
from .api import start_api
from .equity import EquityAdmin
from tools.fill_sim.network import ShadowAPI


def emit(kind,**fields):
    print(json.dumps(REDACTOR.clean(dict(kind=kind,at=time.time(),**fields)),allow_nan=False),flush=True)


@contextmanager
def maintenance_lock(path):
    """Use the SAME lifetime flock as run; never bypass a running coordinator."""
    import fcntl
    with open(str(path)+'.lock','a',encoding='utf-8') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            acquired=True
        except BlockingIOError:
            acquired=False
        yield acquired


def running_rebaseline(config,reason):
    from .api import parse_bind
    token=config.get('CROSSEX_EXEC_ADMIN_TOKEN','')
    if not token or token==config.get('CROSSEX_EXEC_API_TOKEN'):
        raise ValueError('running_executor_admin_required')
    addresses=parse_bind(config.get('CROSSEX_EXEC_API_BIND'))
    address=next((a for a in addresses if a[0]=='127.0.0.1'),addresses[0])
    conn=http.client.HTTPConnection(*address,timeout=20)
    try:
        conn.request('POST','/api/v1/equity/rebaseline',body=json.dumps(dict(confirm=True,reason=reason)),
                     headers={'X-Api-Token':token,'Content-Type':'application/json'})
        response=conn.getresponse()
        raw=response.read(131073)
        if len(raw)>131072:
            raise ValueError('admin_response_invalid')
        value=json.loads(raw)
        if response.status!=200:
            from .equity import ADMIN_REFUSALS
            if isinstance(value,dict) and value.get('error') in ADMIN_REFUSALS:
                raise ValueError(value['error'])
            raise ValueError('running_rebaseline_refused')
        return value
    finally:
        conn.close()


def run_maintenance(config,args):
    path=Path(config.get('CROSSEX_DB',DEFAULT_VAR+'/crossex-exec.db'))
    if not path.is_file():
        raise ValueError('database_not_found')
    with maintenance_lock(path) as acquired:
        if not acquired:
            if args.command in ('external-register','external-unregister'):
                result=running_external(config,args)
            elif args.command!='rebaseline':
                raise ValueError('release_requires_stopped_service')
            else:
                result=running_rebaseline(config,args.reason)
        else:
            # Live writes are disabled even when the env is configured for live.
            cross=CrossEx(Transport('gate',Credentials(config.get('CROSSEX_API_KEY',''),config.get('CROSSEX_API_SECRET','')),
                                    live=False),config.get('CROSSEX_POSITION_MODE','DUAL'))
            bitget=Bitget(Transport('bitget',Credentials(config.get('BITGET_API_KEY',''),config.get('BITGET_API_SECRET',''),
                             config.get('BITGET_API_PASSPHRASE','')),live=False),config.get('BITGET_POSITION_MODE','hedge_mode'))
            with Store(path) as store:
                if args.command in ('external-register','external-unregister'):
                    from .external import ExternalAdmin
                    from .market import RULES_URL
                    market=MarketData(store)
                    try:
                        rules=market.fetch('gate',RULES_URL,priority=2)
                        if not isinstance(rules,list):
                            raise ValueError('external_catalog_unavailable')
                        store.set_meta('external_catalog',dict(at=time.time(),rules=rules))
                    finally:
                        market.close()
                    result=ExternalAdmin(store,Accounts(cross,bitget)).apply(**external_body(args),
                        registered=args.command=='external-register',source='cli')
                else:
                    result=EquityAdmin(store,Accounts(cross,bitget)).apply(confirm=args.confirm,reason=args.reason,
                                source='cli',release_halt=args.command=='release-halt')
        emit('maintenance_complete',action=args.command,result=result)
        return 0


def external_body(args):
    from .external import checked_identity
    return dict(identity=checked_identity(dict(route=args.route,exchange_type=args.exchange_type,
        business=args.business,symbol=args.symbol,quote=args.quote,dex=args.dex)),
        expected_status=args.expected_status,reason=args.reason)


def running_external(config,args):
    from .api import parse_bind
    token=config.get('CROSSEX_EXEC_ADMIN_TOKEN','')
    if not token or token==config.get('CROSSEX_EXEC_API_TOKEN'):
        raise ValueError('running_executor_admin_required')
    addresses=parse_bind(config.get('CROSSEX_EXEC_API_BIND'))
    address=next((a for a in addresses if a[0]=='127.0.0.1'),addresses[0])
    conn=http.client.HTTPConnection(*address,timeout=20)
    try:
        action='register' if args.command=='external-register' else 'unregister'
        conn.request('POST','/api/v1/external-positions/'+action,body=json.dumps(external_body(args)),
            headers={'X-Api-Token':token,'Content-Type':'application/json'})
        response=conn.getresponse()
        raw=response.read(131073)
        if len(raw)>131072:
            raise ValueError('admin_response_invalid')
        value=json.loads(raw)
        if response.status!=200:
            # Preserve the conflict status, never auto-fetch and retry a write.
            from .external import REFUSALS
            reason=value.get('error') if isinstance(value,dict) else None
            raise ValueError(reason if reason in REFUSALS|{'external_conflict'} else 'external_write_refused')
        return value
    finally:
        conn.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description='CrossEx/Bitget execution; no keys in CLI or process environment')
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run')
    run.add_argument('--env-file',default=DEFAULT_ENV)
    for command in ('release-halt','rebaseline'):
        admin=sub.add_parser(command)
        admin.add_argument('--env-file',default=DEFAULT_ENV)
        admin.add_argument('--reason',default='manual_'+command.replace('-','_'))
        if command=='release-halt':
            admin.add_argument('--confirm',required=True,help='Exact current halt reason')
        else:
            admin.add_argument('--confirm',required=True,action='store_true')
    for command in ('external-register','external-unregister'):
        admin=sub.add_parser(command)
        admin.add_argument('--env-file',default=DEFAULT_ENV)
        admin.add_argument('--route',default='crossex',choices=('crossex',))
        for flag in ('exchange-type','business','symbol','quote','dex','expected-status'):
            admin.add_argument('--'+flag,required=True)
        admin.add_argument('--reason',default='manual_external_ownership')
    external_list=sub.add_parser('external-list')
    external_list.add_argument('--db',default=DEFAULT_VAR+'/crossex-exec.db')
    reporting=sub.add_parser('report')
    reporting.add_argument('--db',default=DEFAULT_VAR+'/crossex-exec.db')
    args=parser.parse_args(argv)
    if args.command=='external-list':
        from .management import Management
        print(json.dumps(Management(args.db).get('external-positions'),allow_nan=False,indent=2))
        return 0
    if args.command=='report':
        if not Path(args.db).is_file():
            parser.error('database_not_found')
        with Store(args.db) as store:
            print(json.dumps(report(store),allow_nan=False,indent=2))
        return 0
    # A local development machine cannot satisfy POSIX ownership/mode guarantees.
    config=load_env(args.env_file)
    if args.command in ('release-halt','rebaseline','external-register','external-unregister'):
        try:
            return run_maintenance(config,args)
        except ValueError as error:
            # All known refusal messages are local constants; never echo an
            # exchange response, file content, credential or arbitrary exception.
            from .equity import ADMIN_REFUSALS
            from .external import REFUSALS
            reason=str(error) if str(error) in ADMIN_REFUSALS|REFUSALS|{'external_conflict'} else 'maintenance_failed_closed'
            emit('maintenance_refused',action=args.command,reason=reason)
            return 1
    if config.get('CROSSEX_LIVE_ENABLED','0') not in ('0','1'):
        raise ValueError('invalid_live_switch')
    live=config.get('CROSSEX_LIVE_ENABLED','0')=='1'
    risk_confirmed=config.get('BITGET_RISK_RATE_CONFIRMED','0')=='1'
    if live and not risk_confirmed:
        raise ValueError('bitget_risk_convention_unconfirmed')
    path=Path(config.get('CROSSEX_DB',DEFAULT_VAR+'/crossex-exec.db'))
    path.parent.mkdir(parents=True,exist_ok=True)
    # flock held for full process lifetime prevents two services sharing a journal.
    import fcntl
    with open(str(path)+'.lock','a',encoding='utf-8') as process_lock:
        fcntl.flock(process_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with Store(path) as store:
            store.assert_startable()
            cross=CrossEx(Transport('gate',Credentials(config.get('CROSSEX_API_KEY',''),config.get('CROSSEX_API_SECRET','')),live=live),
                          config.get('CROSSEX_POSITION_MODE','DUAL'))
            bitget=Bitget(Transport('bitget',Credentials(config.get('BITGET_API_KEY',''),config.get('BITGET_API_SECRET',''),
                                                       config.get('BITGET_API_PASSPHRASE','')),live=live),
                          config.get('BITGET_POSITION_MODE','hedge_mode'))
            market=MarketData(store)
            accounts=Accounts(cross,bitget,risk_confirmed=risk_confirmed)
            engine=Engine(store,market,accounts,live=live,stop_file=config.get('CROSSEX_STOP_FILE',DEFAULT_VAR+'/STOP'),
                          alert_sink=lambda reason:emit('alert',reason=reason),config=config)
            shutdown=False
            def stop(signum,frame):
                nonlocal shutdown
                shutdown=True
                engine.risk='STOP'
            signal.signal(signal.SIGTERM,stop)
            signal.signal(signal.SIGINT,stop)
            source=ShadowAPI(config.get('TAOLI_BASE_URL','http://127.0.0.1:8000'),config.get('TAOLI_API_TOKEN',''))
            api_service=None
            try:
                engine.startup(tuple(filter(None,config.get('CROSSEX_BASES','').split(','))))
                api_service=start_api(config,path,logger=emit,equity_admin=engine.rebaseline,external_admin=engine.external_admin)
                last_status=0
                while not shutdown and store.meta('state')!='HALTED':
                    try:
                        engine.tick()
                        now=time.time()
                        rows=source.poll() if engine.can_open() else []
                        if rows:
                            # Each accepted pair can take 10s to confirm; service exits
                            # this batch after one fresh trigger to revisit risk/positions.
                            for row in rows:
                                if not store.db.execute('SELECT 1 FROM triggers WHERE id=?',(row.get('id'),)).fetchone():
                                    engine.ingest(row)
                                    break
                        if now-last_status>=300:
                            emit('status',live=live,state=store.meta('state','RUNNING'),risk=engine.risk,
                                 stop_reason=store.meta('stop_reason'),active_pairs=len(store.pairs(active=True)),
                                 liabilities=store.meta('liabilities',[]),
                                 equity_baseline=store.meta('account_equity_baseline'),
                                 equity_current=store.meta('account_equity_current'))
                            last_status=now
                    except Exception:
                        engine.alert('cycle_failed_closed')
                    time.sleep(.2)
                if shutdown:
                    engine.flatten_all('shutdown')
                    if store.pairs(active=True) or (engine.external.records() and not (store.meta('flatten_status') or {}).get('program_flat')):
                        emit('alert',reason='shutdown_residual_exposure')
                        return 1
                    if engine.external.records():
                        emit('shutdown_positions',**store.meta('flatten_status'))
                return 0
            finally:
                if api_service is not None:
                    api_service.close()
                engine.close()
                market.close()


if __name__=='__main__':
    try:
        sys.exit(main())
    except Exception:
        # No traceback with credentials, exchange text, headers or env content.
        emit('fatal',reason='startup_or_runtime_failed_closed')
        sys.exit(1)
