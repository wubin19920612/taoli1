"""Verified cash-flow attribution; unknown currency/coverage is never zero.

Equity fallback and external contribution share the SAME observation epoch.
Trade history proves quantity changes and checks account-book PnL/fees. The
account book is the single cash source, so trades are not counted a second time.
"""
from collections import defaultdict
from functools import lru_cache

from tools.fill_sim.core import dec, day
from .external import ExternalUnverifiable, plain, route_symbol, digest

BITGET_FUNDING = frozenset(('CONTRACT_MAIN_SETTLE_FEE_USER_IN','CONTRACT_MAIN_SETTLE_FEE_USER_OUT'))
BITGET_TRADING = frozenset(('OPEN_LONG','OPEN_SHORT','CLOSE_LONG','CLOSE_SHORT','BUY_DEAL','SELL_DEAL'))


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


def program_activity(store,activity,captured):
    """Return settled daily funding only after program cash matches its fills."""
    if activity['complete'] is not True:
        raise ValueError('history_coverage')
    start,end=activity['from_ms'],activity['to_ms']
    if start>midnight(captured)*1000 or end<int(captured*1000):
        raise ValueError('history_coverage')
    owned=set()
    expected,actual=defaultdict(lambda:dec(0)),defaultdict(lambda:dec(0))
    @lru_cache(maxsize=64)
    def entries(pair_id):
        return {r['leg']:r for r in store.iter_orders(pair_id=pair_id,reduce_only=False) if r.get('vwap')}
    for row in store.iter_orders(exclude_states=('SHADOW','UNSENT')):
        key=route_symbol(row['instrument'])
        owned.add(key)
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
    funding=dec(0)
    for row,at in unique_rows(activity['crossex_book'],'id',start,end):
        key=('crossex',row['symbol'])
        if key not in owned:
            continue
        if row['coin']!='USDT' or not row['symbol'].startswith(row['exchange_type']+'_FUTURE_'):
            raise ValueError('program_cash_identity')
        kind=row['statement_type']
        value=dec(row['change'])
        if kind in ('TRANSACTION','TRADING_FEE'):
            actual[key]+=value
        elif kind=='FUNDING_FEE':
            if at>=midnight(captured)*1000:
                funding+=value
        else:
            raise ValueError('program_cash_type_unverified')
    for row,at in unique_rows(activity['bitget_book'],'id',start,end):
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
        else:
            raise ValueError('program_cash_type_unverified')
    if {k:v for k,v in expected.items() if v}!={k:v for k,v in actual.items() if v}:
        raise ValueError('program_cash_journal_mismatch')
    return funding


def anchor_value(store,snapshot,projection,captured):
    return dict(day=day(captured),captured_at=snapshot.get('accounting_at',captured),equity=str(snapshot['equity']),
        external_upnl=projection['external_upnl'],positions=plain(projection['external_positions']),
        external_upnl_by_position=plain(projection['external_upnl_by_position']),
        cash_balances=cash_balances(snapshot),history_index=history_index(snapshot['activity']),
        revision=store.meta('external_attribution_revision',0),program_funding_offset='0')


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


def history_index(activity):
    return {name:{row[key]:dict(at=at,hash=digest(row))
                  for row,at in unique_rows(activity[name],key,activity['from_ms'],activity['to_ms'])}
            for name,key in HISTORY_KEYS.items()}


def cash_continuity(anchor,snapshot):
    """Empty/short pages alone cannot prove that no funding cash was missed."""
    activity=snapshot['activity']
    current=history_index(activity)
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
        'position_id','entry_price','fee','funding_fee','closed_pnl','averageOpenPrice','achievedProfits') if k in r}
        for r in rows],key=lambda r:str(sorted(r.items()))) for venue,rows in raw.items()}
    accounts=snapshot['raw_accounts']
    assets={venue:sorted([{k:r[k] for k in ('coin','exchange_type','balance','liability','debt') if k in r}
        for r in data.get('assets',[])],key=lambda r:str(sorted(r.items()))) for venue,data in accounts.items()}
    return dict(positions=positions,assets=assets)


def read_snapshot(accounts,store,clock):
    started=clock()
    first=accounts.snapshot()
    cutoff=clock()
    anchor=store.meta('external_equity_anchor') or {}
    start=min(midnight(cutoff),anchor.get('captured_at',cutoff))
    try:
        activity=accounts.activity(int(start*1000),int(cutoff*1000))
        open_orders=dict(crossex=accounts.cross.open_orders(),bitget=accounts.bitget.open_orders())
        last=accounts.snapshot()
        if (settlement_state(first)!=settlement_state(last) or day(started)!=day(clock())
                or any(not isinstance(rows,list) or rows for rows in open_orders.values())):
            activity=dict(activity,complete=False)
        snapshot=dict(last,raw_open_orders=open_orders)
    except Exception as error:
        activity=dict(from_ms=int(start*1000),to_ms=int(cutoff*1000),complete=False)
        if getattr(error,'account_snapshot',None) is not None:
            activity['failed_read']=plain(error.account_snapshot)
        snapshot=first
    return dict(snapshot,captured_at=started,accounting_at=cutoff,activity=activity)


class ExternalAccounting:
    def __init__(self,store,registry,clock):
        self.store,self.registry,self.clock=store,registry,clock

    def anchor(self,snapshot,projection,captured):
        value=anchor_value(self.store,snapshot,projection,captured)
        self.store.set_meta('external_equity_anchor',value)
        return value

    def evaluate(self,snapshot,projection):
        try:
            return self._evaluate(snapshot,projection)
        except (ValueError,KeyError,TypeError,ArithmeticError):
            raise ExternalUnverifiable('external_income_unverifiable') from None

    def _evaluate(self,snapshot,projection):
        anchor=self.store.meta('external_equity_anchor')
        captured=snapshot['captured_at']
        cutoff=snapshot.get('accounting_at',captured)
        today=day(cutoff)
        if not anchor or anchor['revision']!=self.store.meta('external_attribution_revision',0):
            raise ValueError('external_baseline_unverifiable')
        activity=snapshot['activity']
        start,end=activity['from_ms'],activity['to_ms']
        if (activity['complete'] is not True or type(start) is not int or type(end) is not int
                or start>min(int(anchor['captured_at']*1000),midnight(captured)*1000)
                or end!=int(cutoff*1000) or not 0<=self.clock()-captured<=10):
            raise ValueError('history_coverage')
        records=self.registry.records()
        cash_continuity(anchor,snapshot)
        external={r['identity']['symbol']:r['identity'] for r in records.values()}
        # Current and epoch floating amounts are quote units. Public CrossEx
        # docs do not specify an authoritative historical USDC -> account-equity
        # valuation. Keep the contract registered/banned, but refuse attribution.
        unsupported={s for s,i in external.items() if i['quote']!='USDT'}
        if any(k.split('|')[1] in unsupported and dec(value)
               for amounts in (projection['external_upnl_by_position'],anchor['external_upnl_by_position'])
               for k,value in amounts.items()):
            raise ValueError('external_currency_unverifiable')
        owned=set()
        for row in self.store.iter_orders(exclude_states=('SHADOW','UNSENT')):
            owned.add(route_symbol(row['instrument']))
        epoch=int(anchor['captured_at']*1000)
        cash=dec(0)
        funding=dec(0)
        ledger=defaultdict(lambda:dec(0))
        trade_money=defaultdict(lambda:dec(0))
        positions={k:dec(v) for k,v in anchor['positions'].items()}
        for row,at in unique_rows(activity['crossex_book'],'id',start,end):
            symbol=row['symbol']
            kind=row['statement_type']
            value=dec(row['change'])
            if kind not in ('TRANSACTION','TRADING_FEE','FUNDING_FEE','LIQUIDATION_FEE'):
                # Transfers, repayment, interest, conversions and unknown entries
                # are not contract income. No cancellation/zero assumption.
                raise ValueError('unattributed_cashflow')
            if symbol in external:
                i=external[symbol]
                if row['exchange_type']!=i['exchange_type'] or row['coin']!=i['quote']:
                    raise ValueError('cash_identity')
                if row['id'] not in anchor['history_index']['crossex_book']:
                    if symbol in unsupported and value:
                        raise ValueError('external_currency_unverifiable')
                    cash+=value
                    if kind in ('TRANSACTION','TRADING_FEE'):
                        ledger[(symbol,str(row['business_id']),kind)]+=value
                    elif kind=='LIQUIDATION_FEE':
                        # A forced close must still appear in trades. Keeping its
                        # extra fee is conservative; position continuity is checked below.
                        if value>0:
                            raise ValueError('liquidation_fee_sign')
            elif ('crossex',symbol) in owned:
                if row['coin']!='USDT' or not symbol.startswith(row['exchange_type']+'_FUTURE_'):
                    raise ValueError('program_cash_identity')
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
        funding=program_activity(self.store,activity,cutoff)
        contribution=cash+dec(projection['external_upnl'])-dec(anchor['external_upnl'])
        if anchor['day']==today:
            funding-=dec(anchor.get('program_funding_offset',0))
        result=dict(verified=True,day=today,captured_at=captured,from_ms=epoch,revision=anchor['revision'],
            external_cash=str(cash),external_contribution=str(contribution),program_funding=str(funding),
            equity_daily=str(dec(snapshot['equity'])-dec(anchor['equity'])-contribution))
        if anchor['day']!=today:
            # Verify the COMPLETE carried interval first (including settlements
            # while offline), then start today's equity fallback at the same
            # observation as the original daily_equity branch. Never compare
            # a new equity epoch to an old external contribution epoch.
            result['carried_interval']=dict(from_ms=epoch,external_contribution=str(contribution))
            anchor=anchor_value(self.store,snapshot,projection,captured)
            result.update(from_ms=int(anchor['captured_at']*1000),external_cash='0',
                          external_contribution='0',equity_daily='0')
        # Durable evidence and baselines change together; failure cannot publish
        # a verified projection or let a previously cached result admit a send.
        with self.store.transaction():
            self.store.set_meta('external_equity_anchor',anchor)
            self.store.set_meta('external_income',result)
            self.store.set_meta('external_activity',plain(activity))
        return result
