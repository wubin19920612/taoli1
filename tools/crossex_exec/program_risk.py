"""Program-only risk evidence and exact native-coin external debt attribution."""
from tools.fill_sim.core import dec, day
from .external import digest
from .external_fx import require_fresh_fx
from .position_values import key_currency


def journal_fingerprint(store):
    # New unfilled INTENT/SENT rows are admitted by the existing shared barrier.
    # Any subsequent fill/accounting change revokes the old income proof.
    return digest([r for r in store.iter_orders(exclude_states=('SHADOW','UNSENT'))
                   if dec(r.get('filled',0)) or r['state'] in ('TERMINAL','REJECTED')])


def journal_pnl(store, now, *, fx=None, apply_baseline=True):
    entries={}
    for r in store.iter_orders(reduce_only=False,exclude_states=('SHADOW','UNSENT')):
        if dec(r.get('filled',0)):
            key=(r['pair_id'],r['leg'])
            if key in entries or r.get('vwap') is None:
                raise ValueError('program_entry_unverifiable')
            entries[key]=dec(r['vwap'])
    total=daily=dec(0)
    for r in store.iter_orders(exclude_states=('SHADOW','UNSENT')):
        if r['state'] not in ('TERMINAL','REJECTED') or r.get('accounting_pending'):
            raise ValueError('program_accounting_pending')
        qty=dec(r.get('filled',0))
        if r.get('fee') is None or (qty and (r.get('vwap') is None or r.get('fill_ms') is None)):
            raise ValueError('program_cash_unverifiable')
        value=-dec(r['fee'])
        if qty and r['reduce_only']:
            value+=qty*(dec(r['vwap'])-entries[(r['pair_id'],r['leg'])])*(1 if r['leg']==0 else -1)
        if fx is not None and value:
            from .external import route_symbol
            from .position_values import usd_value
            coin=key_currency('|'.join((*route_symbol(r['instrument']),r['position_side'])))
            value=usd_value(value,coin,fx,now,upper=False)
        total+=value
        if day((r.get('fill_ms') or r['created_at']*1000)/1000)==day(now):
            daily+=value
    offset=store.meta('risk_pnl_baseline',{}) if apply_baseline else {}
    total-=dec(offset.get('total',0))
    if offset.get('day')==day(now):
        daily-=dec(offset.get('daily',0))
    return daily,total


def require_program_proof(store,snapshot,now):
    value=store.meta('external_income',{})
    if (value.get('verified') is not True or value.get('scope')!='program' or value.get('funding_verified') is not True
            or value.get('funding_daily_verified') is not True
            or value.get('snapshot_version')!=snapshot.get('snapshot_version')
            or value.get('snapshot_version')!=store.meta('account_snapshot_version')
            or store.meta('account_snapshot_error')
            or value.get('revision')!=store.meta('external_attribution_revision',0)
            or value.get('equity_revision')!=store.meta('equity_baseline_revision',0)
            or value.get('day')!=day(now) or not 0<=now-value.get('captured_at',0)<=10
            or value.get('program_journal')!=journal_fingerprint(store)):
        raise ValueError('external_income_unverifiable')
    require_fresh_fx(value.get('external_fx',{}),now)
    # Decimal rejects missing, NaN and infinities. Never default a loss to zero.
    dec(value['program_daily']); dec(value['program_total'])
    return value


def program_notionals(store,snapshot,now):
    from .ownership import ProgramOwnership, journal_positions
    projection=ProgramOwnership(store,lambda:now).split(snapshot,pnl=False)
    owned=journal_positions(store)
    notionals={k:str(dec(v)*min(owned.get(k,dec(0)),dec(snapshot['positions'][k]))/dec(snapshot['positions'][k]))
               for k,v in snapshot['notionals'].items() if k in projection['positions'] and k in owned}
    return dict(snapshot,notionals=notionals)


def debt_attribution(store,snapshot,now):
    """Exemption requires one exact exchange/coin domain used only by externals.

    Compare native liability with native external initial margin, cross-checked
    against the asset's futures_initial_margin. Missing/ambiguous evidence blocks.
    No cross-coin conversion, equity netting or ticker matching is permitted.
    """
    debts=snapshot['liabilities']
    if not isinstance(debts,list):
        raise ValueError('account_liability_unknown')
    allowed,blocked=[],[]
    if not debts:
        return dict(external=allowed,blocking=blocked)
    from .ownership import ProgramOwnership
    registry=ProgramOwnership(store,lambda:now)
    try:
        projection=registry.split(snapshot,pnl=False)
        external=projection.get('external_positions',{})
        raw=snapshot['raw_positions']['crossex']
        assets=snapshot['raw_accounts']['crossex']['assets']
        from .accounts import remote_positions
        budgets={}
        mixed=set()
        for row in raw:
            positions=remote_positions([row],[])
            for key in positions:
                _,symbol,_=key.split('|')
                group=(symbol.split('_',1)[0],key_currency(key))
                if key not in external:
                    mixed.add(group)
                else:
                    amount=dec(row['initial_margin'])
                    if amount<0:
                        raise ValueError('external_margin_unknown')
                    budgets[group]=budgets.get(group,dec(0))+amount
        for row in store.iter_orders(states=('INTENT','SENT','ACK','UNKNOWN')):
            i=row['instrument']
            if i['market']['exchange']!='bitget':
                symbol=i['symbol']
                mixed.add((symbol.split('_',1)[0],key_currency('crossex|'+symbol+'|long')))
        for debt in debts:
            try:
                group=(debt['exchange_type'],debt['coin'])
                amount=dec(debt['liability'])
                matched=[a for a in assets if (a['exchange_type'],a['coin'])==group]
                if (debt.get('route')!='crossex' or debt.get('source_field')!='liability'
                        or group in mixed or amount<=0 or amount>budgets.get(group,dec(0))
                        or len(matched)!=1 or dec(matched[0]['liability'])!=amount
                        or dec(matched[0]['futures_initial_margin'])!=budgets[group]):
                    raise ValueError('external_debt_unverifiable')
                allowed.append(dict(debt,external_initial_margin=str(budgets[group])))
            except (KeyError,TypeError,ValueError,ArithmeticError):
                blocked.append(debt)
    except (KeyError,TypeError,ValueError,ArithmeticError):
        return dict(external=[],blocking=debts)
    return dict(external=allowed,blocking=blocked)
