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
    oid, cid, qty = ('order_id','text','executed_qty') if gate else ('orderId','clientOid','baseVolume')
    if (data.get('symbol') != i['instrument']['symbol'] or data.get(cid) != i['cid']
            or (i.get('oid') and str(data.get(oid)) != i['oid'])):
        raise APIError('invalid_response')
    state = data['state']
    terminal = {'FILLED','CANCELLED','FAIL','REJECT'} if gate else {'filled','canceled'}
    active = {'NEW','OPEN','PARTIALLY_FILLED'} if gate else {'live','partially_filled'}
    if state not in terminal | active:
        raise APIError('invalid_response')
    filled = dec(data[qty])
    if filled < 0 or filled > dec(i['quantity']):
        raise APIError('invalid_response')
    return dict(oid=identifier(data[oid]), venue_state=state, filled=str(data[qty]), terminal=state in terminal,
                rejected=state in ('FAIL','REJECT'))


def summarize_fills(rows, i, gate):
    unique, cost, quantity, fee, times = set(), dec(0), dec(0), dec(0), []
    for r in rows:
        oid = str(r['order_id' if gate else 'orderId'])
        if oid != i['oid']:
            continue
        if r['symbol'] != i['instrument']['symbol']:
            raise APIError('invalid_response')
        tid = identifier(r['transaction_id' if gate else 'tradeId'])
        if tid in unique:
            continue
        unique.add(tid)
        q, p = dec(r['qty' if gate else 'baseVolume']), dec(r['price'])
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
                fee -= dec(part['totalFee'])  # Bitget cash-flow sign, Gate cost sign.
        times.append(float(dec(r['create_time' if gate else 'cTime'])))
    if quantity != dec(i['filled']):
        raise APIError('invalid_response')
    return dict(vwap=str(cost/quantity) if quantity else None, fee=str(fee),
                fill_ms=max(times) if times else None, trade_ids=sorted(unique), accounting_pending=False)


class CrossEx:
    def __init__(self, http, mode='SINGLE'):
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
    def __init__(self, http, mode='one_way_mode'):
        if mode not in ('one_way_mode','hedge_mode'):
            raise ValueError('invalid_position_mode')
        self.http, self.mode = http, mode

    def place(self, i, before_send=None):
        validate_intent(i)
        data = dict(symbol=i['instrument']['symbol'], productType='USDT-FUTURES', marginMode='crossed',
                    marginCoin='USDT', size=i['quantity'], price=i['price'], orderType='limit', force='ioc',clientOid=i['cid'])
        if self.mode == 'hedge_mode':
            data.update(side='buy' if i['position_side']=='long' else 'sell',tradeSide='close' if i['reduce_only'] else 'open')
        else:
            data.update(side=i['side'],reduceOnly='YES' if i['reduce_only'] else 'NO')
        response = self.http.request('POST','/api/v2/mix/order/place-order',body=data,before_send=before_send,group='write')
        if response.get('clientOid') != i['cid']:
            raise APIError('invalid_response')
        # Documented reduce-only ACK may omit orderId; immediately query clientOid.
        return identifier(response['orderId']) if response.get('orderId') else None

    def detail(self, i, deadline=None):
        params = dict(symbol=i['instrument']['symbol'],productType='USDT-FUTURES')
        params.update({'orderId':i['oid']} if i.get('oid') else {'clientOid':i['cid']})
        return checked_status(self.http.request('GET','/api/v2/mix/order/detail',params,deadline=deadline),i,gate=False)

    def cancel(self, i):
        body = dict(symbol=i['instrument']['symbol'],productType='USDT-FUTURES',marginCoin='USDT')
        body.update({'orderId':i['oid']} if i.get('oid') else {'clientOid':i['cid']})
        return self.http.request('POST','/api/v2/mix/order/cancel-order',body=body,group='write')

    def fills(self, i):
        rows, cursor = [], None
        for _ in range(20):
            params = dict(symbol=i['instrument']['symbol'],productType='USDT-FUTURES',orderId=i['oid'],limit=100)
            if cursor:
                params['idLessThan'] = cursor
            data = self.http.request('GET','/api/v2/mix/order/fills',params)
            rows.extend(data['fillList'])
            if len(data['fillList']) < 100:
                return summarize_fills(rows,i,False)
            new_cursor = data['endId']
            if not new_cursor or new_cursor == cursor:
                break
            cursor = new_cursor
        raise APIError('invalid_response')

    def account(self, symbol):
        return self.http.request('GET','/api/v2/mix/account/account',
                                 dict(symbol=symbol,productType='USDT-FUTURES',marginCoin='USDT'),group='account')

    def positions(self):
        return self.http.request('GET','/api/v2/mix/position/all-position',
                                 dict(productType='USDT-FUTURES',marginCoin='USDT'),group='position')

    def fee(self, symbol):
        return self.http.request('GET','/api/v2/common/trade-rate',dict(symbol=symbol,businessType='mix'),group='fee')['takerFeeRate']

    def fees(self):
        return self.http.request('GET','/api/v2/common/all-trade-rate',dict(businessType='mix'),group='fee')
