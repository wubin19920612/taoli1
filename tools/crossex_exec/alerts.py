"""Durable per-reason incident notifications; observation churn is not a state change."""
import json
from math import isfinite
from .external import digest


RETRY_SECONDS = 300


def _dedup_state(store,key,signature,now,new_incident,repeat_after,retry_on_failure=False):
    states={}
    try:
        value=store.meta('alert_states',{})
        if not isinstance(value,dict):
            raise ValueError('invalid_alert_states')
        for name,state in value.items():
            try:
                # A corrupt sibling (e.g. an overflowing JSON timestamp) must
                # not poison writes by this alert or later blocking alerts.
                json.dumps(state,allow_nan=False)
            except (TypeError,ValueError,OverflowError):
                continue
            states[name]=state
        previous=states.get(key)
        if previous is None:
            return states,False
        if (not isinstance(previous,dict) or not isinstance(previous.get('state_hash'),str)
                or type(previous.get('at')) not in (int,float) or not isfinite(previous['at'])):
            raise ValueError('invalid_alert_state')
        if retry_on_failure and 'retry_after' in previous:
            retry_after=previous['retry_after']
            if type(retry_after) not in (int,float) or not isfinite(retry_after):
                raise ValueError('invalid_alert_retry')
            # Initial delivery leaves active_key unset, so new_incident must
            # not bypass this backoff. Expiry retries even with repeat_after=None.
            return states,previous['state_hash']==signature and now<retry_after
        duplicate=(not new_incident and previous['state_hash']==signature
                   and (repeat_after is None or 0<=now-previous['at']<repeat_after))
        return states,duplicate
    except Exception:
        # Deduplication is only a convenience. Unknown history must not abort
        # an alert or any risk action following it; retain other readable keys.
        return states,False


def emit(store,clock,reason,values,*,event_kind=None,sink=None,status=None,new_incident=False,active_key=None,cause=None,
         repeat_after=3600,status_key=None,retry_on_failure=False):
    owner=values.get('pair_id','global') if reason in ('unhedged_position','repair_stopped') else 'global'
    key=reason+'|'+owner
    if cause is not None:
        key+='|'+cause
    now=clock()
    try:
        signature=digest(status)
    except Exception:
        signature,new_incident=None,True

    def audit():
        if event_kind:
            store.event(event_kind,values)
        store.event('ALERT',dict(reason=reason,**values))

    with store.lock:
        if retry_on_failure:
            states,duplicate=_dedup_state(store,key,signature,now,new_incident,repeat_after,True)
            if duplicate:
                return
            if sink:
                try:
                    # Keep delivery outside a database transaction. Only this
                    # opt-in path defers successful incident state until delivery.
                    sink(reason)
                except Exception:
                    with store.transaction():
                        # The sink may have emitted another alert under the
                        # reentrant lock; retain its newly persisted state too.
                        states,_=_dedup_state(store,key,signature,now,new_incident,repeat_after,True)
                        audit()
                        store.event('ALERT_SINK_FAILED',{'reason':reason})
                        states[key]=dict(state_hash=signature,at=now,retry_after=now+RETRY_SECONDS)
                        store.set_meta('alert_states',states)
                    return
        with store.transaction():
            states,duplicate=_dedup_state(store,key,signature,now,new_incident,repeat_after)
            if not retry_on_failure and duplicate:
                return
            audit()
            states[key]=dict(state_hash=signature,at=now)
            store.set_meta('alert_states',states)
            if status_key:
                store.set_meta(status_key,status)
            if active_key:
                store.set_meta(active_key,True)
    if sink and not retry_on_failure:
        try:
            sink(reason)
        except Exception:
            store.event('ALERT_SINK_FAILED',{'reason':reason})
