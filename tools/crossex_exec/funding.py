"""Independent lifetime funding counters checked against contract cash ledgers.

Cash balances only constrain the sum of a venue/coin domain. They cannot prove
that offsetting funding on two different contracts was returned by the ledger.
Current and fully closed position counters supply the second observation.
"""
from collections import defaultdict

from tools.fill_sim.core import dec
from .accounts import remote_positions
from .position_values import position_identity
from .external import route_symbol


def milliseconds(value):
    if isinstance(value,bool):
        raise ValueError('funding_time_invalid')
    value=dec(value)
    if value<0 or value!=value.to_integral_value():
        raise ValueError('funding_time_invalid')
    return int(value)


def funding_positions(snapshot):
    """Preserve direction and lifetime; do not sum partial-close history twice."""
    activity=snapshot['activity']
    if activity['complete'] is not True:
        raise ValueError('funding_history_incomplete')
    cutoff=activity['to_ms']
    result={}
    for route in ('crossex','bitget'):
        current=snapshot['raw_positions'][route]
        history=activity[route+'_position_history']
        if not isinstance(current,list) or not isinstance(history,list):
            raise ValueError('funding_history_incomplete')
        for closed,rows in ((False,current),(True,history)):
            for row in rows:
                i=position_identity(route,row['symbol'],row)
                if closed:
                    if route=='crossex':
                        if row['closed_type']=='PARTIAL_CLOSED':
                            continue  # the live counter (or final close) includes it
                        if row['closed_type']!='COMPLETE_CLOSED':
                            raise ValueError('funding_close_unknown')
                    side=row['position_side'].lower() if route=='crossex' else row['posSide']
                    if side not in ('long','short'):
                        raise ValueError('funding_direction_unknown')
                    position_key='|'.join((route,row['symbol'],side))
                else:
                    keys=remote_positions([row],[]) if route=='crossex' else remote_positions([],[row])
                    if not keys:
                        continue
                    position_key=next(iter(keys))
                created=milliseconds(row['create_time' if route=='crossex' else 'createdTime'])
                end=milliseconds(row['update_time' if route=='crossex' else 'updatedTime']) if closed else None
                if created>cutoff or (closed and not created<=end<=cutoff):
                    raise ValueError('funding_lifetime_unknown')
                # CrossEx supplies a stable position ID. UTA current-position
                # supplies createdTime, also present in its historical rows.
                token=row['position_id'] if route=='crossex' else str(created)
                if not isinstance(token,str) or not token or '|' in token:
                    raise ValueError('funding_lifetime_unknown')
                raw=row['funding_fee' if route=='crossex' else 'totalFunding']
                if isinstance(raw,bool):
                    raise ValueError('funding_counter_unknown')
                value=dec(raw)
                settled=None
                if route=='crossex' and not closed and 'funding_time' in row:
                    settled=milliseconds(row['funding_time'])
                    if settled>cutoff or (settled and settled<created) or (settled==0 and value):
                        raise ValueError('funding_collection_time_invalid')
                key=position_key+'|'+token
                if key in result:
                    raise ValueError('funding_lifetime_duplicate')
                result[key]=dict(position_key=position_key,contract='|'.join((route,i['symbol'],i['quote'])),
                                 created_ms=created,closed_ms=end,value=str(value),settled_ms=settled)
    return result


def funding_continuity(store,anchor,snapshot):
    # Missing legacy collection clocks are UNKNOWN, never a synthetic zero.
    # Normalize the optional field for immutable finalized-counter comparison.
    before={k:dict(row,settled_ms=row.get('settled_ms'))
            for k,row in anchor['funding_positions'].items()}
    after=funding_positions(snapshot)
    activity=snapshot['activity']
    # Local wall clocks have sub-millisecond precision; use the same inclusive
    # integer boundary as the fixed-window account-book requests.
    epoch=int(anchor['captured_at']*1000)
    for key,row in before.items():
        if key not in after:
            if row['closed_ms'] is None or row['closed_ms']>=activity['from_ms']:
                raise ValueError('funding_lifetime_missing')
            after[key]=row  # finalized before the requested window
        elif (any(after[key][k]!=row[k] for k in ('position_key','contract','created_ms'))
              or row['closed_ms'] is not None and after[key]!=row):
            raise ValueError('funding_lifetime_revised')
    for key,row in after.items():
        if key not in before and row['created_ms']<epoch:
            raise ValueError('funding_lifetime_unanchored')
    # A position opened and closed between reads must still have a final
    # independent witness. An empty position response is not proof of zero fees.
    for row in store.iter_orders(exclude_states=('SHADOW','UNSENT')):
        if not dec(row.get('filled',0)):
            continue
        at=milliseconds(row['fill_ms'])
        if at<epoch:
            continue
        key='|'.join((*route_symbol(row['instrument']),row['position_side']))
        if not any(r['position_key']==key and r['created_ms']<=at
                   and (r['closed_ms'] is None or at<=r['closed_ms']) for r in after.values()):
            raise ValueError('program_funding_lifetime_missing')
    expected,actual=defaultdict(lambda:dec(0)),defaultdict(lambda:dec(0))
    for rows,sign in ((after,1),(before,-1)):
        for row in rows.values():
            expected[row['contract']]+=sign*dec(row['value'])
    for route,name in (('crossex','crossex_book'),('bitget','bitget_book')):
        for row in activity[name]:
            if row['id'] in anchor['history_index'][name]:
                continue
            kind=row['statement_type' if route=='crossex' else 'type']
            if kind not in ('FUNDING_FEE','CONTRACT_MAIN_SETTLE_FEE_USER_IN','CONTRACT_MAIN_SETTLE_FEE_USER_OUT'):
                continue
            i=position_identity(route,row['symbol'],row)
            if row['coin']!=i['quote']:
                raise ValueError('funding_currency_mismatch')
            key='|'.join((route,row['symbol'],row['coin']))
            value=dec(row['change']) if route=='crossex' else dec(row['amount'])+dec(row['fee'])
            actual[key]+=value
    if {k:v for k,v in expected.items() if v}!={k:v for k,v in actual.items() if v}:
        raise ValueError('funding_counter_ledger_mismatch')
    return after


def daily_continuity(store,anchor,snapshot):
    """Independent cumulative increments must fit ONE UTC day, before netting.

    CrossEx's live collection time bounds the last settlement (0 means none).
    A snapshot after midnight with collection time before midnight therefore
    witnesses the boundary counter. A net-zero multi-day gap is NOT evidence.
    UTA and closed histories have no such clock: their lifetime/observation
    bounds must fit one day, otherwise retain the old epoch and stop opening.
    """
    previous=store.meta('funding_checkpoint')
    revision=store.meta('equity_baseline_revision',0)
    if (not previous or previous.get('revision')!=anchor['revision']
            or previous.get('equity_revision')!=revision
            or previous['captured_at']<anchor['captured_at']):
        previous=anchor
    epoch=int(previous['captured_at']*1000)
    activity=snapshot['activity']
    if epoch>activity['to_ms']:
        raise ValueError('funding_checkpoint_future')
    after=funding_continuity(store,previous,snapshot)
    before=previous['funding_positions']
    expected,actual=defaultdict(lambda:dec(0)),defaultdict(lambda:dec(0))
    for key,row in after.items():
        old=before.get(key)
        delta=dec(row['value'])-dec(old['value'] if old else 0)
        if old and old['closed_ms'] is not None:
            continue  # immutable, independently finalized before this checkpoint
        lower=max(epoch,row['created_ms'])
        upper=row['closed_ms'] if row['closed_ms'] is not None else activity['to_ms']
        settled=row.get('settled_ms')
        if settled is not None:
            if old and old.get('settled_ms') is not None and settled<old['settled_ms']:
                raise ValueError('funding_collection_time_revised')
            upper=settled
        if upper<lower or settled==0:
            if delta:
                raise ValueError('funding_collection_counter_conflict')
            continue
        if lower//86400000!=upper//86400000:
            raise ValueError('funding_utc_boundary_unproven')
        expected[(row['contract'],upper//86400000)]+=delta
    for route,name in (('crossex','crossex_book'),('bitget','bitget_book')):
        for row in activity[name]:
            if row['id'] in previous['history_index'][name]:
                continue
            kind=row['statement_type' if route=='crossex' else 'type']
            if kind not in ('FUNDING_FEE','CONTRACT_MAIN_SETTLE_FEE_USER_IN','CONTRACT_MAIN_SETTLE_FEE_USER_OUT'):
                continue
            at=milliseconds(row['create_time' if route=='crossex' else 'ts'])
            if at<epoch:
                raise ValueError('funding_late_checkpoint_history')
            contract='|'.join((route,row['symbol'],row['coin']))
            value=dec(row['change']) if route=='crossex' else dec(row['amount'])+dec(row['fee'])
            actual[(contract,at//86400000)]+=value
    if {k:v for k,v in expected.items() if v}!={k:v for k,v in actual.items() if v}:
        raise ValueError('funding_daily_ledger_mismatch')
    return dict(captured_at=snapshot.get('accounting_at',snapshot['captured_at']),
        revision=anchor['revision'],equity_revision=revision,funding_positions=after)
