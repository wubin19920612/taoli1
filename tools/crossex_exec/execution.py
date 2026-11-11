"""Durable IOC lifecycle. Unknown outcomes are never automatically re-submitted."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from tools.fill_sim.core import dec
from .adapters import validate_intent
from .transport import APIError


class Executor:
    def __init__(self, store, adapters, *, live=False, poll_interval=.2, confirm_timeout=10, can_open=lambda: True, check_order=None, alert=None):
        self.store,self.adapters,self.live=store,adapters,live
        self.poll_interval,self.confirm_timeout=poll_interval,confirm_timeout
        self.can_open=can_open
        self.check_order=check_order
        self.alert=alert or (lambda reason:self.store.set_meta('stop_reason',reason))
        self.locks,self.lock= {},threading.Lock()

    def adapter(self,i):
        return self.adapters['bitget' if i['instrument']['market']['exchange']=='bitget' else 'crossex']

    def execute_pair(self, intents):
        # Commit BOTH IDs before either worker may send.
        for i in intents:
            self.prepare(i)
        if self.live and self.check_order and all(self.store.order(i['cid'])['state']=='INTENT' for i in intents):
            try:
                for i in intents:
                    self.check_order(i)
            except Exception:
                results=[]
                for i in intents:
                    row=self.store.order(i['cid'])
                    row.update(state='UNSENT',accounting_pending=False,reason='pair_risk_admission')
                    self.store.save_order(row,'UNSENT')
                    results.append(row)
                return results
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='ioc') as pool:
            jobs=[pool.submit(self.execute,i) for i in intents]
            return [f.result() for f in jobs]

    def prepare(self,i):
        validate_intent(i)
        with self.lock:
            existing=self.store.order(i['cid'])
            if existing is None:
                i=dict(i,state='INTENT',filled='0',oid=None,accounting_pending=True)
                self.store.save_order(i,'INTENT')
                return i
            return existing

    def execute(self,intent):
        with self.lock:
            mutex=self.locks.setdefault(intent['cid'],threading.Lock())
        with mutex:
            i=self.prepare(intent)
            if i['state'] in ('TERMINAL','REJECTED','SHADOW','UNSENT'):
                return i
            if not self.live:
                if i['state']!='INTENT':
                    # A prior live attempt cannot become a shadow success after restart.
                    return self.confirm(i)
                i.update(state='SHADOW',accounting_pending=False)
                self.store.save_order(i,'SHADOW')
                return i
            if i['state']!='INTENT':
                return self.confirm(i)
            adapter=self.adapter(i)
            def sent():
                if self.check_order:
                    self.check_order(i)
                if not i['reduce_only'] and not self.can_open():
                    raise APIError('rejected')
                i.update(state='SENT',sent_ms=time.time()*1000)
                self.store.save_order(i,'SENT')
            try:
                if self.check_order:
                    self.check_order(i)  # known breaches never reach adapter.place
                oid=adapter.place(i,before_send=sent)
                i.update(oid=oid,state='ACK',ack_ms=time.time()*1000)
                self.store.save_order(i,'ACK')
            except Exception as error:
                kind=error.kind if isinstance(error,APIError) else 'unknown'
                if i['state']=='INTENT':
                    i.update(state='UNSENT',accounting_pending=False,reason=kind)
                    self.store.save_order(i,'UNSENT')
                    return i
                if kind=='rejected':
                    i.update(state='REJECTED',accounting_pending=False,reason=kind)
                    self.store.save_order(i,'REJECTED')
                    return i
                i.update(state='UNKNOWN',reason=kind)
                self.store.save_order(i,'UNKNOWN')
            return self.confirm(i)

    def confirm(self,i):
        adapter=self.adapter(i)
        deadline=time.monotonic()+self.confirm_timeout
        while time.monotonic()<deadline:
            try:
                status=adapter.detail(i,deadline=deadline)
                if not i.get('oid'):
                    i.update(oid=status['oid'],state='ACK',ack_ms=time.time()*1000)
                    self.store.save_order(i,'ACK')
                i.update(venue_state=status['venue_state'],filled=status['filled'],rejected=status['rejected'])
                if status.get('average_price') is not None:
                    i['average_price']=status['average_price']
                if status['terminal']:
                    i.update(state='TERMINAL',terminal_ms=time.time()*1000)
                    self.store.save_order(i,'TERMINAL')
                    return self.account(i)
            except Exception:
                pass
            time.sleep(min(self.poll_interval,max(0,deadline-time.monotonic())))
        i.update(state='UNKNOWN',reason='confirmation_deadline')
        self.store.save_order(i,'UNKNOWN')
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
        self.store.save_order(i,'FILLS' if not i['accounting_pending'] else 'FILLS_PENDING')
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

    def recover(self):
        for i in self.store.all_orders():
            if i['state'] in ('SENT','ACK','UNKNOWN'):
                self.cancel(i) if self.live else self.confirm(i)
            elif i['state']=='INTENT':
                # No automatic stale-entry send after process death.
                i.update(state='UNSENT',accounting_pending=False,reason='restart_before_send')
                self.store.save_order(i,'UNSENT')
            elif i['state']=='TERMINAL' and i.get('accounting_pending'):
                self.account(i)
