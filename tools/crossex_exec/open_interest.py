"""Documented public OI; unsupported or unverifiable responses stay missing."""
from urllib.parse import urlencode

from tools.fill_sim.core import dec
from tools.fill_sim.exchanges import check_response, exact


def oi_urls(market):
    raw, ex = market.raw_symbol, market.exchange
    if ex == 'binance':
        return ['https://fapi.binance.com/futures/data/openInterestHist?'+urlencode(dict(symbol=raw, period='5m', limit=1)),
                'https://fapi.binance.com/fapi/v1/premiumIndex?'+urlencode(dict(symbol=raw))]
    if ex == 'bitget':
        return ['https://api.bitget.com/api/v2/mix/market/open-interest?'+urlencode(dict(symbol=raw, productType='USDT-FUTURES'))]
    if ex == 'bybit':
        return ['https://api.bybit.com/v5/market/open-interest?'+urlencode(dict(category='linear', symbol=raw, intervalTime='5min', limit=1))]
    if ex == 'okx':
        return ['https://www.okx.com/api/v5/public/open-interest?'+urlencode(dict(instType='SWAP', instId=raw))]
    if ex == 'gate':
        return ['https://api.gateio.ws/api/v4/futures/usdt/contract_stats?'+urlencode(dict(contract=raw, interval='5m', limit=1))]
    return []  # No verified public Aster OI route or unit; do not guess one.


def parse_oi(market, payloads, price, now):
    ex, raw = market.exchange, market.raw_symbol
    data = payloads[0]
    price = dec(price)
    if price <= 0:
        raise ValueError('oi_price_invalid')
    evidence = {}
    if ex == 'binance':
        row = exact(data, 'symbol', raw)
        mark = exact([payloads[1]], 'symbol', raw)
        quantity, value, mark_price = dec(row['sumOpenInterest']), dec(row['sumOpenInterestValue']), dec(mark['markPrice'])
        if quantity <= 0 or value <= 0 or mark_price <= 0 or not 0 <= dec(now)-dec(mark['time'])/1000 <= 300:
            raise ValueError('oi_unit_unverified')
        implied = value/quantity
        if abs(implied/mark_price-1) > dec('.01'):
            raise ValueError('oi_unit_unverified')
        at, unit = dec(row['timestamp'])/1000, 'native_base'
        evidence = dict(quantity=str(quantity), implied_price=str(implied), mark_price=str(mark_price),
                        mark_at=str(dec(mark['time'])/1000), quote='USDT')
    elif ex == 'bitget':
        check_response(ex, data)
        if not isinstance(data.get('data'), dict):
            raise ValueError('oi_v2_shape_required')
        row = exact(data['data']['openInterestList'], 'symbol', raw)
        value = dec(row['size'])*price
        at, unit = dec(data['data']['ts'])/1000, 'base'
        evidence = dict(quantity=row['size'], price=str(price), quote='USDT')
    elif ex == 'bybit':
        check_response(ex, data)
        if data['result']['symbol'] != raw or data['result']['category'] != 'linear':
            raise ValueError('oi_identity_mismatch')
        rows = data['result']['list']
        if len(rows) != 1:
            raise ValueError('oi_ambiguous')
        row = rows[0]
        value, at, unit = dec(row['openInterest'])*price, dec(row['timestamp'])/1000, 'base_both_sides'
        evidence = dict(quantity=row['openInterest'], price=str(price), quote='USDT')
    elif ex == 'okx':
        check_response(ex, data)
        row = exact(data['data'], 'instId', raw)
        value, at, unit = dec(row['oiUsd']), dec(row['ts'])/1000, 'USD'
    elif ex == 'gate':
        if not isinstance(data, list) or len(data) != 1:
            raise ValueError('oi_ambiguous')
        row = data[0]
        if row.get('contract', raw) != raw:
            raise ValueError('oi_identity_mismatch')
        value, at, unit = dec(row['open_interest_usd']), dec(row['time']), 'USDT'
    else:
        raise ValueError('oi_unit_unverified')
    if value < 0 or not 0 <= dec(now)-at <= 300:
        raise ValueError('oi_missing')
    return dict(market_key=market.key, usd=str(value), at=float(at), unit=unit, unit_verified=True,
                source=oi_urls(market)[0], evidence=evidence)
