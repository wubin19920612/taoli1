"""Native contract identity and conservative USD position valuation.

This does not grant trading/registration eligibility. Unknown units fail closed.
USDT/USD use the existing reporting convention; all other quotes need public FX.
"""
import re

from tools.fill_sim.core import dec
from .external_fx import NATIVE_USD, quote_band


def position_identity(route,symbol,row=None,*,expected=None):
    row=row or {}
    if not isinstance(symbol,str) or len(symbol)>160:
        raise ValueError('position_identity_unknown')
    if route=='crossex':
        match=re.fullmatch(r'([A-Z][A-Z0-9]*)_FUTURE_([^|\s_]+)_([A-Z0-9]{2,20})',symbol)
        if not match:
            raise ValueError('position_identity_unknown')
        exchange,base,quote=match.groups()
        dex=base.split(':')[0] if ':' in base else ''
        if exchange=='HYPERLIQUID' and ':' not in base:
            # Some exact CrossEx symbols omit the DEX prefix. Ownership callers
            # supply the catalog-proven identity; explicit row tags must agree.
            dex=(expected['dex'] if expected is not None else row.get('tag',row.get('dex','')))
            if not isinstance(dex,str) or (dex and not re.fullmatch(r'[A-Za-z0-9-]{1,64}',dex)):
                raise ValueError('position_identity_unknown')
        if (':' in base and (exchange!='HYPERLIQUID' or base.count(':')!=1 or not all(base.split(':')))
                or any(ord(c)<32 for c in base)):
            raise ValueError('position_identity_unknown')
        result=dict(route=route,exchange_type=exchange,business='FUTURE',symbol=symbol,quote=quote,dex=dex)
        if expected is not None and result!=expected:
            raise ValueError('position_identity_unknown')
        for raw,field in (('exchange_type','exchange_type'),('business_type','business'),
                          ('tag','dex'),('dex','dex'),('quote','quote'),('business','business'),('route','route')):
            if raw in row and row[raw]!=result[field]:
                raise ValueError('position_identity_unknown')
        return result
    if route=='bitget':
        match=re.fullmatch(r'[A-Za-z0-9]+(USDT|USDC)',symbol)
        if not match:
            raise ValueError('position_identity_unknown')
        quote=match.group(1)
        business=quote+'-FUTURES'
        if row.get('category',business)!=business or row.get('marginCoin',quote)!=quote:
            raise ValueError('position_identity_unknown')
        return dict(route=route,exchange_type='BITGET',business=business,symbol=symbol,quote=quote,dex='')
    raise ValueError('position_identity_unknown')


def verify_external_rows(snapshot, identities):
    """One identity parser for registration, risk attribution and the public view.

    Official position rows may omit exchange/tag: the exact raw symbol supplies
    those fields. Any explicit field must agree with it AND the registered rule.
    Both DUAL directions are checked; quantity normalization never nets them.
    """
    from .accounts import remote_positions
    raw=snapshot['raw_positions']
    if remote_positions(raw['crossex'],raw['bitget'])!={k:dec(v) for k,v in snapshot['positions'].items()}:
        raise ValueError('raw_positions_disagree')
    expected={(i['route'],i['symbol']):i for i in identities}
    for route,rows in raw.items():
        for row in rows:
            target=(route,row['symbol'])
            if target in expected and position_identity(route,row['symbol'],row,expected=expected[target])!=expected[target]:
                raise ValueError('position_identity_unknown')


def key_currency(key):
    route,symbol,side=key.split('|')
    if side not in ('long','short'):
        raise ValueError('position_identity_unknown')
    return position_identity(route,symbol)['quote']


def usd_value(value,coin,fx,now,*,upper):
    value=dec(value)
    if coin in NATIVE_USD:
        return value
    band=quote_band(coin,fx.get(coin,[]),now)
    # Exposure is positive and uses H; displayed PnL uses its lower bound:
    # gains * L, losses * H. No peg and no reuse after source expiry.
    bound='upper' if (value>=0)==upper else 'lower'
    return value*dec(band[bound])


def notional_values(snapshot,now):
    result={}
    fx=snapshot.get('external_fx',{})
    for key,value in snapshot['notionals'].items():
        value=dec(value)
        if value<0:
            raise ValueError('negative_notional')
        result[key]=str(usd_value(value,key_currency(key),fx,now,upper=True))
    return result


def value_snapshot(snapshot,now):
    from .accounts import isolated_margin_coverage
    snapshot=isolated_margin_coverage(snapshot,now)
    result=dict(snapshot,notionals_usd={},notional_valuation_verified=False)
    try:
        result['notionals_usd']=notional_values(snapshot,now)
        result['notional_valuation_verified']=True
    except (ValueError,KeyError,TypeError,ArithmeticError):
        # Risk account evidence must survive valuation failure for hard exits.
        result['notional_valuation_error']='position_fx_unavailable'
    return result
