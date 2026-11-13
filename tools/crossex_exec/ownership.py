"""Program contracts are claimed by durable orders, never by ticker aliases."""
from tools.fill_sim.core import dec
from .external import ExternalRegistry, ExternalUnverifiable, route_symbol
from .accounts import remote_positions
from .position_values import position_identity


def program_contracts(store):
    # No cache: INTENT publication and every admission read share Store.lock.
    with store.lock:
        claims={tuple(k) for k in store.meta('program_crash_claims',[])}
        return claims|{route_symbol(row['instrument']) for row in
                       store.iter_orders(exclude_states=('SHADOW','UNSENT'))}


def retain_crash_claims(store):
    # Recovery correctly retires pre-send intents as UNSENT. Preserve the
    # ownership evidence that existed at the crash without changing that path.
    with store.transaction():
        old=store.meta('program_crash_claims',[])
        claims={tuple(k) for k in old}
        claims|={route_symbol(r['instrument']) for r in store.iter_orders(states=('INTENT',))}
        value=[list(k) for k in sorted(claims)]
        if value!=old:
            store.set_meta('program_crash_claims',value)
            store.event('PROGRAM_CRASH_CLAIMS',dict(contracts=value))


def journal_positions(store, *, pair_id=None):
    result = {}
    for row in store.iter_orders(exclude_states=('SHADOW','UNSENT'), **(
            {'pair_id':pair_id} if pair_id is not None else {})):
        key = '|'.join((*route_symbol(row['instrument']),row['position_side']))
        qty = dec(row.get('filled',0)) * (-1 if row['reduce_only'] else 1)
        result[key] = result.get(key,dec(0))+qty
    if any(q<0 for q in result.values()):
        raise ValueError('negative_local_position')
    return {k:q for k,q in result.items() if q}


class ProgramOwnership(ExternalRegistry):
    def split(self,snapshot,*,pnl=True):
        with self.store.lock:
            owned = program_contracts(self.store)
            positions = snapshot['positions']
            identities={(r['identity']['route'],r['identity']['symbol']):r['identity']
                        for r in self.records().values()}
            from .cash_identity import program_contract_identity
            for order in self.store.iter_orders(exclude_states=('SHADOW','UNSENT')):
                if route_symbol(order['instrument'])[0]=='crossex':
                    identities[route_symbol(order['instrument'])]=program_contract_identity(order['instrument'])
            raw = snapshot.get('raw_positions')
            if raw is not None:
                if remote_positions(raw['crossex'],raw['bitget'])!={k:dec(v) for k,v in positions.items()}:
                    raise ExternalUnverifiable('external_identity_unverifiable')
                for route,rows in raw.items():
                    for row in rows:
                        try:
                            position_identity(route,row['symbol'],row,expected=identities.get((route,row['symbol'])))
                        except ValueError:
                            raise ExternalUnverifiable('external_identity_unverifiable') from None
            # Retain manual tombstones; impossible overlap must never relabel
            # durable program exposure as external.
            for record in self.records().values():
                identity = record['identity']
                if (identity['route'],identity['symbol']) in owned:
                    raise ExternalUnverifiable('program_ownership')
            external = {k:q for k,q in positions.items() if tuple(k.split('|')[:2]) not in owned}
            result = dict(snapshot,positions={k:q for k,q in positions.items() if k not in external},
                          external_positions=external,ownership_mode='local_order_history')
            if pnl:
                floating = snapshot.get('upnl_by_position',{})
                program = {k:floating[k] for k in result['positions']}
                result.update(upnl_by_position=program,upnl=str(sum(map(dec,program.values()),dec(0))),
                    external_upnl_by_position={k:floating.get(k) for k in external},
                    external_upnl=None)  # observation only; never a risk input
            return result

    def check_order(self,order):
        super().check_order(order)
        if order.get('reduce_only',False):
            return
        target = route_symbol(order['instrument'])
        with self.store.lock:
            snapshot = self.store.meta('account_snapshot',{})
            expected = journal_positions(self.store)
            for key,qty in snapshot.get('positions',{}).items():
                if tuple(key.split('|')[:2])==target and dec(qty)>expected.get(key,dec(0)):
                    # Also runs after fresh INTENT/SENT has claimed this symbol.
                    raise ValueError('external_contract_forbidden')


def bounded_pair_positions(store,pair,snapshot):
    """Bound resolved exposure by the journal; unresolved IOC keeps recovery.

    Unknown orders can have fills not yet in the journal. Preserve their original
    conservative remote recovery bound. Admission prevented outside positions on
    that contract before the first send; resolved fills must never absorb extras.
    """
    rows = list(store.iter_orders(pair_id=pair['id'],exclude_states=('SHADOW','UNSENT')))
    owned = journal_positions(store,pair_id=pair['id'])
    for row in rows:
        if not row['reduce_only'] and row['state'] in ('INTENT','SENT','ACK','UNKNOWN'):
            key='|'.join((*route_symbol(row['instrument']),row['position_side']))
            owned[key]=owned.get(key,dec(0))+max(dec(0),dec(row['quantity'])-dec(row.get('filled',0)))
    return dict(snapshot,positions={k:min(dec(q),owned.get(k,dec(0)))
                                   for k,q in snapshot['positions'].items()})
