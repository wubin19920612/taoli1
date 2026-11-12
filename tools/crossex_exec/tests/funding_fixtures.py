"""Independent position-response shapes for the older synthetic scenarios.

Older scenarios describe settlement truth in their full in-memory cash lists.
Build position counters from that truth, before the requested/visible history
window is selected. R15 delay tests use their own independently mutated counters.
No production fallback or test assertion is installed by this helper.
"""
import copy
from collections import defaultdict

from tools.fill_sim.core import dec
from tools.crossex_exec.accounts import remote_positions
from tools.crossex_exec.external import route_symbol


def enrich(owner,snapshot,cross_book=(),bitget_book=(),trades=()):
    prior=getattr(owner,'fixture_funding_positions',{})
    funds=defaultdict(lambda:dec(0))
    settlements=defaultdict(lambda:dec(0))
    times=defaultdict(list)
    for route,rows in (('crossex',cross_book),('bitget',bitget_book)):
        for row in rows:
            kind=row['statement_type' if route=='crossex' else 'type']
            if kind in ('FUNDING_FEE','CONTRACT_MAIN_SETTLE_FEE_USER_IN','CONTRACT_MAIN_SETTLE_FEE_USER_OUT'):
                key=(route,row['symbol'])
                at=int(row['create_time' if route=='crossex' else 'ts'])
                if at<=int(owner.now*1000):
                    funds[key]+=dec(row['change']) if route=='crossex' else dec(row['amount'])+dec(row['fee'])
                    settlements[(*key,at)]+=dec(row['change']) if route=='crossex' else dec(row['amount'])+dec(row['fee'])
                    times[key].append(at)
    fills=defaultdict(list)
    for order in owner.store.iter_orders(exclude_states=('SHADOW','UNSENT')):
        if dec(order.get('filled',0)) and order.get('fill_ms') is not None:
            key='|'.join((*route_symbol(order['instrument']),order['position_side']))
            fills[key].append(int(order['fill_ms']))
    for trade in trades:
        if trade.get('position_side') in ('LONG','SHORT'):
            fills['crossex|'+trade['symbol']+'|'+trade['position_side'].lower()].append(int(trade['create_time']))
    current={}
    for route,rows in snapshot['raw_positions'].items():
        for row in rows:
            normalized=remote_positions([row],[]) if route=='crossex' else remote_positions([],[row])
            for key in normalized:
                current[key]=row
    all_keys=set(prior)|set(current)|set(fills)
    assigned=set()
    for key in sorted(all_keys):
        route,symbol,side=key.split('|')
        group=(route,symbol)
        old=prior.get(key,{})
        created=old.get('created',min(fills.get(key) or times.get(group) or [int(owner.now*1000)]))
        value=funds[group] if group not in assigned else dec(0)
        assigned.add(group)
        closed=None if key in current else max(fills.get(key,[])+times.get(group,[])+[old.get('closed') or int(owner.now*1000)])
        row=current.get(key,{})
        if route=='crossex':
            evidence=dict(symbol=symbol,position_side=side.upper(),position_id=key.replace('|','~'),
                          create_time=str(created),funding_fee=str(value),funding_time='0')
            if closed is not None:
                evidence.update(closed_type='COMPLETE_CLOSED',update_time=str(closed))
        else:
            evidence=dict(symbol=symbol,posSide=side,category='USDT-FUTURES',marginCoin='USDT',
                          createdTime=str(created),totalFunding=str(value))
            if closed is not None:
                evidence.update(positionId=key,updatedTime=str(closed))
        row.update(evidence)
        prior[key]=dict(created=created,closed=closed,row=copy.deepcopy(evidence))
    owner.fixture_funding_positions=prior
    owner.fixture_synthetic_funding=[]
    for (route,symbol,at),amount in settlements.items():
        if any(k.split('|')[:2]==[route,symbol] for k in all_keys):
            continue
        if route=='crossex':
            row=dict(symbol=symbol,position_side='LONG',position_id='settled-'+str(at),
                create_time=str(at),update_time=str(at),closed_type='COMPLETE_CLOSED',funding_fee=str(amount))
        else:
            row=dict(symbol=symbol,posSide='long',positionId='settled-'+str(at),category='USDT-FUTURES',
                marginCoin='USDT',createdTime=str(at),updatedTime=str(at),totalFunding=str(amount))
        owner.fixture_synthetic_funding.append((route,at,row))
    return snapshot


def histories(owner,start,end):
    result=dict(crossex_position_history=[],bitget_position_history=[])
    for key,value in getattr(owner,'fixture_funding_positions',{}).items():
        at=value['closed']
        if at is not None and start<=at<=end:
            result[key.split('|')[0]+'_position_history'].append(copy.deepcopy(value['row']))
    for route,at,row in getattr(owner,'fixture_synthetic_funding',[]):
        if start<=at<=end:
            result[route+'_position_history'].append(copy.deepcopy(row))
    return result
