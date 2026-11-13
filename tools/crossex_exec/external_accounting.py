"""Verified cash-flow attribution; unknown currency/coverage is never zero.

Equity fallback and external contribution share the SAME observation epoch.
Trade history proves quantity changes and checks account-book PnL/fees. The
account book is the single cash source, so trades are not counted a second time.
"""
from collections import defaultdict
from functools import lru_cache

from tools.fill_sim.core import dec, day
from .external import ExternalUnverifiable, plain, route_symbol, digest, canonical_json
from .external_fx import NATIVE_USD, quote_band, upper_contribution
from .program_risk import journal_pnl, journal_fingerprint
from .position_values import key_currency, usd_value
from .funding import funding_positions, funding_continuity, daily_continuity, certify_daily
from .cash_identity import cash_identity, program_contract_identity

BITGET_FUNDING = frozenset(('CONTRACT_MAIN_SETTLE_FEE_USER_IN','CONTRACT_MAIN_SETTLE_FEE_USER_OUT'))
BITGET_TRADING = frozenset(('OPEN_LONG','OPEN_SHORT','CLOSE_LONG','CLOSE_SHORT','BUY_DEAL','SELL_DEAL'))


def capital_transfer(row, *, bitget=False):
    kind=row['type' if bitget else 'statement_type']
    # Exact documented transfer enums only. No borrowing/interest/conversion
    # aliases and no contract cash disguised as a transfer.
    allowed=('TRANSFER_IN','TRANSFER_OUT')
    if kind not in allowed:
        return False
    value=dec(row['amount' if bitget else 'change'])
    if (row.get('symbol') not in ('',None) or not row.get('coin')
            or (not bitget and not row.get('exchange_type'))
            or (bitget and dec(row['fee'])!=0)
            or (kind.endswith('_IN') and value<=0) or (kind.endswith('_OUT') and value>=0)):
        raise ValueError('capital_transfer_unverifiable')
    return True


def midnight(now):
    return int(now)//86400*86400


def unique_rows(rows,key,start,end):
    if not isinstance(rows,list):
        raise ValueError('history_missing')
    seen=set()
    for row in rows:
        identity=row[key]
        if not isinstance(identity,str) or not identity or identity in seen:
            raise ValueError('history_duplicate')
        seen.add(identity)
        at=dec(row['create_time'] if 'create_time' in row else row['ts'])
        if at!=at.to_integral_value() or not start<=at<=end:
            raise ValueError('history_window')
        yield row,int(at)


def program_activity(store,activity,captured,anchor=None):
    """Return settled daily funding only after program cash matches its fills."""
    if activity['complete'] is not True:
        raise ValueError('history_coverage')
    start,end=activity['from_ms'],activity['to_ms']
    if start>midnight(captured)*1000 or end<int(captured*1000):
        raise ValueError('history_coverage')
    owned={}
    expected,actual=defaultdict(lambda:dec(0)),defaultdict(lambda:dec(0))
    @lru_cache(maxsize=64)
    def entries(pair_id):
        return {r['leg']:r for r in store.iter_orders(pair_id=pair_id,reduce_only=False) if r.get('vwap')}
    for row in store.iter_orders(exclude_states=('SHADOW','UNSENT')):
        key=route_symbol(row['instrument'])
        owned[key]=program_contract_identity(row['instrument']) if key[0]=='crossex' else None
        if row['state'] not in ('TERMINAL','REJECTED') or row.get('accounting_pending'):
            raise ValueError('program_accounting_pending')
        value=-dec(row.get('fee') or 0)
        quantity=dec(row.get('filled',0))
        if quantity and row.get('vwap') is None:
            raise ValueError('program_accounting_pending')
        if row['reduce_only'] and quantity:
            entry=entries(row['pair_id']).get(row['leg'])
            if not entry:
                raise ValueError('program_entry_missing')
            value+=quantity*(dec(row['vwap'])-dec(entry['vwap']))*(1 if row['leg']==0 else -1)
        if value:
            at=row.get('fill_ms')
            if at is None:
                raise ValueError('program_cash_time_unknown')
            if start<=dec(at)<=end:
                expected[key]+=value
    funding=new_funding=dec(0)
    def is_new(name,identity):
        return anchor is not None and identity not in anchor['history_index'][name]
    for row,at in unique_rows(activity['crossex_book'],'id',start,end):
        if capital_transfer(row):
            continue
        key=('crossex',row['symbol'])
        if key not in owned:
            continue
        cash_identity(row,owned[key])
        kind=row['statement_type']
        value=dec(row['change'])
        if kind in ('TRANSACTION','TRADING_FEE'):
            actual[key]+=value
        elif kind=='FUNDING_FEE':
            if at>=midnight(captured)*1000:
                funding+=value
            if is_new('crossex_book',row['id']):
                new_funding+=value
        else:
            raise ValueError('program_cash_type_unverified')
    for row,at in unique_rows(activity['bitget_book'],'id',start,end):
        if capital_transfer(row,bitget=True):
            continue
        key=('bitget',row['symbol'])
        if key not in owned or row['category']!='USDT-FUTURES' or row['coin']!='USDT':
            raise ValueError('program_cash_identity')
        kind=row['type']
        value=dec(row['amount'])+dec(row['fee'])
        if kind in BITGET_TRADING:
            actual[key]+=value
        elif kind in BITGET_FUNDING:
            if (dec(row['fee'])>0 or (kind.endswith('_IN') and value<0)
                    or (kind.endswith('_OUT') and value>0)):
                raise ValueError('program_funding_sign')
            if at>=midnight(captured)*1000:
                funding+=value
            if is_new('bitget_book',row['id']):
                new_funding+=value
        else:
            raise ValueError('program_cash_type_unverified')
    if {k:v for k,v in expected.items() if v}!={k:v for k,v in actual.items() if v}:
        raise ValueError('program_cash_journal_mismatch')
    return funding if anchor is None else (funding,dec(anchor['program_funding_carry'])+new_funding)


def anchor_value(store,snapshot,projection,captured):
    return dict(day=day(captured),captured_at=snapshot.get('accounting_at',captured),equity=str(snapshot['equity']),
        external_upnl=projection['external_upnl'],positions=plain(projection['external_positions']),
        external_upnl_by_position=plain(projection['external_upnl_by_position']),
        cash_balances=cash_balances(snapshot),history_index=history_index(snapshot['activity']),
        funding_positions=funding_positions(snapshot),
        revision=store.meta('external_attribution_revision',0),program_funding_offset='0',program_funding_carry='0')


def cash_balances(snapshot):
    result={}
    for venue,data in snapshot['raw_accounts'].items():
        if venue not in ('crossex','bitget') or not isinstance(data['assets'],list):
            raise ValueError('cash_balances_unknown')
        for row in data['assets']:
            key='|'.join((venue,row['exchange_type'],row['coin'])) if venue=='crossex' else 'bitget|'+row['coin']
            if key in result:
                raise ValueError('cash_balances_duplicate')
            result[key]=str(dec(row['balance']))
    if set(snapshot['raw_accounts'])!={'crossex','bitget'}:
        raise ValueError('cash_balances_unknown')
    return result


HISTORY_KEYS = dict(crossex_book='id',crossex_trades='transaction_id',bitget_book='id')


def history_index(activity, cache=None):
    # Complete remote rows are still examined each time. This cache saves only
    # hashing/normalization of byte-for-byte equal rows; it supplies NO rows to a
    # response and cannot hide a deletion, rewrite, late row or incomplete page.
    result = {}
    remaining_bytes = 8*1024*1024
    remaining_rows = 25000
    for name,key in HISTORY_KEYS.items():
        index = result[name] = {}
        prior = cache.get(name,{}) if cache is not None else {}
        pending = {}
        for row,at in unique_rows(activity[name],key,activity['from_ms'],activity['to_ms']):
            old = prior.get(row[key])
            encoded = canonical_json(row)
            fingerprint = old[1] if old is not None and old[0] == encoded else digest(row)
            index[row[key]] = dict(at=at,hash=fingerprint)
            # Bounded optimization; eviction never changes verification.
            if remaining_rows and len(encoded)<=remaining_bytes:
                pending[row[key]] = (encoded, fingerprint)
                remaining_rows -= 1
                remaining_bytes -= len(encoded)
        if cache is not None:
            cache[name] = pending
    return result


def verified_history(store,activity,current=None):
    """Monotonic ID/content evidence, independent of the accounting epoch.

    Entries outside the requested window remain durable but need not be returned.
    An ID seen again must ALWAYS have the same contents. Migration includes the
    last verified pre-round-13 activity, not just the original anchor index.
    """
    previous=store.meta('external_verified_history')
    if previous is None:
        previous={name:{} for name in HISTORY_KEYS}
        anchor=store.meta('external_equity_anchor') or {}
        seeds=[anchor.get('history_index',{})]
        legacy=store.meta('external_activity')
        if legacy:
            seeds.append(history_index(legacy))
        for seed in seeds:
            for name,rows in seed.items():
                for key,row in rows.items():
                    if key in previous[name] and previous[name][key]!=row:
                        raise ValueError('history_revised_or_missing')
                    previous[name][key]=row
    current=history_index(activity) if current is None else {k:dict(v) for k,v in current.items()}
    for name in HISTORY_KEYS:
        for key,row in previous[name].items():
            if key in current[name] or row['at']>=activity['from_ms']:
                if current[name].get(key)!=row:
                    raise ValueError('history_revised_or_missing')
        current[name]={**previous[name],**current[name]}
    return current


def cash_continuity(anchor,snapshot,current=None):
    """Empty/short pages alone cannot prove that no funding cash was missed."""
    activity=snapshot['activity']
    current=history_index(activity) if current is None else current
    previous=anchor['history_index']
    epoch=int(anchor['captured_at']*1000)
    for name,rows in previous.items():
        for key,row in rows.items():
            if row['at']>=activity['from_ms'] and current[name].get(key)!=row:
                raise ValueError('history_revised_or_missing')
    for name,rows in current.items():
        if any(row['at']<epoch and key not in previous[name] for key,row in rows.items()):
            raise ValueError('late_epoch_history')
    cash=defaultdict(lambda:dec(0))
    for row in activity['crossex_book']:
        if row['id'] not in previous['crossex_book']:
            cash['|'.join(('crossex',row['exchange_type'],row['coin']))]+=dec(row['change'])
    for row in activity['bitget_book']:
        if row['id'] not in previous['bitget_book']:
            cash['bitget|'+row['coin']]+=dec(row['amount'])+dec(row['fee'])
    before,after=anchor['cash_balances'],cash_balances(snapshot)
    # Missing asset rows do not supply a zero balance proof for CrossEx.
    if set(before)!=set(after) or any(dec(after[k])-dec(before[k])!=cash[k] for k in before):
        raise ValueError('cash_balance_history_mismatch')
    if any(v for k,v in cash.items() if k not in before):
        raise ValueError('cash_currency_unknown')


def settlement_state(snapshot):
    """Marks may move; cash, quantities and cumulative settlements may not."""
    raw=snapshot['raw_positions']
    positions={venue:sorted([{k:r[k] for k in ('symbol','position_side','posSide','position_qty','total',
        'position_id','entry_price','fee','funding_fee','funding_time','closed_pnl','averageOpenPrice',
        'achievedProfits','totalFunding','createdTime','create_time') if k in r}
        for r in rows],key=lambda r:str(sorted(r.items()))) for venue,rows in raw.items()}
    accounts=snapshot['raw_accounts']
    assets={venue:sorted([{k:r[k] for k in ('coin','exchange_type','balance','liability','debt') if k in r}
        for r in data.get('assets',[])],key=lambda r:str(sorted(r.items()))) for venue,data in accounts.items()}
    return dict(positions=positions,assets=assets)


def read_snapshot(accounts,store,clock):
    from .history import HistoryError, error_details
    started=clock()
    first=accounts.snapshot()
    cutoff=clock()
    anchor=store.meta('external_equity_anchor') or {}
    start=min(midnight(cutoff),anchor.get('captured_at',cutoff))
    activity=dict(from_ms=int(start*1000),to_ms=int(cutoff*1000),complete=False)
    stage='activity'
    try:
        activity=accounts.activity(int(start*1000),int(cutoff*1000))
        open_orders={}
        stage='/api/v4/crossex/open_orders'
        open_orders['crossex']=accounts.cross.open_orders()
        stage='/api/v3/trade/unfilled-orders'
        open_orders['bitget']=accounts.bitget.open_orders()
        stage='closing_account_snapshot'
        last=accounts.snapshot()
        condition=None
        if settlement_state(first)!=settlement_state(last):
            condition='settlement_changed_during_read'
        elif day(started)!=day(clock()):
            condition='utc_day_changed_during_read'
        elif any(not isinstance(rows,list) or rows for rows in open_orders.values()):
            condition='remote_open_orders_or_invalid_response'
        elif not 0<=clock()-started<=10:
            condition='snapshot_stale'
        elif activity['complete'] is not True:
            condition='provider_incomplete'
        if condition:
            activity=dict(activity,complete=False,failure=activity.get('failure') or
                dict(stage='snapshot_consistency',condition=condition,from_ms=int(start*1000),
                     to_ms=int(cutoff*1000),page=None))
        snapshot=dict(last,raw_open_orders=open_orders)
    except Exception as error:
        diagnostic=(error.diagnostic if isinstance(error,HistoryError) else
            dict(stage=stage,condition='request_failed',from_ms=int(start*1000),to_ms=int(cutoff*1000),
                 page=None,**error_details(error)))
        activity=dict(getattr(error,'activity',activity),complete=False,failure=diagnostic)
        if getattr(error,'account_snapshot',None) is not None:
            activity['failed_read']=plain(error.account_snapshot)
        snapshot=first
    return dict(snapshot,captured_at=started,accounting_at=cutoff,activity=activity)


class ExternalAccounting:
    def __init__(self,store,registry,clock):
        self.store,self.registry,self.clock=store,registry,clock
        self._history_hashes={}

    def anchor(self,snapshot,projection,captured):
        value=anchor_value(self.store,snapshot,projection,captured)
        self.store.set_meta('external_equity_anchor',value)
        return value

    def evaluate(self,snapshot,projection):
        try:
            return self._evaluate(snapshot,projection)
        except (ValueError,KeyError,TypeError,ArithmeticError) as error:
            failure=ExternalUnverifiable('external_income_unverifiable')
            safe={'funding_utc_boundary_unproven','funding_daily_ledger_mismatch',
                  'funding_late_checkpoint_history','funding_collection_time_invalid',
                  'funding_collection_time_revised','funding_collection_counter_conflict',
                  'funding_counter_ledger_mismatch','funding_lifetime_missing',
                  'history_coverage','external_baseline_unverifiable','cash_balance_history_mismatch',
                  'history_revised_or_missing','late_epoch_history','unattributed_cashflow',
                  'cash_ownership_unknown','trade_ownership_unknown'}
            failure.diagnostic=dict(stage='income_verification',
                condition=str(error) if str(error) in safe else 'income_evidence_invalid')
            raise failure from None

    def _evaluate(self,snapshot,projection):
        anchor=self.store.meta('external_equity_anchor')
        original_anchor=anchor
        equity_revision=self.store.meta('equity_baseline_revision',0)
        prior_history=self.store.meta('external_verified_history')
        prior_checkpoint=self.store.meta('funding_checkpoint')
        original_journal=journal_fingerprint(self.store)
        captured=snapshot['captured_at']
        cutoff=snapshot.get('accounting_at',captured)
        today=day(cutoff)
        initial=False
        if anchor is None and not self.registry.records() and next(self.store.iter_orders(),None) is None:
            if projection['positions']:
                raise ValueError('program_baseline_unverifiable')
            # A genuinely new, flat program may establish its first cash epoch.
            # Existing history requires the explicit audited management path.
            anchor=anchor_value(self.store,snapshot,projection,captured)
            anchor=certify_daily(anchor,'initial_flat',equity_revision)
            initial=True
        if not anchor or anchor['revision']!=self.store.meta('external_attribution_revision',0):
            raise ValueError('external_baseline_unverifiable')
        activity=snapshot['activity']
        start,end=activity['from_ms'],activity['to_ms']
        if (activity['complete'] is not True or type(start) is not int or type(end) is not int
                or start>min(int(anchor['captured_at']*1000),midnight(captured)*1000)
                or end!=int(cutoff*1000) or not 0<=self.clock()-captured<=10):
            raise ValueError('history_coverage')
        records=self.registry.records()
        index=history_index(activity,self._history_hashes)
        cash_continuity(anchor,snapshot,index)
        funding_continuity(self.store,anchor,snapshot)
        checkpoint=daily_continuity(self.store,anchor,snapshot)
        checkpoint['history_index']=index
        checkpoint=certify_daily(checkpoint,'daily_continuity',equity_revision)
        history=verified_history(self.store,activity,index)
        external={r['identity']['symbol']:r['identity'] for r in records.values()}
        # Keep native units through every ledger/quantity check. Never net
        # distinct settlement currencies before converting their contribution.
        native=defaultdict(lambda:dec(0))
        cash_by_currency=defaultdict(lambda:dec(0))
        required=set()
        for amounts,sign in ((projection['external_upnl_by_position'],1),
                             (anchor['external_upnl_by_position'],-1)):
            for key,value in amounts.items():
                coin=external[key.split('|')[1]]['quote']
                amount=dec(value)
                native[coin]+=sign*amount
                if amount and coin not in NATIVE_USD:
                    required.add(coin)
        owned={}
        for row in self.store.iter_orders(exclude_states=('SHADOW','UNSENT')):
            key=route_symbol(row['instrument'])
            owned[key]=program_contract_identity(row['instrument']) if key[0]=='crossex' else None
        epoch=int(anchor['captured_at']*1000)
        funding=dec(0)
        ledger=defaultdict(lambda:dec(0))
        trade_money=defaultdict(lambda:dec(0))
        positions={k:dec(v) for k,v in anchor['positions'].items()}
        for row,at in unique_rows(activity['crossex_book'],'id',start,end):
            symbol=row['symbol']
            kind=row['statement_type']
            value=dec(row['change'])
            if capital_transfer(row):
                continue
            if kind not in ('TRANSACTION','TRADING_FEE','FUNDING_FEE','LIQUIDATION_FEE'):
                # Transfers, repayment, interest, conversions and unknown entries
                # are not contract income. No cancellation/zero assumption.
                raise ValueError('unattributed_cashflow')
            if symbol in external:
                i=external[symbol]
                cash_identity(row,i)
                if row['id'] not in anchor['history_index']['crossex_book']:
                    coin=i['quote']
                    cash_by_currency[coin]+=value
                    native[coin]+=value
                    if value and coin not in NATIVE_USD:
                        required.add(coin)
                    if kind in ('TRANSACTION','TRADING_FEE'):
                        ledger[(symbol,str(row['business_id']),kind)]+=value
                    elif kind=='LIQUIDATION_FEE':
                        # A forced close must still appear in trades. Keeping its
                        # extra fee is conservative; position continuity is checked below.
                        if value>0:
                            raise ValueError('liquidation_fee_sign')
            elif ('crossex',symbol) in owned:
                cash_identity(row,owned[('crossex',symbol)])
                if kind=='FUNDING_FEE' and at>=midnight(captured)*1000:
                    funding+=value
            else:
                raise ValueError('cash_ownership_unknown')
        for row,at in unique_rows(activity['crossex_trades'],'transaction_id',start,end):
            symbol=row['symbol']
            if symbol not in external:
                if ('crossex',symbol) not in owned:
                    raise ValueError('trade_ownership_unknown')
                continue
            i=external[symbol]
            if (row['exchange_type']!=i['exchange_type'] or row['business_type']!=i['business']
                    or row['fee_coin']!=i['quote'] or row['side'] not in ('BUY','SELL')
                    or dec(row['qty'])<=0 or dec(row['price'])<=0 or dec(row['fee'])<0):
                raise ValueError('trade_identity')
            if row['transaction_id'] in anchor['history_index']['crossex_trades']:
                continue
            oid=str(row['order_id'])
            trade_money[(symbol,oid,'TRANSACTION')]+=dec(row['rpnl'])
            trade_money[(symbol,oid,'TRADING_FEE')]-=dec(row['fee'])
            signed=dec(row['qty'])*(1 if row['side']=='BUY' else -1)
            if row['position_mode']=='DUAL' and row['position_side'] in ('LONG','SHORT'):
                key='crossex|'+symbol+'|'+row['position_side'].lower()
                positions[key]=positions.get(key,dec(0))+signed*(1 if row['position_side']=='LONG' else -1)
                if positions[key]<0:
                    raise ValueError('position_continuity')
            elif row['position_mode']=='SINGLE' and row['position_side']=='NONE':
                long,short=('crossex|'+symbol+'|'+s for s in ('long','short'))
                net=positions.get(long,dec(0))-positions.get(short,dec(0))+signed
                positions[long],positions[short]=max(net,dec(0)),max(-net,dec(0))
            else:
                raise ValueError('position_direction')
        if {k:v for k,v in ledger.items() if v}!={k:v for k,v in trade_money.items() if v}:
            raise ValueError('cash_trade_mismatch')
        if {k:v for k,v in positions.items() if v}!={k:dec(v) for k,v in projection['external_positions'].items()}:
            raise ValueError('position_continuity')
        funding,total_funding=program_activity(self.store,activity,cutoff,anchor)
        daily,realized=journal_pnl(self.store,cutoff)
        if (set(projection['upnl_by_position'])!=set(projection['positions'])
                or sum(map(dec,projection['upnl_by_position'].values()),dec(0))!=dec(projection['upnl'])):
            raise ValueError('program_position_pnl_unknown')
        floating=dec(0)
        for key,value in projection['upnl_by_position'].items():
            if tuple(key.split('|')[:2]) not in owned:
                raise ValueError('program_position_unowned')
            coin=key_currency(key)
            floating+=usd_value(value,coin,snapshot.get('external_fx',{}),self.clock(),upper=False)
            if coin not in NATIVE_USD:
                required.add(coin)
        baseline=self.store.meta('program_daily_upnl')
        last=self.store.meta('program_last_upnl')
        if initial:
            baseline=dict(day=today,upnl='0',positions={},captured_at=captured)
        elif not baseline or baseline['day']!=today:
            if not last:
                raise ValueError('program_baseline_unverifiable')
            baseline=dict(last,day=today)
        fx={coin:quote_band(coin,snapshot.get('external_fx',{}).get(coin,[]),self.clock())
            for coin in sorted(required)}
        def converted(amounts):
            return sum((amount if coin in NATIVE_USD else upper_contribution(amount,fx[coin])
                        for coin,amount in amounts.items() if amount),dec(0))
        cash,contribution=converted(cash_by_currency),converted(native)
        if anchor['day']==today:
            funding-=dec(anchor.get('program_funding_offset',0))
        result=dict(verified=True,day=today,captured_at=captured,from_ms=epoch,revision=anchor['revision'],
            snapshot_version=snapshot.get('snapshot_version'),equity_revision=equity_revision,
            external_cash=str(cash),external_contribution=str(contribution),program_funding=str(funding),
            equity_daily=str(dec(snapshot['equity'])-dec(anchor['equity'])-contribution),
            external_cash_by_currency={k:str(v) for k,v in cash_by_currency.items()},
            external_contribution_by_currency={k:str(v) for k,v in native.items()},
            external_fx=fx,valuation='public_spot_upper_bound' if fx else 'native_usd')
        result.update(scope='program',program_daily=str(daily+funding-dec(baseline['upnl'])+floating),
            program_total=str(realized+total_funding+floating),program_funding_total=str(total_funding),
            program_upnl=str(floating),program_journal=original_journal,funding_verified=True,
            funding_daily_verified=True)
        if anchor['day']!=today:
            # Verify the COMPLETE carried interval first (including settlements
            # while offline), then start today's equity fallback at the same
            # observation as the original daily_equity branch. Never compare
            # a new equity epoch to an old external contribution epoch.
            result['carried_interval']=dict(from_ms=epoch,external_contribution=str(contribution),
                external_cash_by_currency=result['external_cash_by_currency'],
                external_contribution_by_currency=result['external_contribution_by_currency'],external_fx=fx)
            anchor=anchor_value(self.store,snapshot,projection,captured)
            anchor['program_funding_carry']=str(total_funding)
            anchor=certify_daily(anchor,'daily_continuity',equity_revision)
            result.update(from_ms=int(anchor['captured_at']*1000),external_cash='0',
                          external_contribution='0',equity_daily='0',
                          external_cash_by_currency={},external_contribution_by_currency={})
        # Durable evidence and baselines change together; failure cannot publish
        # a verified projection or let a previously cached result admit a send.
        with self.store.transaction():
            if (type(snapshot.get('snapshot_version')) is not int
                    or snapshot['snapshot_version']!=self.store.meta('account_snapshot_version')
                    or self.store.meta('account_snapshot_error')
                    or anchor['revision']!=self.store.meta('external_attribution_revision',0)
                    or equity_revision!=self.store.meta('equity_baseline_revision',0)
                    or original_anchor!=self.store.meta('external_equity_anchor')
                    or prior_history!=self.store.meta('external_verified_history')
                    or prior_checkpoint!=self.store.meta('funding_checkpoint')
                    or result['program_journal']!=journal_fingerprint(self.store)
                    or not 0<=self.clock()-captured<=10):
                raise ValueError('external_snapshot_superseded')
            self.store.set_meta('external_equity_anchor',anchor)
            self.store.set_meta('external_income',result)
            self.store.set_meta('external_activity',plain(activity))
            if history!=prior_history:
                self.store.set_meta('external_verified_history',history)
            self.store.set_meta('funding_checkpoint',checkpoint)
            self.store.set_meta('program_daily_upnl',baseline)
            if not last or captured>=last['captured_at']:
                self.store.set_meta('program_last_upnl',dict(upnl=str(floating),
                    positions=projection['upnl_by_position'],captured_at=captured))
        return result
