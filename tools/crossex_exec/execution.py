"""Durable IOC lifecycle. Unknown outcomes are never automatically re-submitted."""
import threading
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from itertools import chain
from tools.fill_sim.core import dec
from .adapters import validate_intent
from .transport import APIError
from .stops import StopLocks
from .storage import matches_order


NONTERMINAL = frozenset(('SENT', 'ACK', 'UNKNOWN'))


class Executor:
    def __init__(self, store, adapters, *, live=False, poll_interval=.2, confirm_timeout=10, can_open=lambda: True, check_order=None, check_pair=None, check_release=None, alert=None, clock=time.time):
        self.store,self.adapters,self.live=store,adapters,live
        self.poll_interval,self.confirm_timeout=poll_interval,confirm_timeout
        self.can_open=can_open
        self.check_order=check_order
        self.check_pair,self.clock=check_pair,clock
        self.check_release=check_release
        self.alert=alert or StopLocks(store).add
        self.locks,self.lock= {},threading.RLock()
        self.pending_writes={}
        self.owned={}

    def order(self,cid):
        with self.lock:
            return deepcopy(self.pending_writes[cid][0]) if cid in self.pending_writes else self.store.order(cid)

    def iter_orders(self, **filters):
        with self.lock:
            pending={cid:deepcopy(row) for cid,(row,_) in self.pending_writes.items()}
        for row in self.store.iter_orders(**filters):
            if row['cid'] not in pending:
                yield row
        for row in pending.values():
            if matches_order(row, **filters):
                yield row

    def orders(self, **filters):
        return list(self.iter_orders(**filters))

    @contextmanager
    def ownership(self, intents):
        with self.lock:
            for i in intents:
                self.owned[i['cid']]=self.owned.get(i['cid'],0)+1
        try:
            yield
        finally:
            with self.lock:
                for i in intents:
                    cid=i['cid']
                    self.owned[cid]-=1
                    if not self.owned[cid]:
                        del self.owned[cid]
                        self.locks.pop(cid,None)

    def save(self,order,kind=None):
        """Retain observations before attempting I/O; retries never resend orders."""
        with self.lock:
            self.pending_writes[order['cid']]=(deepcopy(order),kind)
            try:
                self.store.save_order(order,kind)
            except Exception:
                return False
            self.pending_writes.pop(order['cid'],None)
            return True

    def flush(self):
        with self.lock:
            for order,kind in list(self.pending_writes.values()):
                self.save(order,kind)

    def adapter(self,i):
        return self.adapters['bitget' if i['instrument']['market']['exchange']=='bitget' else 'crossex']

    def execute_pair(self, intents):
        with self.ownership(intents):
            return self._execute_pair(intents)

    def _execute_pair(self, intents):
        # Validation and BOTH intent records/events are one atomic preparation.
        with self.lock:
            fresh=[]
            for i in intents:
                validate_intent(i)
                if self.order(i['cid']) is None:
                    fresh.append(dict(i,state='INTENT',filled='0',oid=None,accounting_pending=True))
            self.store.save_orders(fresh,'INTENT')
        if self.live and self.check_order and all(self.order(i['cid'])['state']=='INTENT' for i in intents):
            try:
                for i in intents:
                    self.check_order(i)
            except Exception:
                results=[]
                for i in intents:
                    row=self.order(i['cid'])
                    row.update(state='UNSENT',accounting_pending=False,reason='pair_risk_admission')
                    self.save(row,'UNSENT')
                    results.append(row)
                return results
        gate=None
        if self.live and intents and all(not i['reduce_only'] for i in intents):
            approved=threading.Event()
            # Both transports first finish rate limiting and connection setup.
            # The barrier action makes ONE shared decision; neither sender may
            # independently invalidate only its own leg after this decision.
            def authorize():
                # One configuration decision spans review, durable SENT and release.
                # Management writers cannot split this batch between versions.
                # Match save/flush/recover: executor lock precedes journal lock.
                with self.lock, self.store.lock:
                    if self.check_order:
                        for i in intents:
                            self.check_order(i)
                    if self.check_pair:
                        self.check_pair(intents)
                    if not self.can_open():
                        raise APIError('rejected')
                    sent_rows=[dict(self.order(i['cid']),state='SENT',sent_persisted_ms=self.clock()*1000) for i in intents]
                    self.store.save_orders(sent_rows,'SENT')
                    # FULL commits may take longer than the entire book-age budget.
                    # No sender is released until BOTH durable writes and the final
                    # shared hard-risk/age checks succeed. No retry after this point.
                    if self.check_order:
                        for i in intents:
                            self.check_order(i)
                    if self.check_release:
                        self.check_release(intents)
                    elif not self.can_open():
                        raise APIError('rejected')
                    gate.sent_rows={row['cid']:row for row in sent_rows}
                    approved.set()
            gate=threading.Barrier(len(intents),action=authorize,timeout=max(3,self.confirm_timeout))
            gate.approved=approved
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='ioc') as pool:
            jobs=[pool.submit(self.execute,i,send_gate=gate,defer_confirm=True) for i in intents]
            dispatched=[f.result() for f in jobs]
            # Both admitted sends finish before a confirmation failure can latch
            # unknown_order. A slow dispatch must not race its peer's recovery.
            jobs=[pool.submit(self.confirm,i) if i['state'] in NONTERMINAL else None for i in dispatched]
            return [job.result() if job else i for i,job in zip(dispatched,jobs)]

    def prepare(self,i):
        validate_intent(i)
        with self.lock:
            existing=self.order(i['cid'])
            if existing is None:
                i=dict(i,state='INTENT',filled='0',oid=None,accounting_pending=True)
                self.store.save_order(i,'INTENT')
                return i
            return existing

    def execute(self,intent,*,send_gate=None,defer_confirm=False):
        with self.ownership([intent]):
            return self._execute(intent,send_gate=send_gate,defer_confirm=defer_confirm)

    def _execute(self,intent,*,send_gate=None,defer_confirm=False):
        with self.lock:
            mutex=self.locks.setdefault(intent['cid'],threading.Lock())
        with mutex:
            i=self.prepare(intent)
            if i['state'] in ('TERMINAL','REJECTED','SHADOW','UNSENT'):
                if send_gate and not send_gate.approved.is_set():
                    send_gate.abort()
                return i
            if not self.live:
                if i['state']!='INTENT':
                    # A prior live attempt cannot become a shadow success after restart.
                    return i if defer_confirm else self.confirm(i)
                i.update(state='SHADOW',accounting_pending=False)
                self.save(i,'SHADOW')
                return i
            if i['state']!='INTENT':
                if send_gate and not send_gate.approved.is_set():
                    send_gate.abort()
                return i if defer_confirm else self.confirm(i)
            adapter=self.adapter(i)
            def sent():
                if send_gate:
                    send_gate.wait()
                    # The barrier action already committed both records. Only
                    # in-memory work is allowed between release and transport.
                    i.update(send_gate.sent_rows[i['cid']])
                    return
                else:
                    if self.check_order:
                        self.check_order(i)
                    if not i['reduce_only']:
                        if self.check_pair:
                            self.check_pair([i])
                        if not self.can_open():
                            raise APIError('rejected')
                sent_row=dict(i,state='SENT',sent_persisted_ms=self.clock()*1000)
                # Write-ahead is mandatory BEFORE transport may send. If it fails,
                # i remains INTENT and is safely retired as UNSENT by the handler.
                self.store.save_order(sent_row,'SENT')
                i.update(sent_row)
            def on_send():
                # Memory only, immediately before writing the signed request.
                # Missing observations (including a crash) remain unknown.
                i['sent_ms']=self.clock()*1000
            sent.on_send=on_send
            try:
                if self.check_order and not send_gate:
                    self.check_order(i)  # known breaches never reach adapter.place
                oid=adapter.place(i,before_send=sent)
                i.update(oid=oid,state='ACK',ack_ms=self.clock()*1000)
                self.save(i,'ACK')
            except Exception as error:
                if send_gate and not send_gate.approved.is_set():
                    send_gate.abort()
                kind=error.kind if isinstance(error,APIError) else 'unknown'
                if i['state']=='INTENT':
                    i.update(state='UNSENT',accounting_pending=False,reason=kind)
                    self.save(i,'UNSENT')
                    return i
                if kind=='rejected':
                    i.update(state='REJECTED',accounting_pending=False,reason=kind)
                    self.save(i,'REJECTED')
                    return i
                i.update(state='UNKNOWN',reason=kind)
                self.save(i,'UNKNOWN')
            return i if defer_confirm else self.confirm(i)

    def confirm(self,i):
        adapter=self.adapter(i)
        deadline=time.monotonic()+self.confirm_timeout
        while time.monotonic()<deadline:
            try:
                status=adapter.detail(i,deadline=deadline)
                if not i.get('oid'):
                    i.update(oid=status['oid'],state='ACK',ack_ms=self.clock()*1000)
                    self.save(i,'ACK')
                if dec(status['filled'])>dec(i.get('filled',0)):
                    i['fill_observed_at']=self.clock()
                i.update(venue_state=status['venue_state'],filled=status['filled'],rejected=status['rejected'])
                # Persist partial/late fill observation even if confirmation times out.
                self.save(i)
                if status.get('average_price') is not None:
                    i['average_price']=status['average_price']
                if status['terminal']:
                    i.update(state='TERMINAL',terminal_ms=self.clock()*1000)
                    self.save(i,'TERMINAL')
                    return self.account(i)
            except Exception:
                pass
            time.sleep(min(self.poll_interval,max(0,deadline-time.monotonic())))
        i.update(state='UNKNOWN',reason='confirmation_deadline')
        self.save(i,'UNKNOWN')
        self.alert('unknown_order')
        return i

    def account(self,i):
        if dec(i['filled'])==0:
            i.update(vwap=None,fee='0',fill_ms=None,trade_ids=[],accounting_pending=False)
        else:
            try:
                i.update(self.adapter(i).fills(i))
            except Exception:
                i['accounting_pending']=True
                self.alert('fills_pending')
        self.save(i,'FILLS' if not i['accounting_pending'] else 'FILLS_PENDING')
        return i

    def cancel(self,i):
        if not self.live:
            return
        try:
            self.adapter(i).cancel(i)
        except Exception:
            self.store.event('CANCEL_UNKNOWN',cid=i['cid'])
        # Cancellation ACK is not a terminal fill confirmation.
        return self.confirm(i)

    def recover(self,*,startup=True,pair_id=None):
        self.flush()
        for i in chain(self.iter_orders(pair_id=pair_id, states=(*NONTERMINAL,'INTENT')),
                       self.iter_orders(pair_id=pair_id, states=('TERMINAL',), accounting_pending=True)):
            with self.lock:
                # Do not retire a legitimate INTENT waiting at transport/barrier.
                # A durable SENT/UNKNOWN without an owner is ALWAYS reconciled.
                if i['cid'] in self.owned:
                    continue
                i=self.order(i['cid'])
                if i['state']=='INTENT':
                    i.update(state='UNSENT',accounting_pending=False,
                             reason='restart_before_send' if startup else 'orphan_before_send')
                    self.save(i,'UNSENT')
                    continue
            if i['state'] in NONTERMINAL:
                i=self.confirm(i)
                if i['state'] in NONTERMINAL and self.live:
                    self.cancel(i)
            elif i['state']=='TERMINAL' and i.get('accounting_pending'):
                self.account(i)
        self.flush()
