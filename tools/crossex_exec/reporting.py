"""Reports use journaled execution facts; unknown accounting remains null."""
from tools.fill_sim.core import dec


def report(store):
    trades=[]
    for pair in store.iter_pairs():
        related=list(store.iter_orders(pair_id=pair['id']))
        entry=sorted([o for o in related if not o['reduce_only']],key=lambda o:o['leg'])
        timing={}
        for field,name in (('sent_ms','send_delta_ms'),('fill_ms','fill_delta_ms')):
            values=[o.get(field) for o in entry]
            timing[name]=abs(values[1]-values[0]) if len(values)==2 and all(v is not None for v in values) else None
        slips=[]
        for o in related:
            slips.append(dict(cid=o['cid'],leg=o['leg'],reduce_only=o['reduce_only'],filled=o['filled'],
                slippage_bps=float((dec(o['vwap'])/dec(o['estimate'])-1)*10000*(1 if o['side']=='buy' else -1))
                if o.get('vwap') is not None and o.get('estimate') else None,
                fee_usdt=o.get('fee'),accounting_pending=o.get('accounting_pending',True)))
        trades.append(dict(pair_id=pair['id'],trigger_id=pair['trigger_id'],state=pair['state'],**timing,
                           single_failure=pair.get('single_failure',False),net_usd=pair.get('net_usd'),
                           funding_known=pair.get('funding_known',False),fill_sim_estimate=pair['fill'],orders=slips))
    live=[p for p in trades if p['state']!='SHADOW']
    known=[p for p in live if p['net_usd'] is not None and p.get('funding_known')]
    return dict(trades=trades,live_pairs=len(live),shadow_pairs=len(trades)-len(live),
                single_failure_rate=sum(p['single_failure'] for p in live)/len(live) if live else None,
                known_net_usd=str(sum((dec(p['net_usd']) for p in known),dec(0))),
                incomplete_accounting=sum(p['state']=='CLOSED' and (p['net_usd'] is None or not p.get('funding_known')) for p in live))
