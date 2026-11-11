"""Public market data via fill-sim's bounded transport, parsers and send scheduler."""
import time
import threading
from urllib.parse import urlencode
from tools.fill_sim.core import Market, dec
from tools.fill_sim.exchanges import endpoint, parse_spec, parse_book, exact, check_response
from tools.fill_sim.funding import funding_urls, parse_funding
from tools.fill_sim.network import PublicClient, Limiter
from tools.fill_sim.scheduler import RequestQueue, DaemonPool
from .policy import mapped_instrument

RULES_URL='https://api.gateio.ws/api/v4/crossex/rule/symbols'
CATALOGS={
    'gate':'https://api.gateio.ws/api/v4/futures/usdt/contracts',
    'binance':'https://fapi.binance.com/fapi/v1/exchangeInfo',
    'aster':'https://fapi.asterdex.com/fapi/v3/exchangeInfo',
    'bybit':'https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000',
    'okx':'https://www.okx.com/api/v5/public/instruments?instType=SWAP',
    'bitget':'https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES',
}


def native_rows(ex,data):
    if ex=='gate':
        return data
    if ex in ('binance','aster'):
        return data['symbols']
    if ex=='bybit':
        return data['result']['list']
    return data['data']


def native_instrument(ex,row,rules):
    raw=row['name' if ex=='gate' else 'instId' if ex=='okx' else 'symbol']
    market=Market.from_payload(dict(exchange=ex,market_type='future',raw_symbol=raw))
    if ex=='gate':
        data=row
        tick=row['order_price_round']
    elif ex in ('binance','aster'):
        data={'symbols':[row]}
        tick=exact(row['filters'],'filterType','PRICE_FILTER')['tickSize']
    elif ex=='bybit':
        data={'retCode':0,'result':{'list':[row]}}
        tick=row['priceFilter']['tickSize']
    elif ex=='okx':
        if row['instType']!='SWAP':
            raise ValueError('not_perpetual')
        data={'code':'0','data':[row]}
        tick=row['tickSz']
    else:
        if row.get('symbolType')!='perpetual':
            raise ValueError('not_perpetual')
        data={'code':'00000','data':[row]}
        tick=dec(row['priceEndStep'])*dec(10)**-int(row['pricePlace'])
    spec=parse_spec(market,data)
    # Reject every inferred rename / 1000x alias, preserving exact native identity.
    expected=(spec.base+'_USDT' if ex=='gate' else spec.base+'-USDT-SWAP' if ex=='okx' else spec.base+'USDT')
    if raw!=expected or dec(tick)<=0:
        raise ValueError('native_identity_mismatch')
    return mapped_instrument(market,spec,tick,rules)


def history_url(m,now):
    raw,ex=m.raw_symbol,m.exchange
    if ex in ('binance','aster'):
        root='https://fapi.binance.com/fapi/v1/' if ex=='binance' else 'https://fapi.asterdex.com/fapi/v3/'
        return root+'fundingRate?'+urlencode(dict(symbol=raw,startTime=int((now-172800)*1000),endTime=int(now*1000),limit=1000))
    if ex=='gate':
        return 'https://api.gateio.ws/api/v4/futures/usdt/funding_rate?'+urlencode(dict(contract=raw,limit=1000,**{'from':int(now-172800),'to':int(now)}))
    if ex=='bybit':
        return 'https://api.bybit.com/v5/market/funding/history?'+urlencode(dict(category='linear',symbol=raw,endTime=int(now*1000),limit=200))
    if ex=='okx':
        return 'https://www.okx.com/api/v5/public/funding-rate-history?'+urlencode(dict(instId=raw,limit=100))
    return 'https://api.bitget.com/api/v2/mix/market/history-fund-rate?'+urlencode(dict(symbol=raw,productType='USDT-FUTURES',pageSize=100,pageNo=1))


def parse_history(m,data,snapshot,now):
    ex=m.exchange
    if ex not in ('gate','binance','aster'):
        check_response(ex,data)
    rows=data if isinstance(data,list) else data['result']['list'] if ex=='bybit' else data['data']
    entries={}
    for row in rows:
        if ex=='gate':
            ts,rate=dec(row['t']),dec(row['r'])
        else:
            field='fundingRateTimestamp' if ex=='bybit' else 'fundingTime'
            ts,rate=dec(row[field])/1000,dec(row['realizedRate' if ex=='okx' else 'fundingRate'])
            if ex=='okx' and row['instId']!=m.raw_symbol:
                raise ValueError('history_identity_mismatch')
            if ex in ('binance','aster') and row['symbol']!=m.raw_symbol:
                raise ValueError('history_identity_mismatch')
            if 'symbol' in row and row['symbol']!=m.raw_symbol:
                raise ValueError('history_identity_mismatch')
        if ts>dec(now)+5 or (ts in entries and entries[ts]!=rate):
            raise ValueError('history_invalid')
        entries[ts]=rate
    times=sorted(entries)
    interval=dec(snapshot['interval'])
    # Need an anchor BEFORE the 24h window; no empty history / truncated-page pass.
    covered=bool(times and times[0]<=dec(now)-86400 and dec(now)-times[-1]<=interval+90)
    relevant=[t for t in times if t>=dec(now)-86400-interval]
    if any(b-a>interval+90 for a,b in zip(relevant,relevant[1:])):
        covered=False
    maximum=max([abs(v) for t,v in entries.items() if dec(now)-86400<=t<=dec(now)]+[abs(dec(snapshot['rate']))])
    return dict(snapshot,history_ok=covered,max_abs_24h=str(maximum),history_captured_at=now)


def ticker_url(m):
    ex,raw=m.exchange,m.raw_symbol
    if ex in ('binance','aster'):
        root='https://fapi.binance.com/fapi/v1/' if ex=='binance' else 'https://fapi.asterdex.com/fapi/v3/'
        return root+'ticker/24hr?'+urlencode(dict(symbol=raw))
    if ex=='gate':
        return 'https://api.gateio.ws/api/v4/futures/usdt/tickers?'+urlencode(dict(contract=raw))
    if ex=='bybit':
        return 'https://api.bybit.com/v5/market/tickers?'+urlencode(dict(category='linear',symbol=raw))
    if ex=='okx':
        return 'https://www.okx.com/api/v5/market/ticker?'+urlencode(dict(instId=raw))
    return 'https://api.bitget.com/api/v2/mix/market/ticker?'+urlencode(dict(symbol=raw,productType='USDT-FUTURES'))


def turnover(m,data):
    ex=m.exchange
    if ex=='gate':
        return str(dec(exact(data,'contract',m.raw_symbol)['volume_24h_quote']))
    check_response(ex,data)
    if ex in ('binance','aster'):
        return str(dec(exact([data],'symbol',m.raw_symbol)['quoteVolume']))
    if ex=='bybit':
        return str(dec(exact(data['result']['list'],'symbol',m.raw_symbol)['turnover24h']))
    if ex=='bitget':
        return str(dec(exact(data['data'],'symbol',m.raw_symbol)['usdtVolume']))
    row=exact(data['data'],'instId',m.raw_symbol)
    # OKX reports base volume: daily low gives a conservative USD lower bound.
    low=dec(row['low24h'])
    if low<=0:
        raise ValueError('turnover_unknown')
    return str(dec(row['volCcy24h'])*low)


class MarketData:
    def __init__(self,store,clock=time.time,client=None):
        self.store,self.clock=store,clock
        self.client=client or PublicClient(Limiter(state=store.meta('public_limiter')))
        self.queue=RequestQueue(self.client,clock)
        self.pool=DaemonPool(max_workers=4,thread_name_prefix='exec-public')
        self.mapping={}
        self.catalog_errors={}
        self._pump_lock=threading.Lock()
        self._mapping_lock=threading.Lock()
        self.closed=False

    def close(self):
        with self._mapping_lock:
            self.closed=True
            self.store.set_meta('public_limiter',self.client.limiter.snapshot())
        self.queue.close()
        self.pool.shutdown(wait=False)

    def batch(self,items,priority=1,partial=False):
        jobs=[self.queue.submit(ex,url,priority,convert) for ex,url,convert in items]
        deadline=time.monotonic()+10
        while not all(j.done() for j in jobs) and time.monotonic()<deadline:
            with self._pump_lock:
                self.queue.pump(self.pool)
            time.sleep(.01)
        results=[]
        for j in jobs:
            try:
                if not j.done():
                    j.cancel()
                    raise TimeoutError('public_deadline')
                results.append(j.result())
            except Exception:
                if not partial:
                    raise ValueError('public_data_unavailable') from None
                results.append(None)
        return results

    def fetch(self,ex,url,priority=1):
        return self.batch([(ex,url,None)],priority=priority)[0]

    def bootstrap(self,bases=()):
        if not bases:
            self.publish_mapping({},{})
            return self.mapping
        try:
            rules=self.fetch('gate',RULES_URL,priority=2)
        except Exception:
            rules=None
        mapped={k:i for k,i in self.mapping.items() if i.spec.base in bases}
        errors={}
        for ex,url in CATALOGS.items():
            if self.closed:
                return self.mapping
            venue={}
            ambiguous=set()
            try:
                if rules is None and ex!='bitget':
                    raise ValueError('crossex_rules_unavailable')
                data=self.fetch(ex,url,priority=2)
                pages=0
                while True:
                    for row in native_rows(ex,data):
                        try:
                            i=native_instrument(ex,row,rules)
                            if i.spec.base in bases:
                                if i.market.key in venue or i.market.key in ambiguous:
                                    ambiguous.add(i.market.key)
                                    venue.pop(i.market.key,None)
                                    continue
                                venue[i.market.key]=i
                        except (ValueError,KeyError,TypeError):
                            continue
                    cursor=data.get('result',{}).get('nextPageCursor') if ex=='bybit' else None
                    if not cursor:
                        break
                    pages+=1
                    if pages>=10:
                        raise ValueError('catalog_pagination_incomplete')
                    data=self.fetch(ex,url+'&'+urlencode(dict(cursor=cursor)),priority=2)
            except Exception:
                venue={}
                errors[ex]='catalog_unavailable'
            mapped={k:i for k,i in mapped.items() if i.market.exchange!=ex}
            mapped.update(venue)
            self.publish_mapping(mapped,errors)
        return mapped

    def publish_mapping(self,mapped,errors):
        with self._mapping_lock:
            if not self.closed:
                self.mapping=dict(mapped)
                self.catalog_errors=dict(errors)
                self.store.set_meta('mapping',dict(at=self.clock(),errors=errors,instruments={k:v.dump() for k,v in mapped.items()}))

    def books(self,instruments):
        return self.batch([(i.market.exchange,endpoint(i.market,'depth').replace('/fapi/v1/','/fapi/v3/')
                            if i.market.exchange=='aster' else endpoint(i.market,'depth'),
                            lambda d,i=i:parse_book(i.market,i.spec,d,self.clock())) for i in instruments],priority=0,partial=True)

    def funding(self,instruments):
        results=[]
        for i in instruments:
            m=i.market
            urls=funding_urls(m)
            data=self.batch([(m.exchange,u,None) for u in urls])
            snapshot=parse_funding(m,data[0],self.clock(),data[1] if len(data)>1 else None)
            history=self.fetch(m.exchange,history_url(m,self.clock()))
            results.append(parse_history(m,history,snapshot,self.clock()))
        return results

    def current_funding(self,instruments):
        results=[]
        for i in instruments:
            urls=funding_urls(i.market)
            data=self.batch([(i.market.exchange,u,None) for u in urls],priority=0)
            results.append(parse_funding(i.market,data[0],self.clock(),data[1] if len(data)>1 else None))
        return results

    def turnovers(self,instruments):
        return self.batch([(i.market.exchange,ticker_url(i.market),lambda d,m=i.market:turnover(m,d)) for i in instruments])
