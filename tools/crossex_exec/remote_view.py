"""Allowlisted, read-only remote positions from one durable account snapshot."""
from tools.fill_sim.core import dec
from .accounts import remote_positions
from .external import ExternalRegistry, route_symbol
from .position_values import position_identity, usd_value


def decimal_field(value,*,positive=False):
    if value is None or isinstance(value,bool):
        return None
    try:
        parsed=dec(value)
        return str(parsed) if not positive or parsed>0 else None
    except (ValueError,TypeError,ArithmeticError):
        return None


def remote_view(conn,budget,now):
    from .api_data import utc, number, text_field
    meta={key:budget.decode(raw) for key,raw in conn.execute(
        "SELECT key,value FROM meta WHERE key IN ('account_snapshot','account_snapshot_error','external_contracts')")}
    snapshot=meta.get('account_snapshot')
    result=dict(available=False,snapshot_at=None,snapshot_version=None,stale=True,positions=[])
    if snapshot is None:
        return result
    at=number(snapshot.get('captured_at'))
    version=snapshot.get('snapshot_version')
    if at is None or type(version) is not int or version<1:
        raise ValueError('remote_snapshot_invalid')
    stale=not 0<=now-at<=10 or bool(meta.get('account_snapshot_error'))
    result.update(available=True,snapshot_at=utc(at),snapshot_version=version,stale=stale)
    class MetaView:
        def meta(self,key,default=None):
            return meta.get(key,default)
    registry=ExternalRegistry(MetaView())
    records=registry.records()
    registered={tuple(r['identity'][k] for k in ('route','symbol')):r['identity']
                for r in records.values() if r['registered']}
    # Match the registration prohibition: any exact program contract history is
    # still program-owned, including closed pairs, orphan orders and tombstones.
    owned=set()
    for (raw,) in conn.execute('SELECT value FROM orders'):
        budget.check()
        owned.add(route_symbol(budget.decode(raw)['instrument']))
    for (raw,) in conn.execute('SELECT value FROM pairs'):
        budget.check()
        for instrument in budget.decode(raw)['instruments']:
            owned.add(route_symbol(instrument))
    raw_positions=snapshot['raw_positions']
    normalized=remote_positions(raw_positions['crossex'],raw_positions['bitget'])
    if normalized!={k:dec(v) for k,v in snapshot['positions'].items()}:
        raise ValueError('remote_snapshot_incomplete')
    for route in ('crossex','bitget'):
        for raw in raw_positions[route]:
            budget.check()
            positions=remote_positions([raw],[]) if route=='crossex' else remote_positions([],[raw])
            for key,qty in positions.items():
                symbol,side=raw['symbol'],key.rsplit('|',1)[1]
                try:
                    identity=position_identity(route,symbol,raw,expected=registered.get((route,symbol)))
                    identity_verified=True
                except (ValueError,TypeError,KeyError):
                    # Retain unsupported raw identities; never drop an exposure
                    # from the UI or authorize registration with guessed units.
                    identity=dict(route=route,symbol=symbol,exchange_type=text_field(raw.get('exchange_type')),
                        business=text_field(raw.get('business_type',raw.get('category'))),quote=None,dex=None)
                    identity_verified=False
                target=(route,symbol)
                ownership=('program' if target in owned else 'external_registered'
                           if registered.get(target)==identity else 'unregistered')
                entry=decimal_field(raw.get('entry_price' if route=='crossex' else 'avgPrice'),positive=True)
                mark=decimal_field(raw.get('mark_price' if route=='crossex' else 'markPrice'),positive=True)
                native=decimal_field(raw.get('upnl' if route=='crossex' else 'unrealisedPnl'))
                usd=None
                try:
                    if not identity_verified or native is None:
                        raise ValueError('position_value_unknown')
                    usd=str(usd_value(native,identity['quote'],snapshot.get('external_fx',{}),now,upper=False))
                except (ValueError,KeyError,TypeError,ArithmeticError):
                    pass
                result['positions'].append(dict(identity,position_key=key,side=side,quantity=str(qty),
                    margin_mode=raw.get('margin_mode','CROSS') if route=='crossex' else 'CROSS',
                    entry_price=entry,mark_price=mark,unrealized_pnl_native=native,unrealized_pnl_usd=usd,
                    valuation_verified=usd is not None,valuation_error=None if usd is not None else 'position_fx_unavailable',
                    valuation='public_spot_lower_bound' if identity['quote'] not in ('USD','USDT') else 'native_usd',
                    identity_verified=identity_verified,ownership=ownership,snapshot_at=result['snapshot_at'],
                    snapshot_version=version,stale=stale))
    result['positions'].sort(key=lambda r:r['position_key'])
    return result
