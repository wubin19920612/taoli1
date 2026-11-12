"""Batch 24h turnover, separate from best-bid/ask observations when required."""
from tools.fill_sim.core import dec
from tools.fill_sim.exchanges import check_response


VOLUME_URLS = {
    'binance': 'https://fapi.binance.com/fapi/v1/ticker/24hr',
    'aster': 'https://fapi.asterdex.com/fapi/v3/ticker/24hr',
    'bitget': 'https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES',
    'bybit': 'https://api.bybit.com/v5/market/tickers?category=linear',
    'okx': 'https://www.okx.com/api/v5/market/tickers?instType=SWAP',
    'gate': 'https://api.gateio.ws/api/v4/futures/usdt/tickers',
}


def batch_rows(ex, data):
    check_response(ex, data)
    if ex == 'bybit':
        if data['result']['category'] != 'linear':
            raise ValueError('volume_category_invalid')
        return data['result']['list']
    return data if ex in ('binance', 'aster', 'gate') else data['data']


def parse_batch(ex, data, instruments, now, bbo=None):
    rows = batch_rows(ex, data)
    names = {i.market.raw_symbol: i for i in instruments if i.market.exchange == ex}
    key = 'contract' if ex == 'gate' else 'instId' if ex == 'okx' else 'symbol'
    seen, result = set(), {}
    quotes = {}
    ambiguous = set()
    for row in bbo or []:
        raw = row.get('symbol')
        if raw in quotes:
            ambiguous.add(raw)
        quotes[raw] = row
    for row in rows:
        raw = row.get(key)
        if raw not in names or raw in ambiguous:
            continue
        identity = names[raw].market.key
        if raw in seen:
            result.pop(identity, None)
            continue
        seen.add(raw)
        try:
            if ex in ('binance', 'aster'):
                volume = dec(row['quoteVolume'])
                quote = quotes[raw]
                bid, ask = dec(quote['bidPrice']), dec(quote['askPrice'])
                times = [dec(row['closeTime'])/1000, dec(quote['time'])/1000]
            elif ex == 'bitget':
                volume, bid, ask = dec(row['usdtVolume']), dec(row['bidPr']), dec(row['askPr'])
                times = [dec(row['ts'])/1000]
            elif ex == 'bybit':
                volume, bid, ask = dec(row['turnover24h']), dec(row['bid1Price']), dec(row['ask1Price'])
                times = [dec(data['time'])/1000]
            elif ex == 'okx':
                if row['instType'] != 'SWAP' or dec(row['low24h']) <= 0:
                    continue
                volume = dec(row['volCcy24h'])*dec(row['low24h'])
                bid, ask = dec(row['bidPx']), dec(row['askPx'])
                times = [dec(row['ts'])/1000]
            else:
                volume, bid, ask = dec(row['volume_24h_quote']), dec(row['highest_bid']), dec(row['lowest_ask'])
                times = [dec(now)]  # Gate batch ticker has no per-row server clock.
            if volume < 0 or not 0 < bid <= ask or any(not 0 <= dec(now)-t <= 300 for t in times):
                continue
            result[identity] = dict(mid=str((bid+ask)/2), volume=str(volume), at=float(min([dec(now)]+times)),
                received_at=now, volume_basis='base_times_low24h_lower_bound' if ex == 'okx' else 'quote_volume',
                quote='USDT', market_key=identity)
        except (ValueError, KeyError, TypeError):
            continue
    return result
