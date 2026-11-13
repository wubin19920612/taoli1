"""Account-book cash domains are distinct from the underlying contract venue.

Only cash rows may use the observed CROSSEX/USDT settlement domain. Position,
trade, catalog and registration identities retain their strict venue semantics.
"""
from .position_values import position_identity


USDT_CEX = frozenset(('BINANCE', 'OKX', 'GATE', 'BYBIT', 'BITGET'))


def cash_identity(row, expected=None):
    identity = position_identity('crossex', row['symbol'])
    exchange = row['exchange_type']
    if exchange == 'CROSSEX':
        if (identity['exchange_type'] not in USDT_CEX or identity['quote'] != 'USDT'
                or row['coin'] != 'USDT'):
            raise ValueError('cash_identity')
        exchange = identity['exchange_type']
    # Check all other explicit identity fields too; never mutate the raw row or
    # change the asset-domain key used by cash balance continuity.
    checked = position_identity('crossex', row['symbol'], dict(row, exchange_type=exchange))
    if row['coin'] != checked['quote'] or (expected is not None and checked != expected):
        raise ValueError('cash_identity')
    return checked


def program_contract_identity(instrument):
    identity = position_identity('crossex', instrument['symbol'], dict(
        exchange_type=instrument['market']['exchange'].upper(),
        business_type=instrument['market']['market_type'].upper(), quote=instrument['spec']['quote']))
    if identity['quote'] != 'USDT':
        raise ValueError('program_cash_identity')
    return identity
