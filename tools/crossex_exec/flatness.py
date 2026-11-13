"""Shared local/remote flat proof for halt completion and audited release."""
from tools.fill_sim.core import dec
from .ownership import journal_positions


FLATTEN_LOCKS = frozenset(('flatten_retry_pending','flatten_snapshot_unavailable'))
FINAL_FLAT_LOCKS = FLATTEN_LOCKS | frozenset(('unknown_order','unknown_close','accounting_pending',
    'fills_pending','entry_accounting_unknown','negative_local_position','close_incomplete',
    'unknown_exposure_unavailable'))


def require_local_flat(store, executor=None):
    if executor and (executor.pending_writes or executor.owned):
        raise ValueError('local_orders_pending')
    for row in store.iter_orders():
        if (row.get('state') not in ('TERMINAL','REJECTED','SHADOW','UNSENT')
                or row.get('accounting_pending',False)):
            raise ValueError('local_orders_pending')
    # Per-lifecycle net fills cannot cancel a different pair's missing leg.
    try:
        positions=journal_positions(store)
    except ValueError:
        raise ValueError('local_positions') from None
    if positions:
        raise ValueError('local_positions')
    for pair in store.iter_pairs(exclude_states=('SHADOW','SKIPPED')):
        if (pair['state']!='CLOSED' or pair.get('remote_pending') or pair.get('accounting_pending')
                or any(dec(q) for field in ('remaining','remote_remaining') for q in pair.get(field,()))):
            raise ValueError('local_positions')


def flat_projection(registry, snapshot, *, program_scope, executor=None, pnl=True):
    """Exclude residual user quantity only AFTER every program lifecycle is flat.

    This is an observation, never a change to contract claims or close authority.
    ProgramOwnership.check_order continues to reject the mixed raw contract.
    """
    require_local_flat(registry.store,executor)
    latest=max((max(float(row.get('fill_observed_at',0)),float(row.get('fill_ms') or 0)/1000,
                    float(row.get('terminal_ms') or 0)/1000)
                for row in registry.store.iter_orders(exclude_states=('SHADOW','UNSENT'))
                if dec(row.get('filled',0))>0),default=0)
    if snapshot['captured_at']<latest:
        raise ValueError('account_snapshot_stale')
    projection=registry.split(snapshot,pnl=pnl)
    if program_scope:
        projection=dict(projection,positions={},external_positions=dict(snapshot['positions']))
        if pnl:
            projection.update(upnl='0',upnl_by_position={},
                external_upnl_by_position=dict(snapshot.get('upnl_by_position',{})))
    return projection
