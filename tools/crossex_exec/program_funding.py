"""Durable observed quantity bounds for funding on mixed raw contracts.

The original merged ledger/counters are still verified before allocation. Sampled
ownership is an estimate, never an exact historical position reconstruction.
"""
from tools.fill_sim.core import dec
from .external import route_symbol
from .ownership import ProgramOwnership, program_contracts, journal_positions


OWNERSHIP_KEY = 'program_funding_ownership'


def observe_funding_ownership(store,snapshot,at,orders):
    """Persist even when history is unavailable or income certification fails."""
    with store.transaction():
        projection=ProgramOwnership(store,lambda:at).split(snapshot,pnl=False)
        owned=journal_positions(store)
        current=list(store.iter_orders(exclude_states=('SHADOW','UNSENT')))
        stable=orders==current
        previous=store.meta(OWNERSHIP_KEY,{})
        state=dict(previous,version=2,contracts=dict(previous.get('contracts',{})))
        now=int(at*1000)
        for contract in sorted(program_contracts(store)):
            name='|'.join(contract)
            old=state['contracts'].get(name,{})
            if now<old.get('last_ms',0):
                continue  # an older in-flight read cannot erase newer evidence
            local={k:q for k,q in owned.items() if tuple(k.split('|')[:2])==contract}
            remote={k:dec(q) for k,q in projection['positions'].items() if tuple(k.split('|')[:2])==contract}
            mixed=any(q>local.get(k,dec(0)) for k,q in remote.items())
            pending=any(route_symbol(r['instrument'])==contract and r['state'] in ('INTENT','SENT','ACK','UNKNOWN')
                        for r in current)
            intervals=[dict(v) for v in old.get('intervals',[])]
            active=intervals[-1] if intervals and intervals[-1]['to_ms'] is None else None
            if mixed:
                # A net cash row cannot be split by quantities across opposing
                # sides. Unknown entry fills likewise supply no ratio proof.
                certain=stable and not pending and len(remote)==1 and set(local)<=set(remote)
                ratio=dict(owned=str(sum(local.values(),dec(0))),remote=str(sum(remote.values(),dec(0))))
                if active is None:
                    origin=min((int(float(r['created_at'])*1000) for r in current
                                if route_symbol(r['instrument'])==contract),default=now)
                    start=old.get('last_ms',origin)
                    # Already verified rows at the inclusive boundary keep their
                    # previous allocation; late rows at that millisecond do not.
                    history=store.meta('program_verified_history',{}).get(contract[0]+'_book',{})
                    active=dict(from_ms=start,first_mixed_ms=now,to_ms=None,
                                lower=ratio,upper=ratio,certain=certain,
                                prior_ids=sorted(k for k,v in history.items() if v['at']>=start))
                    intervals.append(active)
                else:
                    active['certain']=active['certain'] and certain
                    for bound,sign in (('lower',-1),('upper',1)):
                        old_ratio=active[bound]
                        delta=dec(ratio['owned'])*dec(old_ratio['remote'])-dec(old_ratio['owned'])*dec(ratio['remote'])
                        if delta*sign>0:
                            active[bound]=ratio
                active['observed_ms']=now
            elif active is not None and local==remote and stable and not pending:
                active['to_ms']=now
            state['contracts'][name]=dict(last_ms=now,intervals=intervals)
        if state['contracts'] and state!=previous:
            state['revision']=previous.get('revision',0)+1
            store.set_meta(OWNERSHIP_KEY,state)


def allocate_funding(ownership,route,row,at,value):
    """Choose adverse observed shares per row, before summing opposite cash."""
    intervals=ownership.get('contracts',{}).get(route+'|'+row['symbol'],{}).get('intervals',[])
    matches=[v for v in intervals if v['from_ms']<=at and (v['to_ms'] is None or at<=v['to_ms'])
             and row['id'] not in v['prior_ids']]
    if not matches:
        return value,False
    values=[]
    for interval in matches:
        if not interval['certain'] or (value and at==interval['to_ms']):
            # At the clean boundary the cash and quantity observations have no
            # sub-millisecond ordering proof. Do not discount a new program loss.
            raise ValueError('program_funding_attribution_unverifiable')
        first,last=interval.get('first_mixed_ms'),interval.get('observed_ms')
        if (type(first) is not int or type(last) is not int
                or not interval['from_ms']<=first<=last
                or interval['to_ms'] is not None and last>interval['to_ms']):
            # Legacy intervals did not retain their first mixed observation.
            # A later sample cannot reconstruct that missing entry boundary.
            raise ValueError('program_funding_attribution_unverifiable')
        ratio=interval['upper' if value<0 else 'lower']
        own,total=dec(ratio['owned']),dec(ratio['remote'])
        if not 0<=own<total:
            raise ValueError('program_funding_attribution_unverifiable')
        # The first/last mixed observations bracket only the mixed interior.
        # Between clean and mixed samples the program may pay the entire fee.
        # Include the mixed sample millisecond: cash has no sub-ms ordering.
        # Keep the original from_ms/prior-ID rule and clean-end refusal above;
        # positive cash always takes the smallest observed mixed share.
        gap=interval['from_ms']<at<=first or last<=at
        values.append(value if value<0 and gap else value*own/total)
    return min(values),True
