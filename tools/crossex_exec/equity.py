"""Explicit, audited equity epochs. Reads accounts; never submits/cancels orders."""
from contextlib import nullcontext
import time

from tools.fill_sim.core import dec, day
from .external import ExternalRegistry, plain
from .external_accounting import anchor_value, read_snapshot, program_activity


POSITION_LOCKS = frozenset(('position_mismatch','unhedged_position','repair_stopped',
    'unknown_order','unknown_close','fills_pending','accounting_pending','entry_accounting_unknown',
    'negative_local_position','flatten_retry_pending','flatten_snapshot_unavailable'))
BASELINE_KEYS = ('account_equity_baseline','equity_baseline','daily_equity','daily_upnl','risk_pnl_baseline')
ADMIN_REFUSALS = frozenset(('local_orders_pending','position_or_order_lock','local_positions',
    'not_halted','halt_confirmation','confirmation_required','halted_requires_release',
    'account_read_unavailable','remote_positions','remote_orders_unavailable','remote_orders',
    'account_snapshot_stale','account_liability','equity_invalid','invalid_admin_reason',
    'equity_revision_changed','database_not_found','release_requires_stopped_service',
    'running_executor_admin_required','admin_response_invalid','running_rebaseline_refused',
    'external_income_unverifiable','external_identity_unverifiable','external_catalog_unavailable',
    'margin_open_limit'))


def halt_reason(store):
    return store.meta('halt_reason') or store.meta('stop_reason') or 'halted'


class EquityAdmin:
    def __init__(self,store,accounts,*,engine=None,clock=time.time):
        self.store,self.accounts,self.engine,self.clock=store,accounts,engine,clock

    def local_flat(self):
        engine,store=self.engine,self.store
        if engine and (engine.executor.pending_writes or engine.executor.owned):
            raise ValueError('local_orders_pending')
        locks=engine.stops.reasons if engine else store.meta('stop_locks',{})
        if store.meta('position_mismatch') or POSITION_LOCKS.intersection(locks):
            raise ValueError('position_or_order_lock')
        positions={}
        for row in store.iter_orders():
            if row.get('state') not in ('TERMINAL','REJECTED','SHADOW','UNSENT') or row.get('accounting_pending',False):
                raise ValueError('local_orders_pending')
            if row['state'] in ('SHADOW','UNSENT'):
                continue
            qty=dec(row.get('filled',0))
            if qty:
                key=(row['instrument']['market']['exchange'],row['instrument']['symbol'],row['position_side'])
                positions[key]=positions.get(key,dec(0))+qty*(-1 if row['reduce_only'] else 1)
        if any(positions.values()):
            raise ValueError('local_positions')
        for pair in store.iter_pairs(exclude_states=('SHADOW','SKIPPED')):
            if (pair['state']!='CLOSED' or pair.get('remote_pending') or pair.get('accounting_pending')
                    or any(dec(q) for field in ('remaining','remote_remaining') for q in pair.get(field,()))):
                raise ValueError('local_positions')

    def state_check(self,confirm,release_halt):
        state=self.store.meta('state')
        if release_halt:
            if state!='HALTED':
                raise ValueError('not_halted')
            if not isinstance(confirm,str) or confirm!=halt_reason(self.store):
                raise ValueError('halt_confirmation')
        else:
            if confirm is not True:
                raise ValueError('confirmation_required')
            if state in ('HALTED','HALTING'):
                raise ValueError('halted_requires_release')
        self.local_flat()

    def read_flat_accounts(self):
        started=self.clock()
        registry=ExternalRegistry(self.store,self.clock)
        try:
            snapshot=(read_snapshot(self.accounts,self.store,self.clock) if registry.records() else self.accounts.snapshot())
        except Exception:
            raise ValueError('account_read_unavailable') from None
        projection=registry.split(snapshot) if registry.records() else snapshot
        if not isinstance(projection.get('positions'),dict) or projection['positions']:
            raise ValueError('remote_positions')
        for venue in (self.accounts.cross,self.accounts.bitget):
            try:
                orders=venue.open_orders()
            except Exception:
                raise ValueError('remote_orders_unavailable') from None
            if not isinstance(orders,list) or orders:
                raise ValueError('remote_orders')
        if not 0<=self.clock()-started<=10:
            raise ValueError('account_snapshot_stale')
        if not isinstance(snapshot.get('liabilities'),list) or snapshot['liabilities']:
            raise ValueError('account_liability')
        if registry.records() and (len(snapshot.get('margins',()))!=2
                or any(m is None or dec(m)<3 for m in snapshot['margins'])):
            raise ValueError('margin_open_limit')
        if registry.records():
            try:
                snapshot['program_funding_at_epoch']=str(program_activity(self.store,snapshot['activity'],
                    snapshot.get('accounting_at',started)))
            except (ValueError,KeyError,TypeError,ArithmeticError):
                raise ValueError('external_income_unverifiable') from None
        try:
            equities={v:str(dec(snapshot['equity_by_venue'][v])) for v in ('crossex','bitget')}
            if any(dec(v)<=0 for v in equities.values()) or dec(projection['upnl'])!=0:
                raise ValueError('invalid')
        except (ValueError,TypeError,KeyError,ArithmeticError):
            raise ValueError('equity_invalid') from None
        return snapshot,started,equities

    def pnl_offset(self):
        """Keep immutable fills/history; reset only the risk measurement epoch."""
        daily=total=dec(0)
        today=day(self.clock())
        entries={}
        for row in self.store.iter_orders(reduce_only=False,exclude_states=('SHADOW','UNSENT')):
            if dec(row.get('filled',0)):
                if row.get('vwap') is None:
                    raise ValueError('local_orders_pending')
                key=(row['pair_id'],row['leg'])
                if key in entries:
                    raise ValueError('local_orders_pending')
                entries[key]=dec(row['vwap'])
        for row in self.store.iter_orders(exclude_states=('SHADOW','UNSENT')):
            value=-dec(row.get('fee') or 0)
            qty=dec(row.get('filled',0))
            if row['reduce_only'] and qty:
                if row.get('vwap') is None or (row['pair_id'],row['leg']) not in entries:
                    raise ValueError('local_orders_pending')
                value+=qty*(dec(row['vwap'])-entries[(row['pair_id'],row['leg'])])*(1 if row['leg']==0 else -1)
            total+=value
            if day((row.get('fill_ms') or row['created_at']*1000)/1000)==today:
                daily+=value
        return dict(day=today,total=str(total),daily=str(daily))

    def apply(self,*,confirm,reason,source,release_halt=False):
        if (source not in ('cli','admin_api') or not isinstance(reason,str) or not reason.strip()
                or len(reason)>200 or any(ord(c)<32 for c in reason)):
            raise ValueError('invalid_admin_reason')
        store,engine=self.store,self.engine
        executor_lock=engine.executor.lock if engine else nullcontext()
        with executor_lock, store.lock:
            self.state_check(confirm,release_halt)
            revision=store.meta('equity_baseline_revision',0)
            if engine:
                engine._equity_admin_active=True
        try:
            # No Store/Executor lock spans private I/O. The running engine's
            # coordinator is held; its sends also see the in-memory admin latch.
            snapshot,captured,equities=self.read_flat_accounts()
            with executor_lock, store.lock:
                self.state_check(confirm,release_halt)
                if revision!=store.meta('equity_baseline_revision',0):
                    raise ValueError('equity_revision_changed')
                if not 0<=self.clock()-captured<=10:
                    raise ValueError('account_snapshot_stale')
                old={k:store.meta(k) for k in BASELINE_KEYS}
                total=str(sum(map(dec,equities.values()),dec(0)))
                new=dict(account_equity_baseline=equities,equity_baseline=total,
                    daily_equity=dict(day=day(self.clock()),equity=total),
                    daily_upnl=dict(day=day(self.clock()),upnl='0',positions={},captured_at=captured),
                    risk_pnl_baseline=self.pnl_offset())
                cause=halt_reason(store) if release_halt else None
                locks={k:set(v) for k,v in (engine.stops.reasons if engine else store.meta('stop_locks',{})).items()}
                if release_halt:
                    # ONLY equity halt latches belong to this administrative action.
                    for key in ('account_equity_drop','total_loss'):
                        locks.pop(key,None)
                audit=dict(old=old,new=new,at=self.clock(),captured_at=captured,reason=reason,
                           source=source,halt_reason=cause,revision=revision+1)
                registry=ExternalRegistry(store,self.clock)
                external_projection=registry.split(snapshot) if registry.records() else None
                if external_projection is not None:
                    # Audit the full account and exact excluded positions. These
                    # operations reset an explicit epoch, never erase ownership.
                    audit['external_snapshot']=plain(external_projection)
                    new['external_equity_anchor']=anchor_value(store,snapshot,external_projection,captured)
                    new['external_equity_anchor']['program_funding_offset']=snapshot['program_funding_at_epoch']
                    new['program_daily_upnl']=dict(day=day(self.clock()),upnl='0',positions={},captured_at=captured)
                    new['program_last_upnl']=dict(upnl='0',positions={},captured_at=captured)
                    new['daily_upnl']=dict(day=day(self.clock()),upnl=str(snapshot['upnl']),
                        positions=snapshot['upnl_by_position'],captured_at=captured)
                if not 0<=self.clock()-captured<=10:
                    raise ValueError('account_snapshot_stale')
                with store.transaction():
                    for key,value in new.items():
                        store.set_meta(key,value)
                    store.set_meta('account_equity_current',equities)
                    store.set_meta('account_loss_current',dict(daily='0',total='0',at=self.clock()))
                    store.set_meta('last_upnl',dict(upnl=str(snapshot['upnl']) if external_projection is not None else '0',
                        positions=snapshot['upnl_by_position'] if external_projection is not None else {},captured_at=captured))
                    store.set_meta('equity_baseline_revision',revision+1)
                    if release_halt:
                        store.set_meta('state','RUNNING')
                        store.set_meta('halt_reason',None)
                        store.set_meta('stop_locks',{k:sorted(v) for k,v in locks.items()})
                        preferred=store.meta('stop_reason')
                        store.set_meta('stop_reason',preferred if preferred in locks else next(iter(locks),None))
                    store.event('HALT_RELEASED' if release_halt else 'EQUITY_REBASELINED',audit)
                    if not 0<=self.clock()-captured<=10:
                        raise ValueError('account_snapshot_stale')
                # No mutable engine state changes before a successful COMMIT.
                if engine:
                    engine.account_cache.publish(snapshot,captured)
                    engine.snapshot=dict(snapshot,captured_at=captured)
                    engine.stops.reasons=locks
                    engine.stops.dirty=False
                    engine._account_fresh_streak=engine._liability_clear_streak=0
                    engine._account_fresh_at=None
                    engine.risk='STOP'
                    engine.last_reconcile=0
                return audit
        finally:
            if engine:
                with store.lock:
                    engine._equity_admin_active=False
