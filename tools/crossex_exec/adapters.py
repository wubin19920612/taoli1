"""Documented private routes. These are exercised ONLY against localhost in tests."""
import re
import time
from urllib.parse import quote
from tools.fill_sim.core import dec
from .transport import APIError


def identifier(value):
    value = str(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('invalid_order_id')
    return value


def validate_intent(i):
    identifier(i['cid'])
    identifier(i['instrument']['symbol'])
    if i['side'] not in ('buy','sell') or i['position_side'] not in ('long','short'):
        raise ValueError('invalid_direction')
    if type(i['reduce_only']) is not bool or min(dec(i['quantity']),dec(i['price'])) <= 0:
        raise ValueError('invalid_order')


def checked_status(data, i, *, gate):
    oid, cid, qty = ('order_id','text','executed_qty') if gate else ('orderId','clientOid','cumExecQty')
    if (data.get('symbol') != i['instrument']['symbol'] or data.get(cid) != i['cid']
            or (i.get('oid') and str(data.get(oid)) != i['oid'])):
        raise APIError('invalid_response')
    if not gate and data.get('category') != 'USDT-FUTURES':
        raise APIError('invalid_response')
    state = data['state' if gate else 'orderStatus']
    terminal = {'FILLED','CANCELLED','FAIL','REJECT'} if gate else {'filled','cancelled'}
    active = {'NEW','OPEN','PARTIALLY_FILLED'} if gate else {'live','new','partially_filled'}
    if state not in terminal | active:
        raise APIError('invalid_response')
    filled = dec(data[qty])
    if filled < 0 or filled > dec(i['quantity']):
        raise APIError('invalid_response')
    average=data.get('avg_price' if gate else 'avgPrice')
    try:
        average=str(dec(average)) if average is not None and dec(average)>0 else None
    except ValueError:
        average=None
    return dict(oid=identifier(data[oid]), venue_state=state, filled=str(data[qty]), terminal=state in terminal,
                rejected=state in ('FAIL','REJECT'),average_price=average)


def summarize_fills(rows, i, gate):
    unique, cost, quantity, fee, times = set(), dec(0), dec(0), dec(0), []
    for r in rows:
        oid = str(r['order_id' if gate else 'orderId'])
        if oid != i['oid']:
            continue
        if r['symbol'] != i['instrument']['symbol']:
            raise APIError('invalid_response')
        tid = identifier(r['transaction_id' if gate else 'execId'])
        if tid in unique:
            continue
        unique.add(tid)
        q, p = dec(r['qty' if gate else 'execQty']), dec(r['price' if gate else 'execPrice'])
        if q <= 0 or p <= 0:
            raise APIError('invalid_response')
        quantity += q
        cost += q*p
        if gate:
            if r['fee_coin'] != 'USDT':
                raise APIError('invalid_response')
            fee += dec(r['fee'])
        else:
            if not r['feeDetail']:
                raise APIError('invalid_response')
            for part in r['feeDetail']:
                if part['feeCoin'] != 'USDT' or part.get('deduction','no') != 'no':
                    raise APIError('invalid_response')
                fee += dec(part['fee'])  # UTA v3 documents a positive fee cost.
        times.append(float(dec(r['create_time' if gate else 'createdTime'])))
    if quantity != dec(i['filled']):
        raise APIError('invalid_response')
    return dict(vwap=str(cost/quantity) if quantity else None, fee=str(fee),
                fill_ms=max(times) if times else None, trade_ids=sorted(unique), accounting_pending=False)


class CrossEx:
    def __init__(self, http, mode='DUAL'):
        if mode not in ('SINGLE','DUAL'):
            raise ValueError('invalid_position_mode')
        self.http, self.mode = http, mode

    def place(self, i, before_send=None):
        validate_intent(i)
        data = self.http.request('POST','/api/v4/crossex/orders', body=dict(
            text=i['cid'], symbol=i['instrument']['symbol'], side=i['side'].upper(), type='LIMIT',
            time_in_force='IOC', qty=i['quantity'], price=i['price'],
            reduce_only='true' if i['reduce_only'] else 'false',
            position_side='NONE' if self.mode=='SINGLE' else i['position_side'].upper()),
            before_send=before_send, group='write')
        if data.get('text') != i['cid']:
            raise APIError('invalid_response')
        return identifier(data['order_id'])

    def detail(self, i, deadline=None):
        data = self.http.request('GET','/api/v4/crossex/orders/'+quote(identifier(i.get('oid') or i['cid'])),deadline=deadline)
        return checked_status(data,i,gate=True)

    def cancel(self, i):
        return self.http.request('DELETE','/api/v4/crossex/orders/'+quote(identifier(i.get('oid') or i['cid'])),group='write')

    def fills(self, i):
        rows = []
        end = int(time.time()*1000)+1000
        for page in range(1,21):
            batch = self.http.request('GET','/api/v4/crossex/history_trades',
                {'symbol':i['instrument']['symbol'],'from':int(i['created_at']*1000)-1000,'to':end,'page':page,'limit':1000})
            rows.extend(batch)
            if len(batch) < 1000:
                return summarize_fills(rows,i,True)
        raise APIError('invalid_response')

    def account(self):
        return self.http.request('GET','/api/v4/crossex/accounts',group='account')

    def positions(self):
        return self.http.request('GET','/api/v4/crossex/positions',group='position')

    def fees(self):
        return self.http.request('GET','/api/v4/crossex/fee',group='fee')

    def leverage(self, symbols):
        return self.http.request('GET','/api/v4/crossex/positions/leverage',{'symbols':','.join(symbols)})


class Bitget:
    def __init__(self, http, mode='hedge_mode'):
        if mode not in ('one_way_mode','hedge_mode'):
            raise ValueError('invalid_position_mode')
        self.http, self.mode = http, mode

    def place(self, i, before_send=None):
        validate_intent(i)
        if len(i['cid']) > 32:
            raise ValueError('bitget_client_id_too_long')
        expected = ('sell' if i['position_side']=='long' else 'buy') if i['reduce_only'] else ('buy' if i['position_side']=='long' else 'sell')
        if i['side'] != expected:
            raise ValueError('inconsistent_position_direction')
        data = dict(symbol=i['instrument']['symbol'], category='USDT-FUTURES', marginMode='crossed',
                    qty=i['quantity'], price=i['price'], side=i['side'], orderType='limit',
                    timeInForce='ioc',clientOid=i['cid'])
        if self.mode == 'hedge_mode':
            # UTA closes by opposite side + the existing position side.
            # reduceOnly is documented ONLY for one-way mode.
            data['posSide']=i['position_side']
        else:
            data['reduceOnly']='yes' if i['reduce_only'] else 'no'
        response = self.http.request('POST','/api/v3/trade/place-order',body=data,before_send=before_send,group='write')
        if response.get('clientOid') != i['cid']:
            raise APIError('invalid_response')
        return identifier(response['orderId']) if response.get('orderId') else None

    def detail(self, i, deadline=None):
        params = {'orderId':i['oid']} if i.get('oid') else {'clientOid':i['cid']}
        return checked_status(self.http.request('GET','/api/v3/trade/order-info',params,deadline=deadline),i,gate=False)

    def cancel(self, i):
        body = dict(category='USDT-FUTURES')
        body.update({'orderId':i['oid']} if i.get('oid') else {'clientOid':i['cid']})
        return self.http.request('POST','/api/v3/trade/cancel-order',body=body,group='write')

    def paged(self, path, params):
        rows, seen = [], set()
        params=dict(params,limit=100)
        for _ in range(20):
            data=self.http.request('GET',path,params)
            batch=data['list']
            if not isinstance(batch,list):
                raise APIError('invalid_response')
            rows.extend(batch)
            if len(batch)<100:
                return rows
            cursor=data['cursor']
            if not cursor or cursor in seen:
                raise APIError('invalid_response')
            seen.add(cursor)
            params['cursor']=cursor
        raise APIError('invalid_response')

    def fills(self, i):
        rows=self.paged('/api/v3/trade/fills',dict(category='USDT-FUTURES',orderId=i['oid']))
        return summarize_fills(rows,i,False)

    def account(self):
        return self.http.request('GET','/api/v3/account/assets',group='account')

    def settings(self):
        return self.http.request('GET','/api/v3/account/settings',group='account')

    def positions(self):
        data=self.http.request('GET','/api/v3/position/current-position',
                               dict(category='USDT-FUTURES'),group='position')
        rows=data['list']  # C1 observed null specifically for an empty position list.
        if rows is None:
            return []
        if not isinstance(rows,list):
            raise ValueError('positions_unknown')
        return rows

    def open_orders(self):
        return self.paged('/api/v3/trade/unfilled-orders',dict(category='USDT-FUTURES'))

    def fee(self, symbol):
        return self.http.request('GET','/api/v3/account/fee-rate',
                                 dict(symbol=symbol,category='USDT-FUTURES'),group='fee')['takerFeeRate']
