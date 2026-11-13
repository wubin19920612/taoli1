"""Risk evidence from own executions, own funding, and own marked inventory."""
from tools.fill_sim.core import dec, day
from .external import ExternalUnverifiable, plain, route_symbol
from .external_accounting import midnight, history_index, settlement_state, BITGET_FUNDING
from .ownership import program_contracts, journal_positions, require_program_observation
from .program_risk import journal_pnl, journal_fingerprint
from .funding import funding_continuity, daily_continuity, certify_daily
from .cash_identity import cash_identity, program_contract_identity
from .position_values import key_currency, usd_value, position_identity
from .external_fx import NATIVE_USD, quote_band
from .program_funding import (ADVERSE_POLICY, OWNERSHIP_KEY, observe_funding_ownership,
                              allocate_funding, funding_flat_contracts)


def funding_view(snapshot,contracts,orders):
    """Remove outside positions and every non-funding cash flow before proof."""
    starts={}
    for order in orders:
        key=route_symbol(order['instrument'])
        at=int(float(order['created_at'])*1000)
        starts[key]=min(starts.get(key,at),at)
    def since_claim(route,row,field):
        return int(row[field])>=starts.get((route,row['symbol']),int(snapshot['program_origin']*1000))
    activity=dict(snapshot['activity'],crossex_trades=[])
    raw={route:[r for r in snapshot['raw_positions'][route] if (route,r['symbol']) in contracts]
         for route in ('crossex','bitget')}
    for route in raw:
        kinds={'FUNDING_FEE'} if route=='crossex' else BITGET_FUNDING
        activity[route+'_book']=[r for r in activity[route+'_book']
            if (route,r.get('symbol')) in contracts
            and r.get('statement_type' if route=='crossex' else 'type') in kinds
            and since_claim(route,r,'create_time' if route=='crossex' else 'ts')]
        activity[route+'_position_history']=[r for r in activity[route+'_position_history']
            if (route,r['symbol']) in contracts
            and since_claim(route,r,'update_time' if route=='crossex' else 'updatedTime')]
    return dict(snapshot,raw_positions=raw,activity=activity)


def _program_state(snapshot,contracts):
    # Independent counters/quantities must be stable across the read. External
    # cash, quantities, settlement, and account balances are intentionally absent.
    raw={route:[r for r in rows if (route,r['symbol']) in contracts]
         for route,rows in snapshot.get('raw_positions',{}).items()}
    return settlement_state(dict(raw_positions=raw,raw_accounts={}))['positions']


def read_program_snapshot(accounts,store,clock):
    with store.lock:
        contracts=program_contracts(store)
        orders=list(store.iter_orders(exclude_states=('SHADOW','UNSENT')))
        anchor=store.meta('program_funding_anchor')
        origin=min((float(r['created_at']) for r in orders),default=clock())
        start=min(origin,anchor['captured_at'] if anchor else origin,midnight(clock()))
    started=clock()
    first=accounts.snapshot()
    cutoff=clock()
    ownership_failure=None
    try:
        observe_funding_ownership(store,first,cutoff,orders)
    except (ValueError,KeyError,TypeError,ArithmeticError):
        # Keep the raw account/FX observation available for diagnostics and
        # physical protection; invalid ownership cannot certify program income.
        ownership_failure=dict(condition='program_ownership_observation_failed')
    activity=dict(from_ms=int(start*1000),to_ms=int(cutoff*1000),complete=True,
        crossex_book=[],bitget_book=[],crossex_trades=[],crossex_position_history=[],bitget_position_history=[])
    snapshot=first
    if contracts:
        try:
            activity=accounts.program_activity(contracts,int(start*1000),int(cutoff*1000))
            snapshot=accounts.snapshot()
            observe_funding_ownership(store,snapshot,clock(),orders)
            if _program_state(first,contracts)!=_program_state(snapshot,contracts):
                activity=dict(activity,complete=False,failure=dict(condition='program_settlement_changed_during_read'))
        except Exception as error:
            from .history import HistoryError, error_details
            diagnostic=error.diagnostic if isinstance(error,HistoryError) else dict(
                condition='program_funding_read_failed',**error_details(error))
            activity=dict(activity,complete=False,failure=diagnostic)
    if ownership_failure:
        activity=dict(activity,complete=False,failure=ownership_failure)
    return dict(snapshot,captured_at=started,accounting_at=cutoff,activity=activity,
                program_contracts=[list(k) for k in sorted(contracts)],program_origin=origin)


class _FundingStore:
    """Reuse strict UTC proof with a separate durable program checkpoint."""
    def __init__(self,store):
        self.store=store

    def meta(self,key,default=None):
        return self.store.meta('program_funding_checkpoint' if key=='funding_checkpoint' else key,default)

    def iter_orders(self,**kwargs):
        return self.store.iter_orders(**kwargs)


def program_floating(store,snapshot):
    entries,remaining={},{}
    for row in store.iter_orders(exclude_states=('SHADOW','UNSENT')):
        quantity=dec(row.get('filled',0))
        if not quantity:
            continue
        leg=(row['pair_id'],row['leg'])
        remaining[leg]=remaining.get(leg,dec(0))+quantity*(-1 if row['reduce_only'] else 1)
        if not row['reduce_only']:
            if leg in entries or row.get('vwap') is None:
                raise ValueError('program_entry_unverifiable')
            entries[leg]=row
    values={}
    for leg,qty in remaining.items():
        if qty<0 or leg not in entries:
            raise ValueError('program_entry_unverifiable')
        if not qty:
            continue
        row=entries[leg]
        key='|'.join((*route_symbol(row['instrument']),row['position_side']))
        remote=dec(snapshot['positions'].get(key,0))
        if remote<=0:
            raise ValueError('program_position_mark_unknown')
        mark=dec(snapshot['notionals'][key])/remote
        if mark<=0:
            raise ValueError('program_position_mark_unknown')
        values[key]=values.get(key,dec(0))+qty*(mark-dec(row['vwap']))*(1 if row['position_side']=='long' else -1)
    return values


class ProgramAccounting:
    def __init__(self,store,registry,clock):
        self.store,self.registry,self.clock=store,registry,clock
        self._history_hashes={}

    def evaluate(self,snapshot,projection,*,persist=True):
        try:
            return self._evaluate(snapshot,projection,persist=persist)
        except (ValueError,KeyError,TypeError,ArithmeticError) as error:
            failure=ExternalUnverifiable('program_income_unverifiable')
            safe={'funding_utc_boundary_unproven','funding_daily_ledger_mismatch',
                'funding_counter_ledger_mismatch','funding_lifetime_missing','program_funding_lifetime_missing',
                'funding_collection_counter_conflict','funding_late_checkpoint_history',
                'program_funding_incomplete','program_history_revised_or_missing','program_snapshot_superseded',
                'program_position_mismatch','program_funding_attribution_unverifiable',
                'program_accounting_pending','program_position_mark_unknown','program_baseline_unverifiable'}
            failure.diagnostic=dict(stage='program_income_verification',
                condition=str(error) if str(error) in safe else 'program_evidence_invalid')
            raise failure from None

    def _evaluate(self,snapshot,projection,*,persist=True):
        store=self.store
        now=self.clock()
        captured=snapshot['captured_at']
        cutoff=snapshot.get('accounting_at',captured)
        contracts=program_contracts(store)
        if snapshot.get('program_contracts')!=[list(k) for k in sorted(contracts)]:
            raise ValueError('program_snapshot_superseded')
        view=funding_view(snapshot,contracts,store.iter_orders(exclude_states=('SHADOW','UNSENT')))
        activity=view['activity']
        if (activity['complete'] is not True or not 0<=now-captured<=10
                or day(cutoff)!=day(now) or activity['to_ms']!=int(cutoff*1000)):
            raise ValueError('program_funding_incomplete')
        revision=store.meta('equity_baseline_revision',0)
        ownership=store.meta(OWNERSHIP_KEY,{})
        original_journal=journal_fingerprint(store)
        daily,realized=journal_pnl(store,cutoff,fx=snapshot.get('external_fx',{}))
        flat=funding_flat_contracts(store)
        if {k:dec(q) for k,q in projection['positions'].items() if tuple(k.split('|')[:2]) not in flat}!=journal_positions(store):
            # Keep openings stopped while quantities disagree. Durable ownership
            # intervals survive this failure and still apply once extras leave.
            raise ValueError('program_position_mismatch')
        index=history_index(activity,self._history_hashes)
        previous=store.meta('program_verified_history',{})
        for name,rows in previous.items():
            for key,row in rows.items():
                if (key in index[name] or row['at']>=activity['from_ms']) and index[name].get(key)!=row:
                    raise ValueError('program_history_revised_or_missing')
        anchor=store.meta('program_funding_anchor')
        original_anchor=anchor
        original_checkpoint=store.meta('program_funding_checkpoint')
        if anchor is None:
            anchor=certify_daily(dict(captured_at=snapshot['program_origin'],revision=0,
                equity_revision=revision,funding_positions={},history_index={k:{} for k in index}),
                'initial_flat',revision)
        if activity['from_ms']>int(anchor['captured_at']*1000):
            raise ValueError('program_funding_incomplete')
        funding_continuity(store,anchor,view)
        checkpoint=daily_continuity(_FundingStore(store),anchor,view)
        checkpoint['history_index']=index
        checkpoint=certify_daily(checkpoint,'daily_continuity',revision)
        identities={route_symbol(r['instrument']):program_contract_identity(r['instrument'])
            for r in store.iter_orders(exclude_states=('SHADOW','UNSENT'))
            if route_symbol(r['instrument'])[0]=='crossex'}
        funding=daily_funding=dec(0)
        estimated=False
        required=set(snapshot.get('margin_coverage',{}).get('currencies',()))-NATIVE_USD
        required|={key_currency('|'.join((*route_symbol(r['instrument']),r['position_side'])))
                   for r in store.iter_orders(exclude_states=('SHADOW','UNSENT')) if dec(r.get('filled',0))}
        for route in ('crossex','bitget'):
            for row in activity[route+'_book']:
                if route=='crossex':
                    cash_identity(row,identities[(route,row['symbol'])])
                    value=dec(row['change']); at=int(row['create_time'])
                else:
                    identity=position_identity(route,row['symbol'],row)
                    value=dec(row['amount'])+dec(row['fee']); at=int(row['ts'])
                    if (row['coin']!=identity['quote'] or dec(row['fee'])>0
                            or row['type'].endswith('_IN') and value<0
                            or row['type'].endswith('_OUT') and value>0):
                        raise ValueError('program_funding_identity')
                coin=row['coin']
                required.add(coin)
                value,allocated=allocate_funding(ownership,route,row,at,value)
                estimated=estimated or allocated
                value=usd_value(value,coin,snapshot.get('external_fx',{}),now,upper=False)
                funding+=value
                if day(at/1000)==day(cutoff):
                    daily_funding+=value
        offset=store.meta('program_funding_baseline',{})
        total_funding=funding-dec(offset.get('total',0))
        if offset.get('day')==day(cutoff):
            daily_funding-=dec(offset.get('daily',0))
        amounts=program_floating(store,snapshot)
        floating=dec(0)
        for key,value in amounts.items():
            coin=key_currency(key); required.add(coin)
            floating+=usd_value(value,coin,snapshot.get('external_fx',{}),now,upper=False)
        baseline=store.meta('program_daily_upnl')
        last=store.meta('program_last_upnl')
        require_program_observation(store,baseline)
        require_program_observation(store,last)
        if not baseline:
            # No external anchor is needed. A new program starts from zero;
            # historical open inventory needs a proven prior observation.
            if amounts and not last:
                raise ValueError('program_baseline_unverifiable')
            baseline=dict(day=day(cutoff),upnl='0',positions={},captured_at=captured)
        elif baseline['day']!=day(cutoff):
            if not last:
                raise ValueError('program_baseline_unverifiable')
            baseline=dict(last,day=day(cutoff))
        fx={coin:quote_band(coin,snapshot.get('external_fx',{}).get(coin,[]),now)
            for coin in sorted(required-NATIVE_USD)}
        result=dict(verified=True,scope='program',accounting_basis='own_fills_funding_marks',
            ownership_mode='local_order_history',day=day(cutoff),captured_at=captured,
            from_ms=activity['from_ms'],snapshot_version=snapshot.get('snapshot_version'),
            revision=store.meta('external_attribution_revision',0),equity_revision=revision,
            program_daily=str(daily+daily_funding+floating-dec(baseline['upnl'])),
            program_total=str(realized+total_funding+floating),program_upnl=str(floating),
            program_funding=str(daily_funding),program_funding_total=str(total_funding),
            program_journal=original_journal,funding_verified=True,funding_daily_verified=True,
            funding_attribution=ADVERSE_POLICY if estimated else 'exclusive_program',
            funding_attribution_estimated=estimated,funding_ownership_revision=ownership.get('revision',0),
            external_fx=fx,valuation='public_spot_lower_bound' if fx else 'native_usd',
            external_cash=None,external_contribution=None,equity_daily=None,
            external_cash_by_currency={},external_contribution_by_currency={})
        if not persist:
            return dict(result,prepared=dict(program_funding_anchor=anchor,
                program_funding_checkpoint=checkpoint,program_verified_history=index))
        with store.transaction():
            if (type(snapshot.get('snapshot_version')) is not int
                    or snapshot['snapshot_version']!=store.meta('account_snapshot_version')
                    or store.meta('account_snapshot_error')
                    or original_journal!=journal_fingerprint(store)
                    or contracts!=program_contracts(store)
                    or revision!=store.meta('equity_baseline_revision',0)
                    or original_anchor!=store.meta('program_funding_anchor')
                    or original_checkpoint!=store.meta('program_funding_checkpoint')
                    or previous!=store.meta('program_verified_history',{})
                    or ownership!=store.meta(OWNERSHIP_KEY,{})
                    or not 0<=self.clock()-captured<=10):
                raise ValueError('program_snapshot_superseded')
            store.set_meta('program_funding_anchor',anchor)
            store.set_meta('program_funding_checkpoint',checkpoint)
            if index!=previous:
                store.set_meta('program_verified_history',index)
            store.set_meta('external_income',result)
            store.set_meta('program_activity',plain(activity))
            store.set_meta('program_daily_upnl',baseline)
            store.set_meta('program_last_upnl',dict(upnl=str(floating),positions=plain(amounts),captured_at=captured))
        return result
