"""Zero-fill evidence for a definitive placement rejection, never UNKNOWN."""
import json

from tools.fill_sim.core import dec
from .external import digest


def rejection_identity(row):
    return dict({k:row[k] for k in ('cid','pair_id','instrument','quantity','price',
                                   'side','position_side','reduce_only','created_at','sent_persisted_ms')},
                leg=row.get('leg'))


def empty_rejection(row):
    try:
        return (row['state']=='REJECTED' and row['reason']=='rejected'
                and dec(row['filled'])==0 and row.get('oid') is None
                and row.get('vwap') is None and row.get('fill_ms') is None
                and not row.get('trade_ids') and not row.get('fill_observed_at')
                and row.get('fee') in (None,'0') and row.get('accounting_pending') is False
                and dec(row['sent_persisted_ms'])>0)
    except (KeyError,ValueError,TypeError):
        return False


def zero_rejection(row, source):
    if not empty_rejection(row):
        raise ValueError('rejection_evidence_invalid')
    return dict(row,filled='0',vwap=None,fee='0',fill_ms=None,trade_ids=[],accounting_pending=False,
                zero_fill_evidence=dict(source=source,identity=digest(rejection_identity(row))))


def legacy_rejection(store,row):
    """Upgrade only a matching SENT -> explicit REJECTED immutable audit trail.

    A hand-edited state, accepted order, positive fill, unknown response or absent
    audit is insufficient. This function performs no transport calls or writes.
    """
    if not empty_rejection(row):
        return None
    try:
        identity=rejection_identity(row)
        sent=rejected=False
        for event in store.order_events(row['cid']):
            value=json.loads(event['value'])
            if event['kind']=='INTENT' and not sent:
                continue
            if (event['kind'] not in ('SENT','REJECTED') or rejection_identity(value)!=identity
                    or dec(value['filled'])!=0 or value.get('oid') is not None):
                return None
            if event['kind']=='SENT':
                if sent or rejected or value['state']!='SENT':
                    return None
                sent=True
            else:
                if not sent or rejected or not empty_rejection(value) or value!=row:
                    return None
                rejected=True
        return zero_rejection(row,'durable_rejection_audit') if sent and rejected else None
    except (ValueError,KeyError,TypeError,ArithmeticError):
        return None
