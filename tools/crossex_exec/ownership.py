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
    lifecycles, unresolved = {}, {}
    for row in store.iter_orders(exclude_states=('SHADOW','UNSENT'), **(
            {'pair_id':pair_id} if pair_id is not None else {})):
        key = '|'.join((*route_symbol(row['instrument']),row['position_side']))
        lifecycle = (row['pair_id'],row.get('leg'),key)
        qty = dec(row.get('filled',0)) * (-1 if row['reduce_only'] else 1)
        lifecycles[lifecycle] = lifecycles.get(lifecycle,dec(0))+qty
        if not row['reduce_only'] and row['state'] in ('SENT','ACK','UNKNOWN'):
            unresolved[lifecycle] = unresolved.get(lifecycle,dec(0))+max(dec(0),dec(row['quantity'])-qty)
    result = {}
    for lifecycle,qty in lifecycles.items():
        if qty<0:
            if -qty>unresolved.get(lifecycle,dec(0)):
                raise ValueError('negative_local_position')
            # A confirmed close can arrive before its opening fill. Keep querying
            # that lifecycle, but assign it ZERO reduction authority meanwhile.
            qty=dec(0)
        key=lifecycle[2]
        result[key]=result.get(key,dec(0))+qty
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
    """Only confirmed net fills authorize closes; unknown opens only need queries."""
    rows = list(store.iter_orders(pair_id=pair['id'],exclude_states=('SHADOW','UNSENT')))
    owned = journal_positions(store,pair_id=pair['id'])
    for row in rows:
        if row['reduce_only'] and row['state'] in ('INTENT','SENT','ACK','UNKNOWN'):
            # An unresolved close may already have consumed this inventory.
            # Reserve its unconfirmed remainder so retries cannot spend it twice.
            key='|'.join((*route_symbol(row['instrument']),row['position_side']))
            pending=max(dec(0),dec(row['quantity'])-dec(row.get('filled',0)))
            owned[key]=max(dec(0),owned.get(key,dec(0))-pending)
    return dict(snapshot,positions={k:min(dec(q),owned.get(k,dec(0)))
                                   for k,q in snapshot['positions'].items()})
