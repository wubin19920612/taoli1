"""Pure Decimal execution policy. All sizes are base units, all rates fractions."""
from dataclasses import dataclass, asdict, replace
from decimal import ROUND_CEILING, ROUND_FLOOR
from tools.fill_sim.core import Market, Spec, common_step, opening, dec


@dataclass
class Instrument:
    market: Market
    spec: Spec
    symbol: str
    tick: str

    def dump(self):
        return asdict(self)

    @classmethod
    def load(cls, row):
        return cls(Market(**row['market']),Spec(**row['spec']),row['symbol'],row['tick'])


def mapped_instrument(market, spec, native_tick, rules):
    spec.validate(market)
    if market.market_type != 'future' or dec(spec.price_multiplier) != 1:
        raise ValueError('unsupported_market')
    if market.exchange == 'bitget':
        return Instrument(market,spec,market.raw_symbol,str(native_tick))
    symbol = market.exchange.upper()+'_FUTURE_'+spec.base+'_USDT'
    matches = [r for r in rules if r['symbol']==symbol]
    if len(matches)!=1:
        raise ValueError('mapping_missing_or_ambiguous')
    r = matches[0]
    if (r['state']!='live' or r['business_type']!='FUTURE' or r['exchange_type']!=market.exchange.upper()
            or str(r.get('delist_time'))!='0' or r.get('tag') or r.get('support_cross')!='true'):
        raise ValueError('mapping_not_live')
    # CrossEx v1.0.8: base quantity uniformly; deprecated contract_size is NOT applied.
    for key in ('lot_size','min_size','max_limit_size','tick_size'):
        if dec(r[key]) <= 0:
            raise ValueError('invalid_crossex_rule')
    spec = replace(spec,qty_step=str(common_step(spec.qty_step,r['lot_size'])),
                   min_qty=str(max(dec(spec.min_qty),dec(r['min_size']))),
                   min_notional=str(max(dec(spec.min_notional),dec(r['min_notional'] or 0))),
                   max_qty=str(min(dec(spec.max_qty),dec(r['max_limit_size']))))
    return Instrument(market,spec,symbol,str(common_step(native_tick,r['tick_size'])))


def limit_price(deepest, side, tick, bps=3):
    tick = dec(tick)
    if tick <= 0:
        raise ValueError('invalid_tick')
    value = dec(deepest)*(1+dec(bps)/10000*(1 if side=='buy' else -1))
    # Round outward so the specified worst level remains executable.
    price = (value/tick).to_integral_value(rounding=ROUND_CEILING if side=='buy' else ROUND_FLOOR)*tick
    if price <= 0:
        raise ValueError('invalid_price')
    return format(price,'f')


def review(instruments, books, fees, funding, turnovers, now):
    if len(instruments)!=2 or len(books)!=2 or len(funding)!=2 or len(fees)!=2:
        raise ValueError('two_legs_required')
    if instruments[0].spec.base!=instruments[1].spec.base or instruments[0].market.exchange==instruments[1].market.exchange:
        raise ValueError('identity_mismatch')
    if any(dec(v)<5000000 for v in turnovers) or len(turnovers)!=2:
        raise ValueError('turnover_below_minimum')
    for i,b,f,s in zip(instruments,books,fees,funding):
        i.spec.validate(i.market)
        if i.market.market_type!='future' or f is None or not 0<=dec(f)<dec('.02'):
            raise ValueError('fee_or_market_invalid')
        if not 0<=now-b.received_at<=3 or (b.exchange_at is not None and not -5<=now-b.exchange_at<=3):
            raise ValueError('stale_book')
        if (not s or not s.get('history_ok') or not 0<=now-s['captured_at']<=300
                or s['next']-now<1800 or s['interval']<=0):
            raise ValueError('funding_unknown_or_near')
        if max(abs(dec(s['rate'])),dec(s['max_abs_24h']))>dec('.005'):
            raise ValueError('funding_blacklist')
    # Count one settlement per leg, explicitly retaining unequal cycle lengths.
    # Do not use expected income to rescue a failing spread.
    funding_cost=max(dec(0),dec(funding[0]['rate'])-dec(funding[1]['rate']))*10000
    result=opening(*books,instruments[0].spec,instruments[1].spec,100,*fees,threshold=15+funding_cost)
    if result['status']!='opened':
        raise ValueError('review_rejected')
    result['net_bps']=float(dec(result['net_bps'])-funding_cost)
    result['funding_cost_bps']=str(funding_cost)
    result['limits']=[limit_price(result[k]['deepest'],side,i.tick) for i,k,side in
                      zip(instruments,('buy','sell'),('buy','sell'))]
    return result


def exit_reason(*, age, spread, entry_spread, pnl, notional, next_funding, stale_age):
    if stale_age>=60:
        return 'stale_exit'
    if age>=7200:
        return 'max_age'
    if next_funding<300:
        return 'funding_exit'
    if dec(pnl)<=-dec(notional)*dec('.01'):
        return 'loss_limit'
    if dec(spread)>=dec(entry_spread)+50:
        return 'adverse_spread'
    if dec(spread)<=dec(entry_spread)/2:
        return 'converged'
    return None


def risk_decision(*, daily, total, margins, single_failures, rejected, attempted, reconciled):
    if dec(total)<=-100:
        return 'HALTED'
    if any(v is not None and dec(v)<2 for v in margins):
        return 'FLATTEN'
    if (dec(daily)<=-20 or not reconciled or single_failures>=3
            or any(v is None or dec(v)<3 for v in margins) or len(margins)!=2
            or (attempted and dec(rejected)/dec(attempted)>dec('.3'))):
        return 'STOP'
    return 'RUN'
