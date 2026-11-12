"""Explicit external ownership and irreversible route/symbol trading tombstones.

The registry is a durable logical table in meta.external_contracts. Updates and
their audit share BEGIN IMMEDIATE; deletion is deliberately not an operation.
"""
import copy
import hashlib
import json
import re
import time
from decimal import Decimal

from tools.fill_sim.core import dec
from .accounts import remote_positions
from .position_values import verify_external_rows


IDENTITY_FIELDS = frozenset(('route','exchange_type','business','symbol','quote','dex'))
REFUSALS = frozenset(('program_ownership','program_ownership_unknown','external_remote_position',
    'external_remote_order','external_identity_unverifiable','external_catalog_unavailable',
    'account_snapshot_stale','external_read_unavailable','local_orders_pending',
    'external_income_unverifiable','external_baseline_unverifiable'))


class ExternalUnverifiable(ValueError):
    pass


def plain(value):
    if isinstance(value,Decimal):
        return str(value)
    if isinstance(value,dict):
        return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [plain(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(plain(value),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def symbol_identity(symbol):
    if not isinstance(symbol,str) or len(symbol)>160:
        raise ValueError('invalid_external_identity')
    match=re.fullmatch(r'([A-Z][A-Z0-9]*)_FUTURE_([^|\s_]+)_(USDT|USDC)',symbol)
    if not match:
        raise ValueError('invalid_external_identity')
    exchange,raw,quote=match.groups()
    dex=raw.split(':')[0] if ':' in raw else ''
    if (':' in raw and (exchange!='HYPERLIQUID' or raw.count(':')!=1 or not all(raw.split(':')))
            or any(ord(c)<32 for c in raw)):
        raise ValueError('invalid_external_identity')
    return dict(route='crossex',exchange_type=exchange,business='FUTURE',symbol=symbol,quote=quote,dex=dex)


def checked_identity(value):
    if not isinstance(value,dict) or set(value)!=IDENTITY_FIELDS:
        raise ValueError('invalid_external_identity')
    expected=symbol_identity(value.get('symbol'))
    if value!=expected:
        raise ValueError('invalid_external_identity')
    return dict(expected)


def route_symbol(raw):
    market=raw['market']
    return ('bitget' if market['exchange']=='bitget' else 'crossex',raw['symbol'])


class ExternalRegistry:
    def __init__(self,store,clock=time.time):
        self.store,self.clock=store,clock

    def records(self):
        rows=self.store.meta('external_contracts',{})
        if not isinstance(rows,dict):
            raise ExternalUnverifiable('external_identity_unverifiable')
        try:
            for key,row in rows.items():
                if (key!=digest(checked_identity(row['identity'])) or row['forbidden'] is not True
                        or type(row['registered']) is not bool):
                    raise ValueError('invalid_registry')
        except (ValueError,KeyError,TypeError):
            raise ExternalUnverifiable('external_identity_unverifiable') from None
        return rows

    def status(self):
        catalog=self.store.meta('external_catalog',{})
        rules=sorted(catalog.get('rules',[]),key=lambda r:json.dumps(r,sort_keys=True))
        return 'x1:'+digest(dict(records=self.records(),rules=rules))

    def describe(self):
        return dict(status=self.status(),contracts=list(self.records().values()))

    def forbidden(self,route,symbol):
        # Check the transport identity before looking at canonical tickers,
        # direction, mappings, categories, current catalog or registry status.
        return any((r['identity']['route'],r['identity']['symbol'])==(route,symbol)
                   for r in self.records().values())

    def check_order(self,order):
        if self.forbidden(*route_symbol(order['instrument'])):
            raise ValueError('external_contract_forbidden')

    def rule(self,identity):
        catalog=self.store.meta('external_catalog',{})
        if not 0<=self.clock()-catalog.get('at',0)<=21600:
            raise ExternalUnverifiable('external_catalog_unavailable')
        rows=[r for r in catalog.get('rules',[]) if r.get('symbol')==identity['symbol']]
        if len(rows)!=1:
            raise ExternalUnverifiable('external_identity_unverifiable')
        row=rows[0]
        try:
            if (row['exchange_type']!=identity['exchange_type'] or row['business_type']!=identity['business']
                    or row['tag']!=identity['dex'] or row['state']!='live' or row['support_cross']!='true'
                    or str(row['delist_time'])!='0'
                    or any(dec(row[k])<=0 for k in ('lot_size','min_size','tick_size'))
                    or (row['max_limit_size'] is not None and dec(row['max_limit_size'])<=0)):
                raise ValueError('invalid_rule')
        except (ValueError,KeyError,TypeError):
            raise ExternalUnverifiable('external_identity_unverifiable') from None
        return copy.deepcopy(row)

    def no_program_owner(self,identity):
        target=(identity['route'],identity['symbol'])
        try:
            for row in self.store.iter_orders():
                if route_symbol(row['instrument'])==target:
                    raise ExternalUnverifiable('program_ownership')
            for pair in self.store.iter_pairs():
                if any(route_symbol(raw)==target for raw in pair['instruments']):
                    raise ExternalUnverifiable('program_ownership')
        except (KeyError,TypeError):
            # A corrupt orphan must never be hidden by administrative relabeling.
            raise ExternalUnverifiable('program_ownership_unknown') from None

    def split(self,snapshot,*,pnl=True):
        rows=self.records()
        if not rows:
            return dict(snapshot,external_positions={},external_upnl='0',external_upnl_by_position={})
        try:
            for record in rows.values():
                i=record['identity']
                if self.rule(i)!=record['rule']:
                    raise ValueError('catalog_changed')
                self.no_program_owner(i)
            positions=snapshot['positions']
            raw=snapshot['raw_positions']
            normalized=remote_positions(raw['crossex'],raw['bitget'])
            if normalized!={k:dec(v) for k,v in positions.items()}:
                raise ValueError('raw_positions_disagree')
            verify_external_rows(snapshot,[r['identity'] for r in rows.values()])
            active={r['identity']['symbol'] for r in rows.values() if r['registered']}
            external={}
            for key,qty in positions.items():
                route,symbol,side=key.split('|')
                if side not in ('long','short'):
                    raise ValueError('side_unknown')
                if route=='crossex' and self.forbidden(route,symbol):
                    if symbol not in active:
                        raise ValueError('external_not_registered')
                    external[key]=qty
            result=dict(snapshot,positions={k:v for k,v in positions.items() if k not in external},
                        external_positions=external)
            if pnl:
                floating=snapshot['upnl_by_position']
                if set(floating)!=set(positions) or sum(map(dec,floating.values()),dec(0))!=dec(snapshot['upnl']):
                    raise ValueError('pnl_unknown')
                # Keep the full account snapshot intact. Only this projection is filtered.
                result['upnl_by_position']={k:v for k,v in floating.items() if k not in external}
                result['upnl']=str(sum(map(dec,result['upnl_by_position'].values()),dec(0)))
                result['external_upnl']=str(sum((dec(floating[k]) for k in external),dec(0)))
                result['external_upnl_by_position']={k:floating[k] for k in external}
            return result
        except (KeyError,TypeError,ValueError,ArithmeticError):
            raise ExternalUnverifiable('external_identity_unverifiable') from None


class ExternalAdmin:
    def __init__(self,store,accounts,*,engine=None,clock=time.time):
        self.store,self.accounts,self.engine,self.clock=store,accounts,engine,clock
        self.registry=ExternalRegistry(store,clock)

    def apply(self,*,identity,registered,expected_status,reason,source):
        from .management import WriteConflict
        identity=checked_identity(identity)
        if (type(registered) is not bool or not isinstance(expected_status,str)
                or not re.fullmatch(r'x1:[0-9a-f]{64}',expected_status)
                or source not in ('cli','admin_api') or not isinstance(reason,str) or not reason.strip()
                or len(reason)>200 or any(ord(c)<32 for c in reason)):
            raise ValueError('invalid_external_update')
        store,registry,engine=self.store,self.registry,self.engine
        if engine and (engine.executor.pending_writes or engine.executor.owned):
            raise ValueError('local_orders_pending')
        started=self.clock()
        snapshot=None
        try:
            snapshot=self.accounts.snapshot()
            orders=self.accounts.cross.open_orders()
        except Exception as error:
            evidence=getattr(error,'account_snapshot',snapshot)
            if evidence is not None:
                store.set_meta('external_failed_snapshot',plain(evidence))
            raise ValueError('external_read_unavailable') from None
        store.set_meta('external_admin_snapshot',plain(dict(snapshot,raw_open_orders=dict(crossex=orders))))
        conflict=None
        with store.transaction():
            current=registry.status()
            if expected_status!=current:
                conflict=WriteConflict('EXTERNAL_CONFLICT',dict(error='external_conflict',current_status=current),
                    dict(expected_status=expected_status),identity=identity,registered=registered)
                conflict.audit['source']=source
                store.event(conflict.kind,conflict.audit)
            else:
                if not 0<=self.clock()-started<=10:
                    raise ValueError('account_snapshot_stale')
                if engine and (engine.executor.pending_writes or engine.executor.owned):
                    raise ValueError('local_orders_pending')
                registry.no_program_owner(identity)
                rule=registry.rule(identity)
                raw=snapshot.get('raw_positions',{})
                try:
                    positions=remote_positions(raw['crossex'],raw['bitget'])
                    if positions!={k:dec(v) for k,v in snapshot['positions'].items()}:
                        raise ValueError('raw_mismatch')
                    verify_external_rows(snapshot,[identity])
                except (KeyError,ValueError,TypeError):
                    raise ExternalUnverifiable('external_identity_unverifiable') from None
                if not registered and any(k.split('|')[:2]==[identity['route'],identity['symbol']] for k in positions):
                    raise ValueError('external_remote_position')
                if not isinstance(orders,list) or any(not isinstance(o,dict) or not o.get('symbol') for o in orders):
                    raise ValueError('external_read_unavailable')
                if any(o['symbol']==identity['symbol'] for o in orders):
                    raise ValueError('external_remote_order')
                rows=registry.records()
                key=digest(identity)
                old=rows.get(key)
                if not registered and (not old or not old['registered']):
                    raise ValueError('external_not_registered')
                record=dict(identity=identity,raw_base=identity['symbol'].split('_',2)[2].rsplit('_',1)[0],
                    rule=rule,registered=registered,forbidden=True,revision=(old or {}).get('revision',0)+1,
                    registered_at=(old or {}).get('registered_at',self.clock()),updated_at=self.clock())
                rows[key]=record
                store.set_meta('external_contracts',rows)
                # Adding a contract changes the attribution domain. An explicit
                # flat-program rebaseline is required; no existing loss latch is cleared.
                store.set_meta('external_attribution_revision',store.meta('external_attribution_revision',0)+1)
                store.event('EXTERNAL_CHANGED',dict(old=old,new=record,source=source,reason=reason,
                    expected_status=expected_status,at=self.clock(),snapshot=plain(snapshot),open_orders=orders))
                result=dict(record=record,status=registry.status())
                if not 0<=self.clock()-started<=10:
                    raise ValueError('account_snapshot_stale')
        if conflict:
            raise conflict
        if engine:
            engine.risk='STOP'
            engine.last_reconcile=0
        return result
