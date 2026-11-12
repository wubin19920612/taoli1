"""Public spot valuation bounds for external income, never a stablecoin peg.

For native contribution x and median p, subtract the UPPER contribution
    U(x) = x*p + abs(x)*p*0.002
from account equity change. Thus losses use p*0.998, gains p*1.002.
This is a current public valuation estimate, not historical exchange accounting.
USDT/USD retain the executor's existing USD reporting convention.
"""
import re
from statistics import median
from urllib.parse import urlencode

from tools.fill_sim.core import dec
from tools.fill_sim.exchanges import check_response, exact


NATIVE_USD = frozenset(('USDT','USD'))
MAX_AGE = 60
UNCERTAINTY = dec('.002')
VENUES = ('okx','bitget','bybit')


def spot_urls(coin):
    if not isinstance(coin,str) or not re.fullmatch(r'[A-Z0-9]{2,20}',coin):
        raise ValueError('external_fx_currency')
    return (
        ('okx','https://www.okx.com/api/v5/market/ticker?'+urlencode(dict(instId=coin+'-USDT'))),
        ('bitget','https://api.bitget.com/api/v2/spot/market/tickers?'+urlencode(dict(symbol=coin+'USDT'))),
        ('bybit','https://api.bybit.com/v5/market/tickers?'+urlencode(dict(category='spot',symbol=coin+'USDT'))),
    )


def checked_quote(coin,row,now):
    venue=row['venue']
    if (venue not in VENUES or row['base']!=coin or row['quote']!='USDT' or row['market_type']!='spot'
            or row['symbol']!=(coin+'-USDT' if venue=='okx' else coin+'USDT')):
        raise ValueError('external_fx_identity')
    bid,ask=dec(row['bid']),dec(row['ask'])
    at,received=dec(row['at']),dec(row['received_at'])
    if bid<=0 or ask<bid:
        raise ValueError('external_fx_price')
    if at<=0 or not at<=received<=dec(now) or not 0<=dec(now)-at<=MAX_AGE:
        raise ValueError('external_fx_stale')
    return bid,ask,at


def parse_spot_quote(venue,coin,data,received_at):
    check_response(venue,data)
    if venue=='okx':
        row=exact(data['data'],'instId',coin+'-USDT')
        if row['instType']!='SPOT':
            raise ValueError('external_fx_identity')
        symbol,bid,ask,stamp=row['instId'],row['bidPx'],row['askPx'],row['ts']
    elif venue=='bitget':
        row=exact(data['data'],'symbol',coin+'USDT')
        symbol,bid,ask,stamp=row['symbol'],row['bidPr'],row['askPr'],row['ts']
    elif venue=='bybit':
        if data['result']['category']!='spot':
            raise ValueError('external_fx_identity')
        row=exact(data['result']['list'],'symbol',coin+'USDT')
        symbol,bid,ask,stamp=row['symbol'],row['bid1Price'],row['ask1Price'],data['time']
    else:
        raise ValueError('external_fx_identity')
    stamp=dec(stamp)
    if stamp!=stamp.to_integral_value():
        raise ValueError('external_fx_timestamp')
    result=dict(venue=venue,base=coin,quote='USDT',market_type='spot',symbol=symbol,
                bid=str(dec(bid)),ask=str(dec(ask)),at=float(stamp/1000),received_at=received_at)
    checked_quote(coin,result,received_at)
    return result


def quote_band(coin,rows,now):
    if not isinstance(rows,list) or not 2<=len(rows)<=len(VENUES):
        raise ValueError('external_fx_sources')
    seen=set()
    values=[]
    for row in rows:
        values.append(checked_quote(coin,row,now))
        if row['venue'] in seen:
            raise ValueError('external_fx_sources')
        seen.add(row['venue'])
    price=median([(bid+ask)/2 for bid,ask,at in values])
    lower,upper=price*(1-UNCERTAINTY),price*(1+UNCERTAINTY)
    # No outlier trimming or arbitrary peg clamp: if the actual bid/ask range
    # cannot fit inside the claimed uncertainty band, it cannot prove a bound.
    if any(bid<lower or ask>upper for bid,ask,at in values):
        raise ValueError('external_fx_divergence')
    return dict(median=str(price),lower=str(lower),upper=str(upper),
                expires_at=float(min(at for bid,ask,at in values)+MAX_AGE),sources=rows)


def upper_contribution(value,band):
    value=dec(value)
    return value*dec(band['upper'] if value>=0 else band['lower'])


def require_fresh_fx(proofs,now):
    # Revalidate the original source clocks at admission, including final send.
    # The ten-second income certificate never extends the sixty-second FX TTL.
    for coin,proof in proofs.items():
        if quote_band(coin,proof['sources'],now)!=proof:
            raise ValueError('external_fx_evidence')
