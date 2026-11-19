"""Documented public OI; unsupported or unverifiable responses stay missing."""
import math
from urllib.parse import urlencode

from tools.fill_sim.core import dec
from tools.fill_sim.exchanges import check_response, exact


class OIStaleError(ValueError):
    def __init__(self, reason, age_s):
        super().__init__(reason)
        self.age_s = age_s


def record_oi_failure(failures, leg, market, stage, kind, error=None):
    """Append diagnostics without letting formatting or a collector affect admission."""
    if failures is None:
        return
    try:
        row = dict(leg=leg, exchange=market.exchange, raw_symbol=market.raw_symbol, stage=stage, kind=kind)
        if isinstance(error, OIStaleError):
            row['kind'] = 'stale'
            try:
                age = float(round(error.age_s, 1))
                if math.isfinite(age):
                    row['age_s'] = age
            except Exception:
                pass  # The parser already established staleness. The age is optional.
        elif error is not None:
            row['error'] = str(error)[:80]
        failures.append(row)
    except Exception:
        pass


def oi_urls(market):
    raw, ex = market.raw_symbol, market.exchange
    if ex == 'binance':
        return ['https://fapi.binance.com/fapi/v1/openInterest?'+urlencode(dict(symbol=raw)),
                'https://fapi.binance.com/fapi/v1/premiumIndex?'+urlencode(dict(symbol=raw))]
    if ex == 'bitget':
        return ['https://api.bitget.com/api/v2/mix/market/open-interest?'+urlencode(dict(symbol=raw, productType='USDT-FUTURES'))]
    if ex == 'bybit':
        return ['https://api.bybit.com/v5/market/open-interest?'+urlencode(dict(category='linear', symbol=raw, intervalTime='5min', limit=1))]
    if ex == 'okx':
        return ['https://www.okx.com/api/v5/public/open-interest?'+urlencode(dict(instType='SWAP', instId=raw))]
    if ex == 'gate':
        return ['https://api.gateio.ws/api/v4/futures/usdt/tickers?'+urlencode(dict(contract=raw))]
    return []  # No verified public Aster OI route or unit; do not guess one.


def _require_fresh(at, now):
    age = dec(now)-at
    if not 0 <= age <= 300:
        raise OIStaleError('oi_missing', age)


def check_gate_send_oi(instruments, interests, now, stage, bound_failures):
    """A fresh peer can satisfy size_plan on its own. Gate still needs its own fresh receipt."""
    for leg, instrument in enumerate(instruments):
        if instrument.market.exchange != 'gate':
            continue
        try:
            at = dec(interests[leg]['at'])
        except (IndexError, KeyError, TypeError, ValueError):
            raise ValueError('oi_missing') from None
        try:
            _require_fresh(at, now)
        except OIStaleError as error:
            try:
                failures = []
                record_oi_failure(failures, leg, instrument.market, stage, 'stale', error)
                if failures:
                    error.gate_oi_details = {'oi_failures': bound_failures(failures)}
            except Exception:
                pass  # Diagnostics cannot replace the send rejection.
            raise


def parse_oi(market, payloads, price, now, *, contract_size=None, received_at=None):
    """Gate uses the original receipt time and has no midpoint until a book is available."""
    ex, raw = market.exchange, market.raw_symbol
    data = payloads if ex == 'binance' else payloads[0]
    if ex != 'gate' or price is not None:
        price = dec(price)
        if price <= 0:
            raise ValueError('oi_price_invalid')
    evidence = {}
    if ex == 'binance':
        try:
            live, mark = data
            live = exact([live], 'symbol', raw)
            mark = exact([mark], 'symbol', raw)
            quantity, at = dec(live['openInterest']), dec(live['time'])/1000
            mark_price, mark_at = dec(mark['markPrice']), dec(mark['time'])/1000
            value = quantity*mark_price
        except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError):
            raise ValueError('oi_unit_unverified') from None
        if min(quantity, mark_price, value) <= 0:
            raise ValueError('oi_unit_unverified')
        mark_age = dec(now)-mark_at
        if not 0 <= mark_age <= 300:
            raise OIStaleError('oi_unit_unverified', mark_age)
        unit = 'native_base'
        evidence = dict(quantity=str(quantity), mark_price=str(mark_price), mark_at=str(mark_at), quote='USDT')
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
        if not isinstance(row, dict) or row.get('contract') != raw:
            raise ValueError('oi_identity_mismatch')
        try:
            total_size = dec(row['total_size'])
            multiplier = dec(row['quanto_multiplier'])
            mark_price = dec(row['mark_price'])
            catalog_size = dec(contract_size)
        except (KeyError, ValueError):
            raise ValueError('oi_unit_unverified') from None
        if total_size < 0 or multiplier <= 0 or mark_price <= 0 or multiplier != catalog_size:
            raise ValueError('oi_unit_unverified')
        if price is not None and abs(mark_price/price-1) > dec('.01'):
            raise ValueError('oi_unit_unverified')
        if received_at is None:
            raise ValueError('oi_missing')
        value, at, unit = total_size*multiplier*mark_price, dec(received_at), 'USDT'
        evidence = dict(total_size=str(total_size), quanto_multiplier=str(multiplier), mark_price=str(mark_price))
    else:
        raise ValueError('oi_unit_unverified')
    if value < 0:
        raise ValueError('oi_missing')
    _require_fresh(at, now)
    return dict(market_key=market.key, usd=str(value), at=float(at), unit=unit, unit_verified=True,
                source=oi_urls(market)[0], evidence=evidence)
