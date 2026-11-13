"""Durable per-reason incident notifications; observation churn is not a state change."""
from .external import digest


def emit(store,clock,reason,values,*,event_kind=None,sink=None,status=None,new_incident=False,active_key=None,cause=None):
    owner=values.get('pair_id','global') if reason in ('unhedged_position','repair_stopped') else 'global'
    key=reason+'|'+owner
    if cause is not None:
        key+='|'+cause
    now=clock()
    signature=digest(status)
    with store.transaction():
        states=store.meta('alert_states',{})
        previous=states.get(key)
        if (not new_incident and previous and previous.get('state_hash')==signature
                and 0<=now-previous['at']<3600):
            return
        if event_kind:
            store.event(event_kind,values)
        store.event('ALERT',dict(reason=reason,**values))
        states[key]=dict(state_hash=signature,at=now)
        store.set_meta('alert_states',states)
        if active_key:
            store.set_meta(active_key,True)
    if sink:
        try:
            sink(reason)
        except Exception:
            store.event('ALERT_SINK_FAILED',{'reason':reason})
