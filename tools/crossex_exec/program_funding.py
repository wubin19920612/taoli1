"""Durable adverse-only funding intervals on mixed raw contracts.

The original merged ledger/counters are still verified before allocation. Closed
intervals charge every expense and credit no income; no quantity ratio is needed.
"""
from tools.fill_sim.core import dec
from .external import route_symbol
from .ownership import ProgramOwnership, program_contracts, journal_positions


OWNERSHIP_KEY = 'program_funding_ownership'
ADVERSE_POLICY = 'adverse_only_mixed_interval'


def _maximum_program_positions(rows):
    """An observation bound only; never authority to send a reduction."""
    net,unconfirmed={},{}
    for row in rows:
        key='|'.join((*route_symbol(row['instrument']),row['position_side']))
        lifecycle=(row['pair_id'],row.get('leg'),key)
        filled=dec(row.get('filled',0))
        net[lifecycle]=net.get(lifecycle,dec(0))+filled*(-1 if row['reduce_only'] else 1)
        if not row['reduce_only'] and row['state'] in ('SENT','ACK','UNKNOWN'):
            unconfirmed[lifecycle]=unconfirmed.get(lifecycle,dec(0))+max(dec(0),dec(row['quantity'])-filled)
    result={}
    for lifecycle,quantity in net.items():
        # A close reported before its entry already consumes some of that
        # entry's maximum possible fill. Do not hide outside inventory in it.
        key=lifecycle[2]
        result[key]=result.get(key,dec(0))+max(dec(0),quantity+unconfirmed.get(lifecycle,dec(0)))
    return result


def observe_funding_ownership(store,snapshot,at,orders):
    """Persist even when history is unavailable or income certification fails."""
    with store.transaction():
        projection=ProgramOwnership(store,lambda:at).split(snapshot,pnl=False)
        owned=journal_positions(store)
        current=list(store.iter_orders(exclude_states=('SHADOW','UNSENT')))
        stable=orders==current
        previous=store.meta(OWNERSHIP_KEY,{})
        state=dict(previous,version=3,contracts=dict(previous.get('contracts',{})))
        now=int(at*1000)
        for contract in sorted(program_contracts(store)):
            name='|'.join(contract)
            old=state['contracts'].get(name,{})
            if now<old.get('last_ms',0):
                continue  # an older in-flight read cannot erase newer evidence
            local={k:q for k,q in owned.items() if tuple(k.split('|')[:2])==contract}
            remote={k:dec(q) for k,q in projection['positions'].items() if tuple(k.split('|')[:2])==contract}
            rows=[r for r in current if route_symbol(r['instrument'])==contract]
            origin=min((int(float(r['created_at'])*1000) for r in rows),default=now)
            maximum=_maximum_program_positions(rows)
            mixed=any(q>maximum.get(k,dec(0)) for k,q in remote.items())
            pending=any(r['state'] in ('INTENT','SENT','ACK','UNKNOWN') for r in rows)
            flat=not local and not pending
            intervals=[]
            for prior in old.get('intervals',[]):
                interval=dict(prior,policy=ADVERSE_POLICY)
                first,last=interval.get('first_mixed_ms'),interval.get('observed_ms')
                start,end=interval.get('from_ms'),interval.get('to_ms')
                if ('to_ms' not in interval or type(start) is not int or type(first) is not int or type(last) is not int
                        or not start<=first<=last
                        or end is not None and (type(end) is not int or last>end)):
                    # Missing old sampling evidence cannot justify any credit.
                    # Retain the old evidence, but broaden the adverse boundary
                    # to the first durable order instead of refusing forever.
                    interval['from_ms']=origin
                    interval['legacy_from_order']=True
                    if type(end) is not int or end<origin:
                        interval['to_ms']=None
                intervals.append(interval)
            active=next((v for v in reversed(intervals) if v['to_ms'] is None),None)
            if mixed and not flat:
                if active is None:
                    start=old.get('last_clean_ms',old.get('last_ms',origin))
                    active=dict(from_ms=start,first_mixed_ms=now,to_ms=None,
                                policy=ADVERSE_POLICY)
                    intervals.append(active)
                active['observed_ms']=now
            elif stable:
                # The remaining remote quantity may be entirely external after
                # the program flattens. Also close older migrated open intervals,
                # not just the last record, so no legacy gap can strand proof.
                for interval in intervals:
                    if interval['to_ms'] is None:
                        interval['to_ms']=now
            record=dict(old,last_ms=now,intervals=intervals)
            if stable and (not mixed or flat):
                record['last_clean_ms']=now
            state['contracts'][name]=record
        if state['contracts'] and state!=previous:
            state['revision']=previous.get('revision',0)+1
            store.set_meta(OWNERSHIP_KEY,state)


def allocate_funding(ownership,route,row,at,value):
    """Finalize each closed-interval expense in full and each income as zero."""
    intervals=ownership.get('contracts',{}).get(route+'|'+row['symbol'],{}).get('intervals',[])
    matches=[v for v in intervals if v['from_ms']<=at and (v['to_ms'] is None or at<=v['to_ms'])]
    if not matches:
        return value,False
    if any(v['to_ms'] is None for v in matches):
        raise ValueError('program_funding_attribution_unverifiable')
    # Both endpoints are included, including already seen IDs and delayed cash.
    # No old uncertain ratio can turn a closed interval into a permanent stop.
    return min(value,dec(0)),True
